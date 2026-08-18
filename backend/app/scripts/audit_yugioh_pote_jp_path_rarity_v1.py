from __future__ import annotations

import json
import os
import re
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import unquote, urlsplit

import psycopg2
from psycopg2.extras import RealDictCursor


GAME = "yugioh"
EXPANSION_ID = "5044"
EXPANSION_CODE = "POTE-JP"
SET_CODE = "POTE"
LANGUAGE = "ja"
ACCEPTED = ("accepted", "mapped", "exact")


def _connect():
    url = os.getenv("DATABASE_URL_UNPOOLED") or os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL_UNPOOLED or DATABASE_URL is required")
    conn = psycopg2.connect(
        url,
        connect_timeout=30,
        application_name="dontripit_ygo_pote_jp_path_rarity_v1",
    )
    conn.set_session(readonly=True, autocommit=False)
    return conn


def _path_version_label(value: str | None) -> dict:
    raw = str(value or "").strip()
    if not raw:
        return {"version": None, "label": None, "slug": None}
    parsed = urlsplit(raw)
    path = parsed.path or raw.split("?", 1)[0].split("#", 1)[0]
    slug = unquote(path.rstrip("/").rsplit("/", 1)[-1])
    match = re.search(r"-V(\d+)-(.+)$", slug, flags=re.IGNORECASE)
    if not match:
        return {"version": None, "label": None, "slug": slug}
    label = re.sub(r"[-_]+", " ", match.group(2)).strip().casefold()
    label = re.sub(r"\s+", " ", label)
    return {"version": int(match.group(1)), "label": label or None, "slug": slug}


