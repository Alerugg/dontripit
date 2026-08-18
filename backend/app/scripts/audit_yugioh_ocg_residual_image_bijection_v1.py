from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import psycopg2
from psycopg2.extras import RealDictCursor

from app.scripts.audit_yugioh_agov_jp_image_match_pilot_v1 import (
    IMAGE_BASE,
    _assignment_rankings,
    _download,
    _feature,
    _hamming,
    _pair_signature,
    _pixel_mae,
)


ACCEPTED = ("accepted", "mapped", "exact")
METRICS = ("pixel_mae", "dhash_distance", "ahash_distance")
MIN_GAP = 0.03


def _connect():
    url = os.getenv("DATABASE_URL_UNPOOLED") or os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL_UNPOOLED or DATABASE_URL is required")
    conn = psycopg2.connect(url, connect_timeout=30, application_name="dontripit_ygo_ocg_residual_image_bijection_v1")
    conn.set_session(readonly=True, autocommit=False)
    return conn


def main() -> int:
    parser = argparse.ArgumentParser(description="READ ONLY certify one residual Yu-Gi-Oh OCG physical-variant group by first-party images")
    parser.add_argument("--id-expansion", required=True)
    parser.add_argument("--expansion-code", required=True)
    parser.add_argument("--set-code", required=True)
    parser.add_argument("--card-name", required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()

    conn = _connect()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT id FROM games WHERE slug='yugioh' LIMIT 1")
            game_id = int(cur.fetchone()["id"])
            cur.execute("SELECT max(last_seen_at) capture FROM external_catalog_products WHERE source='cardmarket'")
            capture = cur.fetchone()["capture"]

            cur.execute(
                """SELECT e.id external_product_id,e.external_id id_product,e.name,e.category_id,e.metacard_external_id
                   FROM external_catalog_products e
                   WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single'
                     AND e.expansion_external_id=%s AND e.last_seen_at=%s AND e.name=%s
                   ORDER BY e.external_id::bigint""",
                (game_id, args.id_expansion, capture, args.card_name),
            )
            all_products = [dict(r) for r in cur.fetchall()]

            cur.execute(
                """SELECT p.id print_id,p.card_id,p.collector_number,p.rarity,p.variant,p.language,c.name card_name,
                          pi.url image_url,pi.source image_source
                   FROM prints p JOIN cards c ON c.id=p.card_id JOIN sets s ON s.id=p.set_id
                   LEFT JOIN LATERAL (
                     SELECT x.url,x.source FROM print_images x
                     WHERE x.print_id=p.id ORDER BY x.is_primary DESC,x.id ASC LIMIT 1
                   ) pi ON true
                   WHERE c.game_id=%s AND c.name=%s AND lower(coalesce(p.language,''))='ja'
                     AND upper(coalesce(s.code,''))=%s
                   ORDER BY p.id""",
                (game_id, args.card_name, args.set_code.upper()),
            )
            all_prints = [dict(r) for r in cur.fetchall()]

            product_row_ids = [int(r["external_product_id"]) for r in all_products]
            print_ids_all = [int(r["print_id"]) for r in all_prints]
            if product_row_ids or print_ids_all:
                cur.execute(
                    """SELECT l.external_product_id,l.print_id,e.external_id id_product
                       FROM external_catalog_print_links l
                       JOIN external_catalog_products e ON e.id=l.external_product_id
                       WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single'
                         AND l.link_status=ANY(%s)
                         AND (l.external_product_id=ANY(%s) OR l.print_id=ANY(%s))""",
                    (game_id, list(ACCEPTED), product_row_ids or [-1], print_ids_all or [-1]),
                )
                claims = [dict(r) for r in cur.fetchall()]
            else:
                claims = []
            conn.rollback()
    finally:
        conn.close()

    claimed_products = {int(r["external_product_id"]) for r in claims}
    claimed_prints = {int(r["print_id"]) for r in claims}
    products = [r for r in all_products if int(r["external_product_id"]) not in claimed_products]
    prints = [r for r in all_prints if int(r["print_id"]) not in claimed_prints]

    if not products or len(products) != len(prints) or len(products) < 2:
        raise RuntimeError(
            {
                "residual_surface_not_balanced_multivariant": args.card_name,
                "products": len(products),
                "prints": len(prints),
                "claims": claims,
            }
        )
    metas = {str(r.get("metacard_external_id") or "") for r in products}
    if len(metas) != 1 or "" in metas:
        raise RuntimeError({"residual_products_do_not_share_one_nonempty_metacard": sorted(metas)})
    if any(not r.get("image_url") for r in prints):
        raise RuntimeError({"canonical_image_missing": [int(r["print_id"]) for r in prints if not r.get("image_url")]})

    product_features = {}
    product_downloads = {}
    for row in products:
        pid = str(row["id_product"])
        category_id = str(row.get("category_id") or "5")
        url = f"{IMAGE_BASE}/{category_id}/{args.expansion_code}/{pid}/{pid}.jpg"
        body, meta = _download(url, referer="https://www.cardmarket.com/")
        product_downloads[pid] = {"url": url, **meta}
        if body:
            try:
                product_features[pid] = _feature(body)
            except Exception as exc:
                product_downloads[pid]["decode_error"] = f"{type(exc).__name__}: {exc}"

    print_features = {}
    canonical_downloads = {}
    for row in prints:
        print_id = int(row["print_id"])
        url = str(row["image_url"])
        body, meta = _download(url)
        canonical_downloads[str(print_id)] = {"url": url, "source": row.get("image_source"), **meta}
        if body:
            try:
                print_features[print_id] = _feature(body)
            except Exception as exc:
                canonical_downloads[str(print_id)]["decode_error"] = f"{type(exc).__name__}: {exc}"

    complete = len(product_features) == len(products) and len(print_features) == len(prints)
    matrix = []
    if complete:
        for product in products:
            pid = str(product["id_product"])
            pf = product_features[pid]
            for print_row in prints:
                print_id = int(print_row["print_id"])
                cf = print_features[print_id]
                matrix.append(
                    {
                        "idProduct": pid,
                        "print_id": print_id,
                        "canonical_variant": print_row["variant"],
                        "canonical_rarity": print_row["rarity"],
                        "ahash_distance": _hamming(pf["ahash"], cf["ahash"]),
                        "dhash_distance": _hamming(pf["dhash"], cf["dhash"]),
                        "pixel_mae": round(_pixel_mae(pf["image"], cf["image"]), 4),
                    }
                )

    product_ids = sorted(product_features, key=int)
    print_ids = sorted(print_features)
    assignments = {}
    signatures = []
    for metric in METRICS:
        rankings = _assignment_rankings(matrix, product_ids, print_ids, metric) if complete else []
        best = rankings[0] if rankings else None
        second = rankings[1] if len(rankings) > 1 else None
        if best:
            signatures.append(_pair_signature(best["pairs"]))
        assignments[metric] = {
            "best": best,
            "second": second,
            "absolute_gap": round(second["score"] - best["score"], 4) if best and second else None,
            "relative_gap": round((second["score"] - best["score"]) / best["score"], 6) if best and second and best["score"] else None,
        }

    consensus = complete and len(signatures) == len(METRICS) and len(set(signatures)) == 1
    min_gap = min((v["relative_gap"] for v in assignments.values() if v.get("relative_gap") is not None), default=None)
    candidate = bool(consensus and min_gap is not None and min_gap >= MIN_GAP)

    lookup = {(str(r["idProduct"]), int(r["print_id"])): r for r in matrix}
    consensus_pairs = []
    if consensus and assignments["pixel_mae"]["best"]:
        product_meta = {str(r["id_product"]): r for r in products}
        print_meta = {int(r["print_id"]): r for r in prints}
        for pair in assignments["pixel_mae"]["best"]["pairs"]:
            pid = str(pair["idProduct"])
            print_id = int(pair["print_id"])
            distance = lookup[(pid, print_id)]
            pm = product_meta[pid]
            cm = print_meta[print_id]
            consensus_pairs.append(
                {
                    "idProduct": pid,
                    "external_product_id": int(pm["external_product_id"]),
                    "idMetacard": str(pm["metacard_external_id"]),
                    "print_id": print_id,
                    "card_id": int(cm["card_id"]),
                    "card_name": cm["card_name"],
                    "collector_number": cm["collector_number"],
                    "canonical_variant": cm["variant"],
                    "canonical_rarity": cm["rarity"],
                    "product_image_sha256": product_downloads[pid].get("sha256"),
                    "canonical_image_sha256": canonical_downloads[str(print_id)].get("sha256"),
                    "matched_distances": {
                        "pixel_mae": distance["pixel_mae"],
                        "dhash_distance": distance["dhash_distance"],
                        "ahash_distance": distance["ahash_distance"],
                    },
                }
            )

    report = {
        "status": "pass",
        "production_writes": 0,
        "cardmarket_capture": str(capture),
        "certified_region": {
            "idExpansion": args.id_expansion,
            "expansion_code": args.expansion_code,
            "canonical_set": args.set_code.upper(),
        },
        "card_name": args.card_name,
        "idMetacard": next(iter(metas)),
        "residual_products": len(products),
        "residual_prints": len(prints),
        "complete_surface": complete,
        "product_downloads": product_downloads,
        "canonical_downloads": canonical_downloads,
        "matrix": matrix,
        "assignment_by_metric": assignments,
        "assignment_consensus": consensus,
        "minimum_relative_assignment_gap": min_gap,
        "threshold": MIN_GAP,
        "image_bijection_candidate": candidate,
        "consensus_pairs": consensus_pairs,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n"
    args.report.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
