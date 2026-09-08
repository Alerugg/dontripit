#!/usr/bin/env python3
from __future__ import annotations

import json
import os

import psycopg2
from psycopg2.extras import RealDictCursor


def main() -> int:
    url = os.getenv("DATABASE_URL_UNPOOLED") or os.getenv("DATABASE_URL")
    if not url:
        raise SystemExit("DATABASE_URL_UNPOOLED or DATABASE_URL is required")

    conn = psycopg2.connect(url)
    conn.autocommit = False
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT id FROM games WHERE slug='yugioh'")
            game_id = int(cur.fetchone()["id"])

            cur.execute(
                """
                SELECT count(*) AS n
                FROM external_catalog_products
                WHERE source='cardmarket' AND game_id=%s AND product_group='single'
                  AND name ~* '\\(V\\.?[[:space:]]*[0-9]+'
                """,
                (game_id,),
            )
            names_with_version_token = int(cur.fetchone()["n"])

            cur.execute(
                """
                SELECT count(*) AS n
                FROM external_catalog_products
                WHERE source='cardmarket' AND game_id=%s AND product_group='single'
                  AND name ~* '(Ultra Rare|Secret Rare|Ultimate Rare|Common|Super Rare|Rare|Ghost Rare|Starlight Rare|Collector.?s Rare|Quarter Century)'
                """,
                (game_id,),
            )
            names_with_rarity_token = int(cur.fetchone()["n"])

            cur.execute(
                """
                SELECT id, external_id, expansion_external_id, name, website_path,
                       raw_json
                FROM external_catalog_products
                WHERE source='cardmarket' AND game_id=%s AND product_group='single'
                  AND lower(name) LIKE '%%blue-eyes white dragon%%'
                ORDER BY date_added NULLS LAST, id
                LIMIT 250
                """,
                (game_id,),
            )
            blue_eyes = [dict(r) for r in cur.fetchall()]

            cur.execute(
                """
                SELECT id, external_id, expansion_external_id, name, website_path,
                       raw_json
                FROM external_catalog_products
                WHERE source='cardmarket' AND game_id=%s AND product_group='single'
                  AND name ~* '\\(V\\.?[[:space:]]*[0-9]+'
                ORDER BY id
                LIMIT 100
                """,
                (game_id,),
            )
            version_samples = [dict(r) for r in cur.fetchall()]

        result = {
            "mode": "read_only",
            "game": "yugioh",
            "summary": {
                "names_with_version_token": names_with_version_token,
                "names_with_rarity_token": names_with_rarity_token,
                "blue_eyes_rows": len(blue_eyes),
                "production_writes": 0,
            },
            "blue_eyes": blue_eyes,
            "version_samples": version_samples,
        }
        print("YGO_VARIANT_METADATA_V6=" + json.dumps(result["summary"], separators=(",", ":")))
        report = os.getenv("REPORT_PATH")
        if report:
            with open(report, "w", encoding="utf-8") as f:
                json.dump(result, f, ensure_ascii=False, indent=2, default=str)
                f.write("\n")
        conn.rollback()
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
