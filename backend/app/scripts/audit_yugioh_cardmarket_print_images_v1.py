from __future__ import annotations

import argparse
import csv
import json
import os
from collections import Counter, defaultdict
from pathlib import Path

import psycopg2
from psycopg2.extras import RealDictCursor

ACCEPTED = ("accepted", "mapped", "exact")


def _connect():
    url = os.getenv("DATABASE_URL_UNPOOLED") or os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL_UNPOOLED or DATABASE_URL is required")
    conn = psycopg2.connect(
        url,
        connect_timeout=30,
        application_name="dontripit_ygo_cardmarket_print_images_v1",
    )
    conn.set_session(readonly=True, autocommit=False)
    return conn


def _counter(rows: list[dict], key: str) -> dict[str, int]:
    return dict(sorted(Counter(str(row.get(key) or "unknown") for row in rows).items()))


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "READ ONLY inventory missing Yu-Gi-Oh PrintImages that already have a current, "
            "reviewed, exact, one-to-one Cardmarket physical product identity"
        )
    )
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--csv", type=Path, required=True)
    parser.add_argument("--sample-limit", type=int, default=100)
    args = parser.parse_args()

    conn = _connect()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT id FROM games WHERE slug='yugioh' LIMIT 1")
            game = cur.fetchone()
            if not game:
                raise RuntimeError("Yu-Gi-Oh game row missing")
            game_id = int(game["id"])

            cur.execute(
                "SELECT max(last_seen_at) AS capture FROM external_catalog_products WHERE source='cardmarket'"
            )
            capture = cur.fetchone()["capture"]
            if capture is None:
                raise RuntimeError("Current Cardmarket capture missing")

            cur.execute(
                """
                SELECT p.id AS print_id,p.card_id,p.collector_number,p.language,p.rarity,p.variant,
                       p.is_foil,p.print_key,p.yugioh_id,c.name AS card_name,
                       s.id AS set_id,s.code AS set_code,s.name AS set_name,s.region AS set_region
                FROM prints p
                JOIN cards c ON c.id=p.card_id
                JOIN sets s ON s.id=p.set_id
                WHERE c.game_id=%s
                  AND NOT EXISTS (SELECT 1 FROM print_images pi WHERE pi.print_id=p.id)
                ORDER BY lower(coalesce(p.language,'')), upper(s.code), p.collector_number, p.id
                """,
                (game_id,),
            )
            missing = [dict(row) for row in cur.fetchall()]

            cur.execute(
                """
                WITH claims AS (
                    SELECT l.id AS link_id,l.external_product_id,l.print_id,l.link_status,l.mapping_method,
                           l.confidence,l.reviewed,
                           count(*) OVER (PARTITION BY l.external_product_id) AS accepted_claims_for_product,
                           count(*) OVER (PARTITION BY l.print_id) AS accepted_claims_for_print
                    FROM external_catalog_print_links l
                    JOIN external_catalog_products ce ON ce.id=l.external_product_id
                    WHERE ce.source='cardmarket' AND ce.game_id=%s AND ce.product_group='single'
                      AND l.link_status=ANY(%s)
                )
                SELECT p.id AS print_id,p.card_id,p.collector_number,p.language,p.rarity,p.variant,
                       p.is_foil,p.print_key,p.yugioh_id,c.name AS card_name,
                       s.id AS set_id,s.code AS set_code,s.name AS set_name,s.region AS set_region,
                       e.id AS external_product_row_id,e.external_id AS id_product,e.name AS product_name,
                       e.category_id,e.expansion_external_id,e.metacard_external_id,e.last_seen_at,
                       cl.link_id,cl.link_status,cl.mapping_method,cl.confidence,cl.reviewed,
                       cl.accepted_claims_for_product,cl.accepted_claims_for_print
                FROM claims cl
                JOIN external_catalog_products e ON e.id=cl.external_product_id
                JOIN prints p ON p.id=cl.print_id
                JOIN cards c ON c.id=p.card_id
                JOIN sets s ON s.id=p.set_id
                WHERE e.last_seen_at=%s
                  AND cl.confidence='exact' AND cl.reviewed IS TRUE
                  AND cl.accepted_claims_for_product=1 AND cl.accepted_claims_for_print=1
                  AND c.game_id=%s
                  AND NOT EXISTS (SELECT 1 FROM print_images pi WHERE pi.print_id=p.id)
                ORDER BY lower(coalesce(p.language,'')), upper(s.code), p.collector_number, p.id
                """,
                (game_id, list(ACCEPTED), capture, game_id),
            )
            eligible = [dict(row) for row in cur.fetchall()]

            cur.execute(
                """
                WITH claims AS (
                    SELECT l.external_product_id,l.print_id,
                           count(*) OVER (PARTITION BY l.external_product_id) AS product_claims,
                           count(*) OVER (PARTITION BY l.print_id) AS print_claims
                    FROM external_catalog_print_links l
                    JOIN external_catalog_products e ON e.id=l.external_product_id
                    WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single'
                      AND l.link_status=ANY(%s)
                )
                SELECT
                  count(*) FILTER (WHERE confidence='exact' AND reviewed IS TRUE) AS exact_reviewed_current_missing,
                  count(*) FILTER (WHERE confidence='exact' AND reviewed IS TRUE AND product_claims=1 AND print_claims=1)
                    AS exact_reviewed_one_to_one_current_missing,
                  count(*) FILTER (WHERE confidence='exact' AND reviewed IS TRUE AND (product_claims<>1 OR print_claims<>1))
                    AS exact_reviewed_identity_conflicts
                FROM claims cl
                JOIN external_catalog_print_links l
                  ON l.external_product_id=cl.external_product_id AND l.print_id=cl.print_id
                JOIN external_catalog_products e ON e.id=cl.external_product_id
                JOIN prints p ON p.id=cl.print_id
                JOIN cards c ON c.id=p.card_id
                WHERE e.last_seen_at=%s AND c.game_id=%s
                  AND NOT EXISTS (SELECT 1 FROM print_images pi WHERE pi.print_id=p.id)
                """,
                (game_id, list(ACCEPTED), capture, game_id),
            )
            claim_summary = dict(cur.fetchone() or {})
            conn.rollback()
    finally:
        conn.close()

    grouped: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for row in eligible:
        lang = str(row.get("language") or "unknown").lower()
        grouping = "|".join(
            [
                lang,
                str(row.get("set_code") or ""),
                str(row.get("expansion_external_id") or ""),
                str(row.get("category_id") or ""),
            ]
        )
        grouped[lang][grouping] += 1

    args.csv.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "print_id","card_id","card_name","set_id","set_code","set_name","set_region",
        "collector_number","language","rarity","variant","is_foil","print_key","yugioh_id",
        "external_product_row_id","id_product","product_name","category_id","expansion_external_id",
        "metacard_external_id","last_seen_at","link_id","link_status","mapping_method","confidence","reviewed",
        "accepted_claims_for_product","accepted_claims_for_print",
    ]
    with args.csv.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in eligible:
            writer.writerow(row)

    report = {
        "status": "pass",
        "production_writes": 0,
        "cardmarket_capture": str(capture),
        "missing_print_images_total": len(missing),
        "missing_by_language": _counter(missing, "language"),
        "missing_by_set_region": _counter(missing, "set_region"),
        "exact_reviewed_current_missing": int(claim_summary.get("exact_reviewed_current_missing") or 0),
        "exact_reviewed_one_to_one_current_missing": int(
            claim_summary.get("exact_reviewed_one_to_one_current_missing") or 0
        ),
        "exact_reviewed_identity_conflicts": int(claim_summary.get("exact_reviewed_identity_conflicts") or 0),
        "eligible_one_to_one": len(eligible),
        "eligible_by_language": _counter(eligible, "language"),
        "eligible_by_set_region": _counter(eligible, "set_region"),
        "eligible_grouped_language_set_expansion_category": {
            lang: dict(sorted(groups.items(), key=lambda item: (-item[1], item[0])))
            for lang, groups in sorted(grouped.items())
        },
        "samples": eligible[: max(0, args.sample_limit)],
    }
    if report["eligible_one_to_one"] != report["exact_reviewed_one_to_one_current_missing"]:
        raise RuntimeError({"eligible_accounting_drift": report})

    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