def main() -> int:
    conn = _connect()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT id FROM games WHERE slug=%s LIMIT 1", (GAME,))
            row = cur.fetchone()
            if not row:
                raise RuntimeError("Yu-Gi-Oh game row missing")
            game_id = int(row["id"])

            cur.execute("SELECT max(last_seen_at) capture FROM external_catalog_products WHERE source='cardmarket'")
            capture = cur.fetchone()["capture"]
            if capture is None:
                raise RuntimeError("Current Cardmarket catalog capture missing")

            cur.execute(
                """
                SELECT e.id external_product_id,e.external_id id_product,e.name,e.metacard_external_id,
                       e.website_path,e.last_seen_at
                FROM external_catalog_products e
                WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single'
                  AND e.expansion_external_id=%s AND e.last_seen_at=%s
                ORDER BY e.external_id::bigint
                """,
                (game_id, EXPANSION_ID, capture),
            )
            products = [dict(r) for r in cur.fetchall()]

            cur.execute(
                """
                SELECT p.id print_id,p.card_id,p.collector_number,p.rarity,p.variant,p.language,c.name card_name
                FROM prints p
                JOIN cards c ON c.id=p.card_id
                JOIN sets s ON s.id=p.set_id
                WHERE c.game_id=%s AND lower(coalesce(p.language,''))=%s
                  AND upper(coalesce(s.code,''))=%s
                ORDER BY p.id
                """,
                (game_id, LANGUAGE, SET_CODE),
            )
            prints = [dict(r) for r in cur.fetchall()]

            cur.execute(
                """
                SELECT l.external_product_id,l.print_id,l.mapping_method,l.confidence,l.reviewed,
                       e.external_id id_product,e.metacard_external_id,e.website_path,
                       p.card_id,p.collector_number,p.rarity,p.variant,c.name card_name,s.code set_code,p.language
                FROM external_catalog_print_links l
                JOIN external_catalog_products e ON e.id=l.external_product_id
                JOIN prints p ON p.id=l.print_id
                JOIN cards c ON c.id=p.card_id
                JOIN sets s ON s.id=p.set_id
                WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single'
                  AND e.expansion_external_id=%s AND e.last_seen_at=%s
                  AND l.link_status=ANY(%s)
                ORDER BY e.external_id::bigint
                """,
                (game_id, EXPANSION_ID, capture, list(ACCEPTED)),
            )
            accepted_pote = [dict(r) for r in cur.fetchall()]

            cur.execute(
                """
                SELECT l.external_product_id,l.print_id,e.external_id id_product,e.metacard_external_id,
                       e.expansion_external_id,p.card_id
                FROM external_catalog_print_links l
                JOIN external_catalog_products e ON e.id=l.external_product_id
                JOIN prints p ON p.id=l.print_id
                WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single'
                  AND l.link_status=ANY(%s)
                """,
                (game_id, list(ACCEPTED)),
            )
            accepted_global = [dict(r) for r in cur.fetchall()]
            conn.rollback()
    finally:
        conn.close()

    claimed_product_rows = {int(r["external_product_id"]) for r in accepted_global}
    claimed_print_ids = {int(r["print_id"]) for r in accepted_global}
    residual_products = [r for r in products if int(r["external_product_id"]) not in claimed_product_rows]
    residual_prints = [r for r in prints if int(r["print_id"]) not in claimed_print_ids]

    calibration = defaultdict(Counter)
    calibration_examples = defaultdict(list)
    accepted_unparsed = []
    for row in accepted_pote:
        parsed = _path_version_label(row.get("website_path"))
        label = parsed["label"]
        rarity = str(row.get("rarity") or "").casefold()
        if not label:
            accepted_unparsed.append(
                {
                    "idProduct": str(row["id_product"]),
                    "website_path": row.get("website_path"),
                    "rarity": rarity,
                    "variant": row.get("variant"),
                }
            )
            continue
        calibration[label][rarity] += 1
        if len(calibration_examples[label]) < 8:
            calibration_examples[label].append(
                {
                    "idProduct": str(row["id_product"]),
                    "version": parsed["version"],
                    "rarity": rarity,
                    "variant": row.get("variant"),
                    "slug": parsed["slug"],
                }
            )

    exact_label_to_rarity = {}
    calibration_report = {}
    for label in sorted(calibration):
        counts = dict(sorted(calibration[label].items()))
        calibration_report[label] = {
            "canonical_rarity_counts": counts,
            "accepted_evidence": sum(counts.values()),
            "unambiguous": len(counts) == 1,
            "examples": calibration_examples[label],
        }
        if len(counts) == 1:
            exact_label_to_rarity[label] = next(iter(counts))

    meta_to_cards = defaultdict(set)
    for row in accepted_global:
        meta = str(row.get("metacard_external_id") or "")
        if meta:
            meta_to_cards[meta].add(int(row["card_id"]))

    prints_by_card_rarity = defaultdict(list)
    for row in residual_prints:
        key = (int(row["card_id"]), str(row.get("rarity") or "").casefold())
        prints_by_card_rarity[key].append(row)

    proposal = []
    unresolved = []
    for product in residual_products:
        pid = str(product["id_product"])
        meta = str(product.get("metacard_external_id") or "")
        parsed = _path_version_label(product.get("website_path"))
        label = parsed["label"]
        if not label:
            unresolved.append(
                {
                    "idProduct": pid,
                    "reason": "website_path_version_label_unparsed",
                    "website_path": product.get("website_path"),
                    "slug": parsed["slug"],
                }
            )
            continue
        rarity = exact_label_to_rarity.get(label)
        if rarity is None:
            unresolved.append(
                {
                    "idProduct": pid,
                    "reason": "path_label_not_unambiguously_calibrated",
                    "path_label": label,
                    "version": parsed["version"],
                    "calibration": calibration_report.get(label),
                }
            )
            continue
        cards = sorted(meta_to_cards.get(meta, set()))
        if len(cards) != 1:
            unresolved.append(
                {
                    "idProduct": pid,
                    "reason": "metacard_not_exactly_one_canonical_card",
                    "idMetacard": meta or None,
                    "canonical_card_ids": cards,
                    "path_label": label,
                    "calibrated_rarity": rarity,
                }
            )
            continue
        card_id = cards[0]
        candidates = prints_by_card_rarity.get((card_id, rarity), [])
        if len(candidates) != 1:
            unresolved.append(
                {
                    "idProduct": pid,
                    "reason": "calibrated_rarity_not_exactly_one_residual_print",
                    "idMetacard": meta,
                    "card_id": card_id,
                    "path_label": label,
                    "calibrated_rarity": rarity,
                    "candidate_print_ids": [int(r["print_id"]) for r in candidates],
                }
            )
            continue
        print_row = candidates[0]
        proposal.append(
            {
                "idProduct": pid,
                "external_product_id": int(product["external_product_id"]),
                "idMetacard": meta,
                "website_path": product.get("website_path"),
                "version": parsed["version"],
                "path_label": label,
                "calibrated_rarity": rarity,
                "calibration_evidence_count": calibration_report[label]["accepted_evidence"],
                "print_id": int(print_row["print_id"]),
                "card_id": int(print_row["card_id"]),
                "card_name": print_row["card_name"],
                "collector_number": print_row["collector_number"],
                "canonical_rarity": print_row["rarity"],
                "canonical_variant": print_row["variant"],
            }
        )

    proposal_product_ids = [str(r["idProduct"]) for r in proposal]
    proposal_print_ids = [int(r["print_id"]) for r in proposal]
    duplicate_products = sorted(k for k, n in Counter(proposal_product_ids).items() if n > 1)
    duplicate_prints = sorted(k for k, n in Counter(proposal_print_ids).items() if n > 1)

    residual_label_counts = Counter()
    residual_unparsed_paths = []
    for row in residual_products:
        parsed = _path_version_label(row.get("website_path"))
        if parsed["label"]:
            residual_label_counts[parsed["label"]] += 1
        else:
            residual_unparsed_paths.append(
                {"idProduct": str(row["id_product"]), "website_path": row.get("website_path"), "slug": parsed["slug"]}
            )

    failures = []
    if duplicate_products:
        failures.append(f"proposal_duplicate_products_{len(duplicate_products)}")
    if duplicate_prints:
        failures.append(f"proposal_duplicate_prints_{len(duplicate_prints)}")

    report = {
        "status": "pass" if not failures else "fail",
        "production_writes": 0,
        "cardmarket_capture": str(capture),
        "certified_region": {
            "idExpansion": EXPANSION_ID,
            "expansion_code": EXPANSION_CODE,
            "canonical_set": SET_CODE,
            "language": LANGUAGE,
        },
        "surface": {
            "regional_products": len(products),
            "canonical_ja_prints": len(prints),
            "accepted_links": len(accepted_pote),
            "residual_products": len(residual_products),
            "residual_prints": len(residual_prints),
        },
        "accepted_path_calibration": calibration_report,
        "accepted_unparsed": accepted_unparsed,
        "exact_label_to_canonical_rarity": exact_label_to_rarity,
        "residual_path_label_counts": dict(sorted(residual_label_counts.items())),
        "residual_unparsed_paths": residual_unparsed_paths,
        "proposed_exact_links": len(proposal),
        "proposal": proposal,
        "unresolved": unresolved,
        "proposal_duplicate_products": duplicate_products,
        "proposal_duplicate_prints": duplicate_prints,
        "failures": failures,
    }
    output = os.getenv("YGO_POTE_JP_PATH_RARITY_OUTPUT", "/tmp/yugioh-pote-jp-path-rarity-v1.json")
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n"
    Path(output).write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if not failures else 2


if __name__ == "__main__":
    raise SystemExit(main())
