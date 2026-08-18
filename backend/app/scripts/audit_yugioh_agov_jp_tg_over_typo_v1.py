from __future__ import annotations

import difflib
import json
import os
from pathlib import Path

import psycopg2
from psycopg2.extras import RealDictCursor

from app.scripts.audit_yugioh_agov_jp_image_match_pilot_v1 import (
    EXPANSION_CODE,
    EXPANSION_ID,
    IMAGE_BASE,
    SET_CODE,
    _assignment_rankings,
    _download,
    _feature,
    _hamming,
    _pair_signature,
    _pixel_mae,
)


GAME = "yugioh"
CANONICAL_CARD_NAME = "T.G. Over Dragonar"
EXPECTED_PRODUCTS = {"724145", "724146", "724148"}
ACCEPTED = ("accepted", "mapped", "exact")


def _connect():
    url = os.getenv("DATABASE_URL_UNPOOLED") or os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL_UNPOOLED or DATABASE_URL is required")
    conn = psycopg2.connect(url, connect_timeout=30, application_name="dontripit_ygo_agov_jp_tg_over_typo_v1")
    conn.set_session(readonly=True, autocommit=False)
    return conn


def main() -> int:
    conn = _connect()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT id FROM games WHERE slug=%s LIMIT 1", (GAME,))
            game_id = int(cur.fetchone()["id"])
            cur.execute("SELECT max(last_seen_at) AS ts FROM external_catalog_products WHERE source='cardmarket'")
            capture = cur.fetchone()["ts"]

            cur.execute(
                """
                SELECT e.id AS external_product_id,e.external_id AS id_product,e.name,e.category_id,e.metacard_external_id
                FROM external_catalog_products e
                WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single'
                  AND e.expansion_external_id=%s AND e.last_seen_at=%s
                  AND e.external_id=ANY(%s)
                ORDER BY e.external_id::bigint
                """,
                (game_id, EXPANSION_ID, capture, sorted(EXPECTED_PRODUCTS)),
            )
            products = [dict(r) for r in cur.fetchall()]

            cur.execute(
                """
                SELECT p.id AS print_id,p.card_id,p.collector_number,p.rarity,p.variant,p.language,c.name AS card_name,
                       pi.url AS image_url,pi.source AS image_source
                FROM prints p JOIN cards c ON c.id=p.card_id JOIN sets s ON s.id=p.set_id
                LEFT JOIN LATERAL (
                    SELECT x.url,x.source FROM print_images x
                    WHERE x.print_id=p.id ORDER BY x.is_primary DESC,x.id ASC LIMIT 1
                ) pi ON true
                WHERE c.game_id=%s AND c.name=%s AND lower(coalesce(p.language,''))='ja'
                  AND upper(coalesce(s.code,''))=%s
                ORDER BY p.id
                """,
                (game_id, CANONICAL_CARD_NAME, SET_CODE),
            )
            prints = [dict(r) for r in cur.fetchall()]

            cur.execute(
                """
                SELECT e.external_id AS id_product,l.print_id
                FROM external_catalog_print_links l JOIN external_catalog_products e ON e.id=l.external_product_id
                WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single'
                  AND l.link_status=ANY(%s) AND (e.external_id=ANY(%s) OR l.print_id=ANY(%s))
                """,
                (game_id, list(ACCEPTED), sorted(EXPECTED_PRODUCTS), [int(r["print_id"]) for r in prints]),
            )
            claims = [dict(r) for r in cur.fetchall()]
            conn.rollback()
    finally:
        conn.close()

    if {str(r["id_product"]) for r in products} != EXPECTED_PRODUCTS:
        raise RuntimeError({"expected_products": sorted(EXPECTED_PRODUCTS), "actual": [str(r["id_product"]) for r in products]})
    if len(prints) != 3:
        raise RuntimeError({"expected_three_canonical_prints": len(prints)})
    if claims:
        raise RuntimeError({"group_not_unclaimed": claims})

    product_features = {}
    product_downloads = {}
    for row in products:
        pid = str(row["id_product"])
        category_id = str(row.get("category_id") or "5")
        url = f"{IMAGE_BASE}/{category_id}/{EXPANSION_CODE}/{pid}/{pid}.jpg"
        body, meta = _download(url, referer="https://www.cardmarket.com/")
        product_downloads[pid] = {"url": url, **meta}
        if body:
            product_features[pid] = _feature(body)

    print_features = {}
    canonical_downloads = {}
    for row in prints:
        print_id = int(row["print_id"])
        url = str(row.get("image_url") or "")
        if not url:
            canonical_downloads[str(print_id)] = {"error": "missing canonical image"}
            continue
        body, meta = _download(url)
        canonical_downloads[str(print_id)] = {"url": url, "source": row.get("image_source"), **meta}
        if body:
            print_features[print_id] = _feature(body)

    matrix = []
    for product in products:
        pid = str(product["id_product"])
        pf = product_features.get(pid)
        if not pf:
            continue
        for print_row in prints:
            print_id = int(print_row["print_id"])
            cf = print_features.get(print_id)
            if not cf:
                continue
            matrix.append(
                {
                    "idProduct": pid,
                    "market_name": product["name"],
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
    metrics = ("pixel_mae", "dhash_distance", "ahash_distance")
    assignments = {}
    signatures = []
    for metric in metrics:
        rankings = _assignment_rankings(matrix, product_ids, print_ids, metric)
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

    complete = len(product_features) == 3 and len(print_features) == 3
    consensus = complete and len(signatures) == 3 and len(set(signatures)) == 1
    min_gap = min((v["relative_gap"] for v in assignments.values() if v["relative_gap"] is not None), default=None)
    candidate = bool(consensus and min_gap is not None and min_gap >= 0.03)

    lookup = {(str(r["idProduct"]), int(r["print_id"])): r for r in matrix}
    pairs = []
    if consensus and assignments["pixel_mae"]["best"]:
        for pair in assignments["pixel_mae"]["best"]["pairs"]:
            row = lookup[(str(pair["idProduct"]), int(pair["print_id"]))]
            pairs.append(
                {
                    "idProduct": str(pair["idProduct"]),
                    "market_name": row["market_name"],
                    "print_id": int(pair["print_id"]),
                    "canonical_variant": row["canonical_variant"],
                    "canonical_rarity": row["canonical_rarity"],
                    "product_image_sha256": product_downloads[str(pair["idProduct"])]["sha256"],
                    "canonical_image_sha256": canonical_downloads[str(pair["print_id"])]["sha256"],
                }
            )

    report = {
        "status": "pass",
        "production_writes": 0,
        "cardmarket_capture": str(capture),
        "canonical_card_name": CANONICAL_CARD_NAME,
        "products": [
            {
                "idProduct": str(r["id_product"]),
                "market_name": r["name"],
                "idMetacard": str(r.get("metacard_external_id") or ""),
                "name_similarity_to_canonical": round(difflib.SequenceMatcher(None, str(r["name"]).casefold(), CANONICAL_CARD_NAME.casefold()).ratio(), 4),
            }
            for r in products
        ],
        "prints": [
            {
                "print_id": int(r["print_id"]),
                "collector_number": r["collector_number"],
                "variant": r["variant"],
                "rarity": r["rarity"],
            }
            for r in prints
        ],
        "complete_surface": complete,
        "assignment_by_metric": assignments,
        "assignment_consensus": consensus,
        "minimum_relative_assignment_gap": min_gap,
        "image_bijection_candidate": candidate,
        "consensus_pairs": pairs,
        "product_downloads": product_downloads,
        "canonical_downloads": canonical_downloads,
        "matrix": matrix,
    }
    output = os.getenv("YGO_AGOV_JP_TG_OVER_TYPO_OUTPUT", "/tmp/yugioh-agov-jp-tg-over-typo-v1.json")
    Path(output).write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
