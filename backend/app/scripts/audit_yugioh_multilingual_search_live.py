from __future__ import annotations

import json
import os

import psycopg2


LANGUAGES = ("en", "es", "ja")
EXPECTED_REVISION = "20260815_36"
EXPECTED_PRINTS = {"en": 44226, "es": 38249, "ja": 36426}
EXPECTED_LOCALIZATIONS = {"en": 0, "es": 38249, "ja": 36426}
EXPECTED_MISSING_LOCALIZED_NAMES = {"es": 17, "ja": 0}


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
                FROM prints p JOIN cards c ON c.id=p.card_id
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
                SELECT lower(coalesce(psp.language,'')), count(*)
                FROM print_search_profiles psp
                WHERE psp.game_id=%s AND lower(coalesce(psp.language,'')) IN ('en','es','ja')
                GROUP BY 1 ORDER BY 1
                """,
                (game_id,),
            )

            cur.execute(
                """
                SELECT lower(pl.language), count(*)
                FROM print_localizations pl
                JOIN prints p ON p.id=pl.print_id
                JOIN cards c ON c.id=p.card_id
                WHERE c.game_id=%s
                  AND lower(pl.language) IN ('es','ja')
                  AND (pl.card_name IS NULL OR btrim(pl.card_name)='')
                GROUP BY 1 ORDER BY 1
                """,
                (game_id,),
            )
            missing_names = {"es": 0, "ja": 0}
            for language, count in cur.fetchall():
                if language in missing_names:
                    missing_names[str(language)] = int(count or 0)

            cur.execute(
                """
                SELECT count(*)
                FROM print_localizations pl
                JOIN prints p ON p.id=pl.print_id
                JOIN cards c ON c.id=p.card_id
                WHERE c.game_id=%s
                  AND lower(pl.language) IN ('es','ja')
                  AND lower(pl.language)<>lower(coalesce(p.language,''))
                """,
                (game_id,),
            )
            localization_language_mismatches = int(cur.fetchone()[0] or 0)

            cur.execute(
                """
                SELECT count(*)
                FROM print_search_profiles psp
                JOIN prints p ON p.id=psp.print_id
                WHERE psp.game_id=%s
                  AND lower(coalesce(psp.language,''))<>lower(coalesce(p.language,''))
                """,
                (game_id,),
            )
            profile_language_mismatches = int(cur.fetchone()[0] or 0)

            cur.execute(
                """
                SELECT count(*) FROM prints p JOIN cards c ON c.id=p.card_id
                WHERE c.game_id=%s
                  AND lower(coalesce(p.language,'')) IN ('en','es','ja')
                  AND NOT EXISTS (SELECT 1 FROM print_search_profiles psp WHERE psp.print_id=p.id)
                """,
                (game_id,),
            )
            missing_profiles = int(cur.fetchone()[0] or 0)

            cur.execute(
                """
                SELECT count(*) FROM (
                  SELECT psp.print_id FROM print_search_profiles psp
                  WHERE psp.game_id=%s GROUP BY psp.print_id HAVING count(*)>1
                ) d
                """,
                (game_id,),
            )
            duplicate_profiles = int(cur.fetchone()[0] or 0)

            def shared_with(language: str) -> int:
                cur.execute(
                    """
                    SELECT count(*) FROM cards c
                    WHERE c.game_id=%s
                      AND EXISTS (SELECT 1 FROM prints pen WHERE pen.card_id=c.id AND lower(coalesce(pen.language,''))='en')
                      AND EXISTS (SELECT 1 FROM prints px WHERE px.card_id=c.id AND lower(coalesce(px.language,''))=%s)
                    """,
                    (game_id, language),
                )
                return int(cur.fetchone()[0] or 0)

            shared_en_es_cards = shared_with("es")
            shared_en_ja_cards = shared_with("ja")

            cur.execute(
                """
                SELECT lower(coalesce(p.language,'')), count(DISTINCT p.id)
                FROM prints p
                JOIN cards c ON c.id=p.card_id
                WHERE c.game_id=%s
                  AND lower(coalesce(p.language,'')) IN ('en','es','ja')
                  AND EXISTS (
                    SELECT 1
                    FROM external_catalog_print_links l
                    JOIN external_catalog_products e ON e.id=l.external_product_id
                    WHERE l.print_id=p.id
                      AND e.source='cardmarket'
                      AND e.product_group='single'
                      AND e.game_id=%s
                      AND l.link_status IN ('accepted','mapped','exact')
                  )
                GROUP BY 1 ORDER BY 1
                """,
                (game_id, game_id),
            )
            cardmarket_link_counts = {language: 0 for language in LANGUAGES}
            for language, count in cur.fetchall():
                if language in cardmarket_link_counts:
                    cardmarket_link_counts[str(language)] = int(count or 0)

            cur.execute("SELECT pg_database_size(current_database())")
            database_bytes = int(cur.fetchone()[0] or 0)
            conn.rollback()
    finally:
        conn.close()

    gates = {
        "schema_revision_exact": revision == EXPECTED_REVISION,
        "physical_print_counts_exact": print_counts == EXPECTED_PRINTS,
        "localization_counts_exact": localization_counts == EXPECTED_LOCALIZATIONS,
        "localization_distinct_prints_exact": localization_distinct_prints == EXPECTED_LOCALIZATIONS,
        "localized_name_retention_exact": missing_names == EXPECTED_MISSING_LOCALIZED_NAMES,
        "localization_languages_match_physical_prints": localization_language_mismatches == 0,
        "search_profile_counts_exact": profile_counts == EXPECTED_PRINTS,
        "search_profile_languages_match_physical_prints": profile_language_mismatches == 0,
        "all_physical_prints_indexed": missing_profiles == 0,
        "one_profile_per_physical_print": duplicate_profiles == 0,
        "english_to_spanish_logical_bridge_present": shared_en_es_cards > 0,
        "english_to_japanese_logical_bridge_present": shared_en_ja_cards > 0,
    }

    report = {
        "status": "pass" if all(gates.values()) else "blocked",
        "mode": "production-read-only-yugioh-multilingual-search-v2-audit",
        "production_writes": 0,
        "alembic_revision": revision,
        "expected_print_counts": EXPECTED_PRINTS,
        "print_counts": print_counts,
        "expected_localization_counts": EXPECTED_LOCALIZATIONS,
        "localization_counts": localization_counts,
        "localization_distinct_prints": localization_distinct_prints,
        "missing_localized_names": missing_names,
        "localization_language_mismatches": localization_language_mismatches,
        "search_profile_counts": profile_counts,
        "profile_language_mismatches": profile_language_mismatches,
        "missing_search_profiles": missing_profiles,
        "duplicate_search_profiles": duplicate_profiles,
        "shared_logical_cards": {"en_es": shared_en_es_cards, "en_ja": shared_en_ja_cards},
        "cardmarket_accepted_link_counts": cardmarket_link_counts,
        "database_bytes": database_bytes,
        "database_mib": round(database_bytes / 1024 / 1024, 2),
        "gates": gates,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return report


if __name__ == "__main__":
    run()
