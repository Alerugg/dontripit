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
    "%don!! card%",
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
    }
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

            source_predicate = " OR ".join(["lower(sr.raw_json::text) LIKE %s" for _ in TERMS])
            source_records = _fetchall(
                cur,
                f"""
                SELECT sr.id, sr.source_id, s.name AS source_name, sr.checksum, sr.raw_json, sr.ingested_at
                FROM source_records sr
                JOIN sources s ON s.id = sr.source_id
                WHERE ({source_predicate})
                ORDER BY sr.id
                LIMIT 500
                """,
                tuple(term.lower() for term in TERMS),
            )
            report["queries"]["source_records"] = source_records

            report["queries"]["sources"] = _fetchall(
                cur,
                """
                SELECT id, name, description
                FROM sources
                WHERE lower(name) LIKE '%%onepiece%%'
                   OR lower(coalesce(description, '')) LIKE '%%one piece%%'
                   OR lower(name) LIKE '%%bushiroad%%'
                   OR lower(name) LIKE '%%premier%%'
                ORDER BY id
                """,
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
    finally:
        conn.close()

    report["counts"] = {
        key: len(value)
        for key, value in report["queries"].items()
        if isinstance(value, list)
    }
    report["status"] = "pass" if report["transaction_read_only"] else "fail"
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
