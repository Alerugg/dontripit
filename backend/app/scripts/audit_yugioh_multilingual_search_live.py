from __future__ import annotations

import json
import os
from collections import defaultdict

import psycopg2


LANGUAGES = ("en", "es", "ja")


def _database_url() -> str:
    value = os.getenv("DATABASE_URL_UNPOOLED") or os.getenv("DATABASE_URL")
    if not value:
        raise RuntimeError("DATABASE_URL_UNPOOLED or DATABASE_URL is required")
    return value


def _counts(cur, sql: str, params=()) -> dict[str, int]:
    cur.execute(sql, params)
    out = {language: 0 for language in LANGUAGES}
    for language, count in cur.fetchall():
        key = str(language or "").strip().lower()
        if key in out:
            out[key] = int(count or 0)
    return out


def run() -> dict:
    conn = psycopg2.connect(
        _database_url(),
        connect_timeout=30,
        application_name="dontripit_yugioh_multilingual_search_live_audit",
    )
    conn.set_session(readonly=True, autocommit=False)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT version_num FROM alembic_version LIMIT 1")
            revision_row = cur.fetchone()
            revision = str(revision_row[0]) if revision_row else None

            cur.execute("SELECT id FROM games WHERE slug='yugioh' LIMIT 1")
            game_row = cur.fetchone()
            if not game_row:
                raise RuntimeError("Yu-Gi-Oh game row not found")
            game_id = int(game_row[0])

            print_counts = _counts(
                cur,
                """
                SELECT lower(coalesce(p.language,'')), count(*)
                FROM prints p
                JOIN cards c ON c.id=p.card_id
                WHERE c.game_id=%s AND lower(coalesce(p.language,'')) IN ('en','es','ja')
                GROUP BY 1 ORDER BY 1
                """,
                (game_id,),
            )

            localization_counts = _counts(
                cur,
                """
                SELECT lower(pl.language), count(*)
                FROM print_localizations pl
                JOIN prints p ON p.id=pl.print_id
                JOIN cards c ON c.id=p.card_id
                WHERE c.game_id=%s AND lower(pl.language) IN ('en','es','ja')
                GROUP BY 1 ORDER BY 1
                """,
                (game_id,),
            )

            localization_distinct_prints = _counts(
                cur,
                """
                SELECT lower(pl.language), count(DISTINCT pl.print_id)
                FROM print_localizations pl
                JOIN prints p ON p.id=pl.print_id
                JOIN cards c ON c.id=p.card_id
                WHERE c.game_id=%s AND lower(pl.language) IN ('en','es','ja')
                GROUP BY 1 ORDER BY 1
                """,
                (game_id,),
            )

            profile_counts = _counts(
                cur,
                """
                SELECT lower(coalesce(p.language,'')), count(*)
                FROM print_search_profiles psp
                JOIN prints p ON p.id=psp.print_id
                WHERE psp.game_id=%s AND lower(coalesce(p.language,'')) IN ('en','es','ja')
                GROUP BY 1 ORDER BY 1
                """,
                (game_id,),
            )

            cur.execute(
                """
                SELECT count(*)
                FROM cards c
                WHERE c.game_id=%s
                  AND EXISTS (
                    SELECT 1 FROM prints pen
                    WHERE pen.card_id=c.id AND lower(coalesce(pen.language,''))='en'
                  )
                  AND EXISTS (
                    SELECT 1 FROM prints pja
                    WHERE pja.card_id=c.id AND lower(coalesce(pja.language,''))='ja'
                  )
                """,
                (game_id,),
            )
            shared_en_ja_cards = int(cur.fetchone()[0] or 0)

            cur.execute(
                """
                SELECT count(*)
                FROM cards c
                WHERE c.game_id=%s
                  AND EXISTS (
                    SELECT 1 FROM prints pen
                    WHERE pen.card_id=c.id AND lower(coalesce(pen.language,''))='en'
                  )
                  AND EXISTS (
                    SELECT 1 FROM prints pes
                    WHERE pes.card_id=c.id AND lower(coalesce(pes.language,''))='es'
                  )
                """,
                (game_id,),
            )
            shared_en_es_cards = int(cur.fetchone()[0] or 0)

            cur.execute(
                """
                SELECT
                  c.id,
                  c.name,
                  p.id,
                  p.collector_number,
                  lower(coalesce(p.language,'')),
                  pl.card_name,
                  EXISTS (SELECT 1 FROM print_search_profiles psp WHERE psp.print_id=p.id) AS indexed
                FROM cards c
                JOIN prints p ON p.card_id=c.id
                LEFT JOIN print_localizations pl
                  ON pl.print_id=p.id AND lower(pl.language)=lower(coalesce(p.language,''))
                WHERE c.game_id=%s
                  AND lower(c.name) IN ('dark magician','blue-eyes white dragon')
                  AND lower(coalesce(p.language,'')) IN ('en','es','ja')
                ORDER BY lower(c.name), lower(coalesce(p.language,'')), p.id
                LIMIT 60
                """,
                (game_id,),
            )
            samples = [
                {
                    "card_id": int(card_id),
                    "canonical_name": canonical_name,
                    "print_id": int(print_id),
                    "collector_number": collector_number,
                    "language": language,
                    "localized_name": localized_name,
                    "indexed": bool(indexed),
                }
                for card_id, canonical_name, print_id, collector_number, language, localized_name, indexed in cur.fetchall()
            ]

            cur.execute(
                """
                SELECT lower(coalesce(p.language,'')), count(*)
                FROM prints p
                JOIN cards c ON c.id=p.card_id
                WHERE c.game_id=%s
                  AND lower(coalesce(p.language,'')) IN ('en','es','ja')
                  AND EXISTS (
                    SELECT 1
                    FROM external_product_links epl
                    WHERE epl.entity_type='print'
                      AND epl.entity_id=p.id
                      AND lower(epl.provider)='cardmarket'
                      AND lower(coalesce(epl.status,''))='accepted'
                  )
                GROUP BY 1 ORDER BY 1
                """,
                (game_id,),
            )
            cardmarket_link_counts = {language: 0 for language in LANGUAGES}
            for language, count in cur.fetchall():
                if language in cardmarket_link_counts:
                    cardmarket_link_counts[language] = int(count or 0)

            conn.rollback()
    finally:
        conn.close()

    gates = {
        "schema_has_print_localizations": revision is not None,
        "english_prints_present": print_counts["en"] > 0,
        "spanish_prints_materialized": print_counts["es"] > 0,
        "japanese_prints_materialized": print_counts["ja"] > 0,
        "spanish_localizations_materialized": localization_counts["es"] > 0,
        "japanese_localizations_materialized": localization_counts["ja"] > 0,
        "spanish_search_profiles_materialized": profile_counts["es"] > 0,
        "japanese_search_profiles_materialized": profile_counts["ja"] > 0,
        "english_to_spanish_logical_bridge_present": shared_en_es_cards > 0,
        "english_to_japanese_logical_bridge_present": shared_en_ja_cards > 0,
    }

    report = {
        "status": "pass" if all(gates.values()) else "blocked",
        "mode": "production-read-only-yugioh-multilingual-search-v2-audit",
        "production_writes": 0,
        "alembic_revision": revision,
        "print_counts": print_counts,
        "localization_counts": localization_counts,
        "localization_distinct_prints": localization_distinct_prints,
        "search_profile_counts": profile_counts,
        "shared_logical_cards": {"en_es": shared_en_es_cards, "en_ja": shared_en_ja_cards},
        "cardmarket_accepted_link_counts": cardmarket_link_counts,
        "samples": samples,
        "gates": gates,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return report


if __name__ == "__main__":
    run()
