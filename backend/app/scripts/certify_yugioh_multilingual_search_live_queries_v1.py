from __future__ import annotations

import json

from sqlalchemy import text

from app import db
from app.search_v2.yugioh_advanced import advanced_yugioh_search
from app.search_v2.yugioh_query import normal_yugioh_search


EXPECTED_PROFILES = {"en": 44226, "es": 38249, "ja": 36426}


def _find_card_result(rows: list[dict], card_id: int) -> dict:
    for row in rows:
        if int(row.get("card_id") or 0) == card_id:
            return row
    raise AssertionError(f"Expected card_id={card_id} not found in results")


def _assert_physical(session, result: dict, expected_card_id: int, expected_language: str) -> dict:
    matched = result["matched_print"] if result.get("type") == "card" else result
    print_id = int(matched["print_id"])
    physical = session.execute(
        text(
            """
            SELECT p.card_id, lower(coalesce(p.language,'')) AS language,
                   pl.card_name, pl.set_name
            FROM prints p
            LEFT JOIN print_localizations pl
              ON pl.print_id=p.id AND lower(pl.language)=lower(coalesce(p.language,''))
            WHERE p.id=:print_id
            """
        ),
        {"print_id": print_id},
    ).mappings().one()
    assert int(physical["card_id"]) == expected_card_id, physical
    assert physical["language"] == expected_language, physical
    assert matched["language"] == expected_language, matched
    assert matched["display_language"] == expected_language, matched
    if expected_language in {"es", "ja"}:
        assert physical["card_name"], physical
        assert result["name"] == physical["card_name"], (result, physical)
    return {"print_id": print_id, "language": physical["language"], "name": result["name"]}


def run() -> dict:
    db.init_engine()
    with db.SessionLocal() as session:
        game_id = int(session.execute(text("SELECT id FROM games WHERE slug='yugioh' LIMIT 1")).scalar_one())

        profile_rows = session.execute(
            text(
                """
                SELECT lower(coalesce(language,'')), count(*)
                FROM print_search_profiles
                WHERE game_id=:game_id AND lower(coalesce(language,'')) IN ('en','es','ja')
                GROUP BY 1 ORDER BY 1
                """
            ),
            {"game_id": game_id},
        ).all()
        profiles = {"en": 0, "es": 0, "ja": 0}
        for language, count in profile_rows:
            profiles[str(language)] = int(count or 0)
        assert profiles == EXPECTED_PROFILES, profiles

        sample = session.execute(
            text(
                """
                SELECT
                  c.id AS card_id,
                  c.name AS canonical_name,
                  (
                    SELECT pl.card_name
                    FROM prints p JOIN print_localizations pl ON pl.print_id=p.id
                    WHERE p.card_id=c.id AND lower(coalesce(p.language,''))='es'
                      AND lower(pl.language)='es' AND pl.card_name IS NOT NULL AND btrim(pl.card_name)<>''
                    ORDER BY p.id LIMIT 1
                  ) AS es_name,
                  (
                    SELECT pl.card_name
                    FROM prints p JOIN print_localizations pl ON pl.print_id=p.id
                    WHERE p.card_id=c.id AND lower(coalesce(p.language,''))='ja'
                      AND lower(pl.language)='ja' AND pl.card_name IS NOT NULL AND btrim(pl.card_name)<>''
                    ORDER BY p.id LIMIT 1
                  ) AS ja_name
                FROM cards c
                WHERE c.game_id=:game_id
                  AND EXISTS (SELECT 1 FROM prints p WHERE p.card_id=c.id AND lower(coalesce(p.language,''))='en')
                  AND EXISTS (
                    SELECT 1 FROM prints p JOIN print_localizations pl ON pl.print_id=p.id
                    WHERE p.card_id=c.id AND lower(coalesce(p.language,''))='es'
                      AND lower(pl.language)='es' AND pl.card_name IS NOT NULL AND btrim(pl.card_name)<>''
                  )
                  AND EXISTS (
                    SELECT 1 FROM prints p JOIN print_localizations pl ON pl.print_id=p.id
                    WHERE p.card_id=c.id AND lower(coalesce(p.language,''))='ja'
                      AND lower(pl.language)='ja' AND pl.card_name IS NOT NULL AND btrim(pl.card_name)<>''
                  )
                ORDER BY (lower(c.name)='dark magician') DESC, c.id ASC
                LIMIT 1
                """
            ),
            {"game_id": game_id},
        ).mappings().one()
        card_id = int(sample["card_id"])
        canonical_name = str(sample["canonical_name"])
        es_name = str(sample["es_name"])
        ja_name = str(sample["ja_name"])

        checks: dict[str, dict] = {}

        en = _find_card_result(normal_yugioh_search(session, query=canonical_name, language="en", limit=50), card_id)
        checks["english"] = _assert_physical(session, en, card_id, "en")

        es_from_en = _find_card_result(normal_yugioh_search(session, query=canonical_name, language="es", limit=50), card_id)
        checks["english_query_spanish_filter"] = _assert_physical(session, es_from_en, card_id, "es")

        ja_from_en = _find_card_result(normal_yugioh_search(session, query=canonical_name, language="ja", limit=50), card_id)
        checks["english_query_japanese_filter"] = _assert_physical(session, ja_from_en, card_id, "ja")

        es_direct = _find_card_result(normal_yugioh_search(session, query=es_name, language="es", limit=50), card_id)
        checks["spanish_direct"] = _assert_physical(session, es_direct, card_id, "es")

        ja_direct = _find_card_result(normal_yugioh_search(session, query=ja_name, language="ja", limit=50), card_id)
        checks["japanese_direct"] = _assert_physical(session, ja_direct, card_id, "ja")

        mixed = _find_card_result(normal_yugioh_search(session, query=canonical_name, language="en,ja", limit=50), card_id)
        mixed_language = mixed["matched_print"]["language"]
        assert mixed_language in {"en", "ja"}, mixed
        checks["english_japanese_multiselect"] = _assert_physical(session, mixed, card_id, mixed_language)

        advanced = advanced_yugioh_search(
            session,
            filters={"language": ["ja"]},
            query=canonical_name,
            language="ja",
            limit=50,
            offset=0,
        )
        advanced_row = _find_card_result(advanced["items"], card_id)
        assert all(item["language"] == "ja" for item in advanced["items"]), advanced["items"][:10]
        physical = session.execute(
            text("SELECT lower(coalesce(language,'')) FROM prints WHERE id=:print_id"),
            {"print_id": int(advanced_row["print_id"])},
        ).scalar_one()
        assert physical == "ja", advanced_row
        checks["advanced_japanese"] = {"print_id": int(advanced_row["print_id"]), "language": "ja", "name": advanced_row["name"]}

        session.rollback()

    report = {
        "status": "pass",
        "production_writes": 0,
        "profiles": profiles,
        "sample": {
            "card_id": card_id,
            "canonical_name": canonical_name,
            "spanish_name": es_name,
            "japanese_name": ja_name,
        },
        "checks": checks,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return report


if __name__ == "__main__":
    run()
