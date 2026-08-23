from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import psycopg2
from psycopg2.extras import RealDictCursor


OUTPUT = Path(
    os.getenv(
        "ONEPIECE_DON_AUDIT_OUTPUT",
        "artifacts/onepiece-don-structured-v1-audit.json",
    )
)

TERMS = (
    "%don!!%",
    "%don card%",
    "%ドン!!%",
    "%ドンカード%",
    "%st-01%",
    "%championship 2023%",
    "%大阪大会%",
    "%prod.ww.guan.jp/ps%",
    "%premier event inc%",
    "%bushiroad%",
)


def _fetchall(cur, sql: str, params=()):
    cur.execute(sql, params)
    return [dict(row) for row in cur.fetchall()]


def _write_report(report: dict) -> None:
    report["counts"] = {
        key: len(value)
        for key, value in report.get("queries", {}).items()
        if isinstance(value, list)
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    url = (os.getenv("DATABASE_URL_UNPOOLED") or os.getenv("DATABASE_URL") or "").strip()
    if not url:
        raise RuntimeError("DATABASE_URL_UNPOOLED or DATABASE_URL is required")

    conn = psycopg2.connect(url)
    conn.autocommit = False
    report: dict = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "transaction_read_only": None,
        "production_writes": 0,
        "queries": {},
        "notes": [
            "source_records search is deliberately bounded to recent rows from One Piece/Bushiroad/Premier sources; no production-wide raw_json full scan is allowed",
            "external Cardmarket rows are discovery evidence only and are never treated as canonical DON identities without a deterministic crosswalk",
        ],
    }
    exit_code = 0
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SHOW transaction_read_only")
            read_only = cur.fetchone()["transaction_read_only"]
            report["transaction_read_only"] = read_only == "on"
            if read_only != "on":
                raise RuntimeError("refusing DON audit without transaction_read_only=on")

            cur.execute("SET LOCAL statement_timeout = '12s'")

            games = _fetchall(
                cur,
                "SELECT id, slug, name FROM games WHERE slug = 'onepiece' ORDER BY id",
            )
            report["queries"]["game"] = games
            if len(games) != 1:
                raise AssertionError(f"expected exactly one onepiece game, got {len(games)}")
            game_id = games[0]["id"]

            report["queries"]["cards"] = _fetchall(
                cur,
                """
                SELECT id, name, card_key
                FROM cards
                WHERE game_id = %s
                  AND (
                    lower(name) LIKE '%%don%%'
                    OR name LIKE '%%ドン%%'
                    OR lower(coalesce(card_key, '')) LIKE '%%don%%'
                    OR lower(coalesce(card_key, '')) LIKE '%%st-01%%'
                  )
                ORDER BY id
                LIMIT 500
                """,
                (game_id,),
            )

            report["queries"]["prints"] = _fetchall(
                cur,
                """
                SELECT
                  p.id,
                  p.card_id,
                  c.name AS card_name,
                  c.card_key,
                  s.code AS set_code,
                  s.name AS set_name,
                  s.region,
                  p.collector_number,
                  p.language,
                  p.rarity,
                  p.variant,
                  p.print_key
                FROM prints p
                JOIN cards c ON c.id = p.card_id
                JOIN sets s ON s.id = p.set_id
                WHERE c.game_id = %s
                  AND (
                    lower(c.name) LIKE '%%don%%'
                    OR c.name LIKE '%%ドン%%'
                    OR lower(coalesce(c.card_key, '')) LIKE '%%don%%'
                    OR lower(p.collector_number) IN ('st-01', 'st01')
                    OR lower(coalesce(p.print_key, '')) LIKE '%%don%%'
                  )
                ORDER BY p.id
                LIMIT 1000
                """,
                (game_id,),
            )

            report["queries"]["identifiers"] = _fetchall(
                cur,
                """
                SELECT pi.id, pi.print_id, pi.source, pi.external_id
                FROM print_identifiers pi
                JOIN prints p ON p.id = pi.print_id
                JOIN cards c ON c.id = p.card_id
                WHERE c.game_id = %s
                  AND (
                    lower(pi.source) LIKE '%%don%%'
                    OR lower(pi.external_id) LIKE '%%don%%'
                    OR lower(pi.external_id) LIKE '%%st-01%%'
                    OR lower(pi.external_id) LIKE '%%st01%%'
                  )
                ORDER BY pi.id
                LIMIT 1000
                """,
                (game_id,),
            )

            report["queries"]["field_provenance"] = _fetchall(
                cur,
                """
                SELECT id, entity_type, entity_id, field_name, source, value_text, value_json
                FROM field_provenance
                WHERE lower(source) LIKE '%%onepiece%%'
                  AND (
                    lower(coalesce(value_text, '')) LIKE '%%don%%'
                    OR coalesce(value_text, '') LIKE '%%ドン%%'
                    OR lower(coalesce(value_json::text, '')) LIKE '%%don%%'
                    OR coalesce(value_json::text, '') LIKE '%%ドン%%'
                    OR lower(coalesce(value_text, '')) LIKE '%%st-01%%'
                    OR lower(coalesce(value_json::text, '')) LIKE '%%st-01%%'
                  )
                ORDER BY id
                LIMIT 1000
                """,
            )

            sources = _fetchall(
                cur,
                """
                SELECT id, name, description
                FROM sources
                WHERE lower(name) LIKE '%%onepiece%%'
                   OR lower(name) LIKE '%%one_piece%%'
                   OR lower(coalesce(description, '')) LIKE '%%one piece%%'
                   OR lower(name) LIKE '%%bushiroad%%'
                   OR lower(name) LIKE '%%premier%%'
                ORDER BY id
                """,
            )
            report["queries"]["sources"] = sources
            source_ids = [row["id"] for row in sources]

            if source_ids:
                source_predicate = " OR ".join(
                    ["lower(recent.raw_json::text) LIKE %s" for _ in TERMS]
                )
                report["queries"]["source_records_recent"] = _fetchall(
                    cur,
                    f"""
                    WITH recent AS MATERIALIZED (
                      SELECT sr.id, sr.source_id, sr.checksum, sr.raw_json, sr.ingested_at
                      FROM source_records sr
                      WHERE sr.source_id = ANY(%s)
                      ORDER BY sr.id DESC
                      LIMIT 5000
                    )
                    SELECT recent.id, recent.source_id, s.name AS source_name,
                           recent.checksum, recent.raw_json, recent.ingested_at
                    FROM recent
                    JOIN sources s ON s.id = recent.source_id
                    WHERE ({source_predicate})
                    ORDER BY recent.id DESC
                    LIMIT 250
                    """,
                    (source_ids, *tuple(term.lower() for term in TERMS)),
                )
            else:
                report["queries"]["source_records_recent"] = []

            report["queries"]["external_cardmarket_don_candidates"] = _fetchall(
                cur,
                """
                WITH latest AS (
                  SELECT max(last_seen_at) AS ts
                  FROM external_catalog_products
                  WHERE source = 'cardmarket' AND game_id = %s
                )
                SELECT
                  e.id,
                  e.external_id,
                  e.product_group,
                  e.name,
                  e.category,
                  e.expansion_external_id,
                  e.metacard_external_id,
                  e.website_path,
                  e.raw_json,
                  e.last_seen_at
                FROM external_catalog_products e
                CROSS JOIN latest
                WHERE e.source = 'cardmarket'
                  AND e.game_id = %s
                  AND e.last_seen_at = latest.ts
                  AND (
                    lower(e.name) LIKE '%%don!!%%'
                    OR lower(e.name) LIKE '%%don card%%'
                    OR lower(coalesce(e.category, '')) LIKE '%%don%%'
                    OR lower(coalesce(e.website_path, '')) LIKE '%%don%%'
                    OR lower(coalesce(e.raw_json::text, '')) LIKE '%%don!!%%'
                    OR lower(coalesce(e.raw_json::text, '')) LIKE '%%don card%%'
                  )
                ORDER BY e.product_group, lower(e.name), e.external_id
                LIMIT 2000
                """,
                (game_id, game_id),
            )

            report["queries"]["external_cardmarket_don_linked"] = _fetchall(
                cur,
                """
                SELECT
                  e.external_id,
                  e.name AS market_name,
                  l.print_id,
                  l.mapping_method,
                  l.confidence,
                  l.link_status,
                  l.reviewed,
                  l.evidence,
                  c.name AS canonical_name,
                  p.collector_number,
                  p.variant,
                  p.language
                FROM external_catalog_print_links l
                JOIN external_catalog_products e ON e.id = l.external_product_id
                JOIN prints p ON p.id = l.print_id
                JOIN cards c ON c.id = p.card_id
                WHERE e.source = 'cardmarket'
                  AND e.game_id = %s
                  AND (
                    lower(e.name) LIKE '%%don!!%%'
                    OR lower(e.name) LIKE '%%don card%%'
                    OR lower(coalesce(e.category, '')) LIKE '%%don%%'
                    OR lower(coalesce(e.website_path, '')) LIKE '%%don%%'
                  )
                ORDER BY e.external_id, l.id
                LIMIT 2000
                """,
                (game_id,),
            )

            print_ids = [row["id"] for row in report["queries"]["prints"]]
            if print_ids:
                report["queries"]["price_counts"] = _fetchall(
                    cur,
                    """
                    SELECT p.print_id, count(*) AS price_rows
                    FROM prices p
                    WHERE p.print_id = ANY(%s)
                    GROUP BY p.print_id
                    ORDER BY p.print_id
                    """,
                    (print_ids,),
                )
            else:
                report["queries"]["price_counts"] = []

        conn.rollback()
        report["status"] = "pass"
    except Exception as exc:
        conn.rollback()
        report["status"] = "fail"
        report["error"] = f"{type(exc).__name__}: {exc}"
        exit_code = 1
    finally:
        conn.close()
        _write_report(report)

    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
