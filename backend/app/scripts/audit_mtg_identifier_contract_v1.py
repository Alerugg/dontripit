from __future__ import annotations

import json
import os

import psycopg2


def _db_url() -> str:
    value = os.getenv("DATABASE_URL_UNPOOLED") or os.getenv("DATABASE_URL")
    if not value:
        raise RuntimeError("DATABASE_URL[_UNPOOLED] is required")
    if value.startswith("postgresql+psycopg2://"):
        return "postgresql://" + value[len("postgresql+psycopg2://"):]
    if value.startswith("postgres://"):
        return "postgresql://" + value[len("postgres://"):]
    return value


def main() -> int:
    conn = psycopg2.connect(_db_url(), connect_timeout=30, application_name="dontripit_mtg_identifier_contract_audit")
    conn.set_session(readonly=True, autocommit=False)
    try:
        with conn.cursor() as cur:
            cur.execute("SHOW transaction_read_only")
            if str(cur.fetchone()[0]).lower() != "on":
                raise RuntimeError("read-only transaction guard failed")
            cur.execute("SELECT id,slug FROM games WHERE slug IN ('mtg','magic-the-gathering','magic') ORDER BY CASE slug WHEN 'mtg' THEN 0 ELSE 1 END,id LIMIT 1")
            game = cur.fetchone()
            if not game:
                raise RuntimeError("MTG game missing")
            game_id, game_slug = int(game[0]), str(game[1])

            cur.execute("SELECT count(*) FROM prints p JOIN cards c ON c.id=p.card_id WHERE c.game_id=%s", (game_id,))
            prints = int(cur.fetchone()[0])
            cur.execute("SELECT lower(coalesce(p.language,'')),count(*) FROM prints p JOIN cards c ON c.id=p.card_id WHERE c.game_id=%s GROUP BY 1 ORDER BY 1", (game_id,))
            languages = {str(k): int(v) for k, v in cur.fetchall()}
            cur.execute("SELECT count(*) FROM print_identifiers pi JOIN prints p ON p.id=pi.print_id JOIN cards c ON c.id=p.card_id WHERE c.game_id=%s AND pi.source='scryfall'", (game_id,))
            identifiers = int(cur.fetchone()[0])
            cur.execute("SELECT count(DISTINCT pi.external_id) FROM print_identifiers pi JOIN prints p ON p.id=pi.print_id JOIN cards c ON c.id=p.card_id WHERE c.game_id=%s AND pi.source='scryfall'", (game_id,))
            distinct_identifiers = int(cur.fetchone()[0])
            cur.execute("SELECT count(DISTINCT p.scryfall_id) FROM prints p JOIN cards c ON c.id=p.card_id WHERE c.game_id=%s AND p.scryfall_id IS NOT NULL", (game_id,))
            distinct_objects = int(cur.fetchone()[0])
            cur.execute("""
                SELECT count(*) FROM (
                  SELECT p.scryfall_id FROM prints p JOIN cards c ON c.id=p.card_id
                  WHERE c.game_id=%s AND p.scryfall_id IS NOT NULL
                  GROUP BY p.scryfall_id HAVING count(*)>1
                ) x
            """, (game_id,))
            multi_finish_objects = int(cur.fetchone()[0])
            cur.execute("""
                SELECT count(*) FROM print_identifiers pi
                JOIN prints p ON p.id=pi.print_id JOIN cards c ON c.id=p.card_id
                WHERE c.game_id=%s AND pi.source='scryfall' AND lower(pi.external_id)<>lower(coalesce(p.scryfall_id,''))
            """, (game_id,))
            transformed_identifiers = int(cur.fetchone()[0])
            cur.execute("""
                SELECT lower(coalesce(p.language,'')),p.variant,count(*)
                FROM prints p JOIN cards c ON c.id=p.card_id
                LEFT JOIN print_identifiers pi ON pi.print_id=p.id AND pi.source='scryfall'
                WHERE c.game_id=%s AND p.scryfall_id IS NOT NULL AND pi.print_id IS NULL
                GROUP BY 1,2 ORDER BY 1,2
            """, (game_id,))
            missing_identifier_by_language_variant = {
                f"{lang}:{variant}": int(count) for lang, variant, count in cur.fetchall()
            }
            cur.execute("""
                WITH multi AS (
                  SELECT p.scryfall_id
                  FROM prints p JOIN cards c ON c.id=p.card_id
                  WHERE c.game_id=%s AND p.scryfall_id IS NOT NULL
                  GROUP BY p.scryfall_id HAVING count(*)>1
                )
                SELECT p.scryfall_id,
                       array_agg(p.variant ORDER BY p.variant) AS variants,
                       array_agg(p.variant ORDER BY p.variant) FILTER (WHERE pi.print_id IS NOT NULL) AS identifier_owner_variants,
                       count(pi.print_id) AS identifier_rows
                FROM multi m JOIN prints p ON p.scryfall_id=m.scryfall_id
                JOIN cards c ON c.id=p.card_id
                LEFT JOIN print_identifiers pi ON pi.print_id=p.id AND pi.source='scryfall'
                WHERE c.game_id=%s
                GROUP BY p.scryfall_id ORDER BY p.scryfall_id LIMIT 20
            """, (game_id, game_id))
            samples = [
                {
                    "scryfall_id": str(sid),
                    "variants": list(variants or []),
                    "identifier_owner_variants": list(owner_variants or []),
                    "identifier_rows": int(rows),
                }
                for sid, variants, owner_variants, rows in cur.fetchall()
            ]

            report = {
                "status": "pass",
                "production_writes": 0,
                "transaction_read_only": True,
                "game": {"id": game_id, "slug": game_slug},
                "prints": prints,
                "languages": languages,
                "scryfall_print_identifiers": identifiers,
                "distinct_print_identifier_external_ids": distinct_identifiers,
                "distinct_print_scryfall_objects": distinct_objects,
                "multi_finish_scryfall_objects": multi_finish_objects,
                "transformed_or_suffixed_identifiers": transformed_identifiers,
                "missing_identifier_by_language_variant": missing_identifier_by_language_variant,
                "multi_finish_samples": samples,
            }
            print(json.dumps(report, indent=2, ensure_ascii=False, default=str))
            conn.rollback()
            return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
