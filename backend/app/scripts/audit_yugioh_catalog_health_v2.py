from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import text

from app import db


EXPECTED = {
    "sets": 646,
    "cards": 14479,
    "prints": 44226,
    "catalog_releases": 1032,
    "print_releases": 44226,
    "card_attributes": 14479,
    "print_attributes": 44226,
    "print_images": 44226,
    "cards_without_prints": 490,
    "fallback_rows": 12,
    "deduplicated_source_rows": 52,
    "noisy_rarity_source_rows": 206,
}

NOISY_RARITIES = [
    "2",
    "3",
    "European & Oceanian debut",
    "European debut",
    "New",
    "New artwork",
    "Oceanian debut",
    "Reprint",
    "force-SMW",
]

QUARANTINED_ASSIGNMENTS = [
    ("72843899", "BLCR-EN012"),
    ("46358784", "BLCR-EN013"),
    ("71620241", "BLCR-EN015"),
    ("45236142", "BLCR-EN016"),
    ("88120966", "LDS3-EN063"),
    ("94820406", "SGX3-ENA11"),
    ("24508238", "SGX3-ENE10"),
    ("78060096", "SGX3-ENI25"),
]


def _write(path: Path | None, payload: dict) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )


def _scalar(session, sql: str, params: dict | None = None) -> int:
    return int(session.execute(text(sql), params or {}).scalar_one() or 0)


def _distribution(session, sql: str, params: dict | None = None, *, limit: int = 100) -> list[dict]:
    rows = session.execute(text(sql), params or {}).mappings().all()
    output = []
    for row in rows[:limit]:
        output.append({key: value for key, value in dict(row).items()})
    return output


def _coverage(session, game_id: int, path: str) -> int:
    return _scalar(
        session,
        """
        SELECT COUNT(*)
        FROM card_attributes ca
        JOIN cards c ON c.id=ca.card_id
        WHERE c.game_id=:game
          AND ca.attributes_json ? :key
          AND ca.attributes_json->:key IS NOT NULL
          AND ca.attributes_json->:key <> 'null'::jsonb
          AND COALESCE(NULLIF(BTRIM(ca.attributes_json->>:key), ''), '') <> ''
        """,
        {"game": game_id, "key": path},
    )


def run(*, report_path: Path | None = None) -> dict:
    db.init_engine()
    with db.SessionLocal() as session:
        game_id = session.execute(
            text("SELECT id FROM games WHERE slug='yugioh' LIMIT 1")
        ).scalar_one_or_none()
        if game_id is None:
            raise AssertionError("Yu-Gi-Oh game row is missing")
        game_id = int(game_id)

        counts = {
            "sets": _scalar(session, "SELECT COUNT(*) FROM sets WHERE game_id=:game", {"game": game_id}),
            "cards": _scalar(session, "SELECT COUNT(*) FROM cards WHERE game_id=:game", {"game": game_id}),
            "prints": _scalar(
                session,
                "SELECT COUNT(*) FROM prints p JOIN cards c ON c.id=p.card_id WHERE c.game_id=:game",
                {"game": game_id},
            ),
            "catalog_releases": _scalar(session, "SELECT COUNT(*) FROM catalog_releases WHERE game_id=:game", {"game": game_id}),
            "print_releases": _scalar(
                session,
                "SELECT COUNT(*) FROM print_releases pr JOIN prints p ON p.id=pr.print_id JOIN cards c ON c.id=p.card_id WHERE c.game_id=:game",
                {"game": game_id},
            ),
            "card_attributes": _scalar(
                session,
                "SELECT COUNT(*) FROM card_attributes ca JOIN cards c ON c.id=ca.card_id WHERE c.game_id=:game",
                {"game": game_id},
            ),
            "print_attributes": _scalar(
                session,
                "SELECT COUNT(*) FROM print_attributes pa JOIN prints p ON p.id=pa.print_id JOIN cards c ON c.id=p.card_id WHERE c.game_id=:game",
                {"game": game_id},
            ),
            "print_images": _scalar(
                session,
                "SELECT COUNT(*) FROM print_images pi JOIN prints p ON p.id=pi.print_id JOIN cards c ON c.id=p.card_id WHERE c.game_id=:game",
                {"game": game_id},
            ),
            "cards_without_prints": _scalar(
                session,
                "SELECT COUNT(*) FROM cards c WHERE c.game_id=:game AND NOT EXISTS (SELECT 1 FROM prints p WHERE p.card_id=c.id)",
                {"game": game_id},
            ),
        }

        for key, expected in EXPECTED.items():
            if key in counts and counts[key] != expected:
                raise AssertionError(f"YGO canonical count moved: {key}={counts[key]} expected={expected}")

        duplicate_checks = {
            "set_codes": _scalar(
                session,
                """
                SELECT COUNT(*) FROM (
                  SELECT code FROM sets WHERE game_id=:game GROUP BY code HAVING COUNT(*) > 1
                ) q
                """,
                {"game": game_id},
            ),
            "card_external_ids": _scalar(
                session,
                """
                SELECT COUNT(*) FROM (
                  SELECT yugoprodeck_id FROM cards
                  WHERE game_id=:game AND yugoprodeck_id IS NOT NULL
                  GROUP BY yugoprodeck_id HAVING COUNT(*) > 1
                ) q
                """,
                {"game": game_id},
            ),
            "print_external_ids": _scalar(
                session,
                """
                SELECT COUNT(*) FROM (
                  SELECT p.yugioh_id FROM prints p JOIN cards c ON c.id=p.card_id
                  WHERE c.game_id=:game AND p.yugioh_id IS NOT NULL
                  GROUP BY p.yugioh_id HAVING COUNT(*) > 1
                ) q
                """,
                {"game": game_id},
            ),
            "print_keys": _scalar(
                session,
                """
                SELECT COUNT(*) FROM (
                  SELECT p.print_key FROM prints p JOIN cards c ON c.id=p.card_id
                  WHERE c.game_id=:game AND p.print_key IS NOT NULL
                  GROUP BY p.print_key HAVING COUNT(*) > 1
                ) q
                """,
                {"game": game_id},
            ),
            "shared_print_tuple": _scalar(
                session,
                """
                SELECT COUNT(*) FROM (
                  SELECT p.set_id, p.collector_number, p.language, p.is_foil, p.variant
                  FROM prints p JOIN cards c ON c.id=p.card_id
                  WHERE c.game_id=:game
                  GROUP BY p.set_id, p.collector_number, p.language, p.is_foil, p.variant
                  HAVING COUNT(*) > 1
                ) q
                """,
                {"game": game_id},
            ),
        }
        if any(duplicate_checks.values()):
            raise AssertionError(f"Canonical YGO duplicate identity detected: {duplicate_checks}")

        linkage = {
            "prints_without_image": _scalar(
                session,
                """
                SELECT COUNT(*) FROM prints p JOIN cards c ON c.id=p.card_id
                WHERE c.game_id=:game
                  AND NOT EXISTS (SELECT 1 FROM print_images pi WHERE pi.print_id=p.id)
                """,
                {"game": game_id},
            ),
            "prints_without_release": _scalar(
                session,
                """
                SELECT COUNT(*) FROM prints p JOIN cards c ON c.id=p.card_id
                WHERE c.game_id=:game
                  AND NOT EXISTS (SELECT 1 FROM print_releases pr WHERE pr.print_id=p.id)
                """,
                {"game": game_id},
            ),
            "prints_with_multiple_releases": _scalar(
                session,
                """
                SELECT COUNT(*) FROM (
                  SELECT p.id
                  FROM prints p
                  JOIN cards c ON c.id=p.card_id
                  JOIN print_releases pr ON pr.print_id=p.id
                  WHERE c.game_id=:game
                  GROUP BY p.id HAVING COUNT(*) > 1
                ) q
                """,
                {"game": game_id},
            ),
            "releases_with_date": _scalar(
                session,
                "SELECT COUNT(*) FROM catalog_releases WHERE game_id=:game AND release_date IS NOT NULL",
                {"game": game_id},
            ),
            "releases_without_date": _scalar(
                session,
                "SELECT COUNT(*) FROM catalog_releases WHERE game_id=:game AND release_date IS NULL",
                {"game": game_id},
            ),
        }
        if linkage["prints_without_image"] != 0 or linkage["prints_without_release"] != 0:
            raise AssertionError(f"YGO Print linkage incomplete: {linkage}")

        fallback_rows = _scalar(
            session,
            """
            SELECT COUNT(*)
            FROM print_attributes pa
            JOIN prints p ON p.id=pa.print_id
            JOIN cards c ON c.id=p.card_id
            WHERE c.game_id=:game
              AND pa.attributes_json->>'family_resolution'='same_release_unanimous_fallback'
            """,
            {"game": game_id},
        )
        if fallback_rows != EXPECTED["fallback_rows"]:
            raise AssertionError(f"YGO fallback row count moved: {fallback_rows}")

        deduplicated_source_rows = _scalar(
            session,
            """
            SELECT COALESCE(SUM(GREATEST(jsonb_array_length(pa.attributes_json->'source_rows') - 1, 0)), 0)
            FROM print_attributes pa
            JOIN prints p ON p.id=pa.print_id
            JOIN cards c ON c.id=p.card_id
            WHERE c.game_id=:game
            """,
            {"game": game_id},
        )
        if deduplicated_source_rows != EXPECTED["deduplicated_source_rows"]:
            raise AssertionError(f"YGO deduped source row count moved: {deduplicated_source_rows}")

        noisy_rarity_source_rows = _scalar(
            session,
            """
            SELECT COUNT(*)
            FROM print_attributes pa
            JOIN prints p ON p.id=pa.print_id
            JOIN cards c ON c.id=p.card_id
            CROSS JOIN LATERAL jsonb_array_elements(pa.attributes_json->'source_rows') sr
            WHERE c.game_id=:game
              AND sr->>'rarity_raw' = ANY(:noisy)
            """,
            {"game": game_id, "noisy": NOISY_RARITIES},
        )
        if noisy_rarity_source_rows != EXPECTED["noisy_rarity_source_rows"]:
            raise AssertionError(f"YGO noisy rarity count moved: {noisy_rarity_source_rows}")

        spell_of_mask = {
            "canonical_card": _scalar(
                session,
                "SELECT COUNT(*) FROM cards WHERE game_id=:game AND yugoprodeck_id='300302018' AND name='Spell of Mask'",
                {"game": game_id},
            ),
            "alias_card": _scalar(
                session,
                "SELECT COUNT(*) FROM cards WHERE game_id=:game AND yugoprodeck_id='300302053'",
                {"game": game_id},
            ),
            "alias_evidence": _scalar(
                session,
                """
                SELECT COUNT(*) FROM card_attributes ca JOIN cards c ON c.id=ca.card_id
                WHERE c.game_id=:game AND c.yugoprodeck_id='300302018'
                  AND ca.attributes_json->'source_alias_ids' ? '300302053'
                """,
                {"game": game_id},
            ),
            "physical_print": _scalar(
                session,
                """
                SELECT COUNT(*) FROM prints p JOIN cards c ON c.id=p.card_id
                WHERE c.game_id=:game AND c.yugoprodeck_id='300302018' AND p.collector_number='SBCB-ENS08'
                """,
                {"game": game_id},
            ),
        }
        if spell_of_mask != {
            "canonical_card": 1,
            "alias_card": 0,
            "alias_evidence": 1,
            "physical_print": 1,
        }:
            raise AssertionError(f"Spell of Mask canonical alias gate moved: {spell_of_mask}")

        quarantined_present = []
        for external_id, collector in QUARANTINED_ASSIGNMENTS:
            count = _scalar(
                session,
                """
                SELECT COUNT(*) FROM prints p JOIN cards c ON c.id=p.card_id
                WHERE c.game_id=:game AND c.yugoprodeck_id=:card AND p.collector_number=:collector
                """,
                {"game": game_id, "card": external_id, "collector": collector},
            )
            if count:
                quarantined_present.append({"card": external_id, "collector": collector, "rows": count})
        if quarantined_present:
            raise AssertionError(f"Quarantined YGO source conflicts entered canonical catalog: {quarantined_present}")

        search_state = {}
        for table_name in ("search_documents", "card_search_profiles", "print_search_profiles", "facet_definitions"):
            exists = session.execute(text("SELECT to_regclass(:name) IS NOT NULL"), {"name": f"public.{table_name}"}).scalar_one()
            if not exists:
                search_state[table_name] = 0
            else:
                search_state[table_name] = _scalar(
                    session,
                    f'SELECT COUNT(*) FROM "{table_name}" WHERE game_id=:game',
                    {"game": game_id},
                )
        if any(search_state.values()):
            raise AssertionError(f"YGO Search V2 should still be empty before indexing: {search_state}")

        coverage_keys = [
            "category",
            "type",
            "frame_type",
            "race",
            "archetype",
            "attribute",
            "level",
            "rank",
            "scale",
            "atk",
            "def",
            "link_value",
        ]
        coverage = {key: _coverage(session, game_id, key) for key in coverage_keys}
        coverage["link_markers_nonempty"] = _scalar(
            session,
            """
            SELECT COUNT(*) FROM card_attributes ca JOIN cards c ON c.id=ca.card_id
            WHERE c.game_id=:game
              AND jsonb_typeof(ca.attributes_json->'link_markers')='array'
              AND jsonb_array_length(ca.attributes_json->'link_markers') > 0
            """,
            {"game": game_id},
        )
        coverage["typeline_nonempty"] = _scalar(
            session,
            """
            SELECT COUNT(*) FROM card_attributes ca JOIN cards c ON c.id=ca.card_id
            WHERE c.game_id=:game
              AND jsonb_typeof(ca.attributes_json->'typeline')='array'
              AND jsonb_array_length(ca.attributes_json->'typeline') > 0
            """,
            {"game": game_id},
        )
        coverage["banlist_info_nonempty"] = _scalar(
            session,
            """
            SELECT COUNT(*) FROM card_attributes ca JOIN cards c ON c.id=ca.card_id
            WHERE c.game_id=:game
              AND jsonb_typeof(ca.attributes_json->'banlist_info')='object'
              AND ca.attributes_json->'banlist_info' <> '{}'::jsonb
            """,
            {"game": game_id},
        )
        coverage["source_aliases_nonempty"] = _scalar(
            session,
            """
            SELECT COUNT(*) FROM card_attributes ca JOIN cards c ON c.id=ca.card_id
            WHERE c.game_id=:game
              AND jsonb_typeof(ca.attributes_json->'source_alias_ids')='array'
              AND jsonb_array_length(ca.attributes_json->'source_alias_ids') > 0
            """,
            {"game": game_id},
        )
        coverage["cards_with_artwork_candidates"] = _scalar(
            session,
            """
            SELECT COUNT(*) FROM card_attributes ca JOIN cards c ON c.id=ca.card_id
            WHERE c.game_id=:game
              AND jsonb_typeof(ca.attributes_json->'artwork_candidates')='array'
              AND jsonb_array_length(ca.attributes_json->'artwork_candidates') > 0
            """,
            {"game": game_id},
        )
        total_artwork_candidates = _scalar(
            session,
            """
            SELECT COALESCE(SUM(jsonb_array_length(ca.attributes_json->'artwork_candidates')),0)
            FROM card_attributes ca JOIN cards c ON c.id=ca.card_id
            WHERE c.game_id=:game
              AND jsonb_typeof(ca.attributes_json->'artwork_candidates')='array'
            """,
            {"game": game_id},
        )

        distributions = {
            "category": _distribution(
                session,
                """
                SELECT ca.attributes_json->>'category' AS value, COUNT(*) AS cards
                FROM card_attributes ca JOIN cards c ON c.id=ca.card_id
                WHERE c.game_id=:game AND COALESCE(ca.attributes_json->>'category','')<>''
                GROUP BY value ORDER BY cards DESC, value
                """,
                {"game": game_id},
                limit=50,
            ),
            "type": _distribution(
                session,
                """
                SELECT ca.attributes_json->>'type' AS value, COUNT(*) AS cards
                FROM card_attributes ca JOIN cards c ON c.id=ca.card_id
                WHERE c.game_id=:game AND COALESCE(ca.attributes_json->>'type','')<>''
                GROUP BY value ORDER BY cards DESC, value
                """,
                {"game": game_id},
                limit=100,
            ),
            "frame_type": _distribution(
                session,
                """
                SELECT ca.attributes_json->>'frame_type' AS value, COUNT(*) AS cards
                FROM card_attributes ca JOIN cards c ON c.id=ca.card_id
                WHERE c.game_id=:game AND COALESCE(ca.attributes_json->>'frame_type','')<>''
                GROUP BY value ORDER BY cards DESC, value
                """,
                {"game": game_id},
                limit=100,
            ),
            "attribute": _distribution(
                session,
                """
                SELECT ca.attributes_json->>'attribute' AS value, COUNT(*) AS cards
                FROM card_attributes ca JOIN cards c ON c.id=ca.card_id
                WHERE c.game_id=:game AND COALESCE(ca.attributes_json->>'attribute','')<>''
                GROUP BY value ORDER BY cards DESC, value
                """,
                {"game": game_id},
                limit=30,
            ),
            "race": _distribution(
                session,
                """
                SELECT ca.attributes_json->>'race' AS value, COUNT(*) AS cards
                FROM card_attributes ca JOIN cards c ON c.id=ca.card_id
                WHERE c.game_id=:game AND COALESCE(ca.attributes_json->>'race','')<>''
                GROUP BY value ORDER BY cards DESC, value
                """,
                {"game": game_id},
                limit=100,
            ),
            "top_archetypes": _distribution(
                session,
                """
                SELECT ca.attributes_json->>'archetype' AS value, COUNT(*) AS cards
                FROM card_attributes ca JOIN cards c ON c.id=ca.card_id
                WHERE c.game_id=:game AND COALESCE(ca.attributes_json->>'archetype','')<>''
                GROUP BY value ORDER BY cards DESC, value
                """,
                {"game": game_id},
                limit=75,
            ),
            "rarity": _distribution(
                session,
                """
                SELECT COALESCE(p.rarity,'<null>') AS value, COUNT(*) AS prints
                FROM prints p JOIN cards c ON c.id=p.card_id
                WHERE c.game_id=:game
                GROUP BY value ORDER BY prints DESC, value
                """,
                {"game": game_id},
                limit=150,
            ),
            "language": _distribution(
                session,
                """
                SELECT COALESCE(p.language,'<null>') AS value, COUNT(*) AS prints
                FROM prints p JOIN cards c ON c.id=p.card_id
                WHERE c.game_id=:game
                GROUP BY value ORDER BY prints DESC, value
                """,
                {"game": game_id},
                limit=30,
            ),
            "variant": _distribution(
                session,
                """
                SELECT p.variant AS value, COUNT(*) AS prints
                FROM prints p JOIN cards c ON c.id=p.card_id
                WHERE c.game_id=:game
                GROUP BY value ORDER BY prints DESC, value
                """,
                {"game": game_id},
                limit=150,
            ),
            "family_resolution": _distribution(
                session,
                """
                SELECT pa.attributes_json->>'family_resolution' AS value, COUNT(*) AS prints
                FROM print_attributes pa JOIN prints p ON p.id=pa.print_id JOIN cards c ON c.id=p.card_id
                WHERE c.game_id=:game
                GROUP BY value ORDER BY prints DESC, value
                """,
                {"game": game_id},
                limit=20,
            ),
            "release_year": _distribution(
                session,
                """
                SELECT EXTRACT(YEAR FROM release_date)::int AS year, COUNT(*) AS releases
                FROM catalog_releases
                WHERE game_id=:game AND release_date IS NOT NULL
                GROUP BY year ORDER BY year DESC
                """,
                {"game": game_id},
                limit=100,
            ),
            "top_set_families": _distribution(
                session,
                """
                SELECT s.code AS value, COUNT(*) AS prints
                FROM prints p JOIN cards c ON c.id=p.card_id JOIN sets s ON s.id=p.set_id
                WHERE c.game_id=:game
                GROUP BY s.code ORDER BY prints DESC, s.code
                """,
                {"game": game_id},
                limit=75,
            ),
        }

        print_unknown_rarity = _scalar(
            session,
            "SELECT COUNT(*) FROM prints p JOIN cards c ON c.id=p.card_id WHERE c.game_id=:game AND p.rarity='Unknown'",
            {"game": game_id},
        )
        language_non_en = _scalar(
            session,
            "SELECT COUNT(*) FROM prints p JOIN cards c ON c.id=p.card_id WHERE c.game_id=:game AND COALESCE(p.language,'')<>'en'",
            {"game": game_id},
        )
        database_bytes = _scalar(session, "SELECT pg_database_size(current_database())")

        session.rollback()

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": "read_only_yugioh_catalog_health_v2_post_commit",
        "status": "pass",
        "canonical_counts": counts,
        "identity_duplicates": duplicate_checks,
        "linkage": linkage,
        "edge_cases": {
            "fallback_rows": fallback_rows,
            "deduplicated_source_rows": deduplicated_source_rows,
            "noisy_rarity_source_rows": noisy_rarity_source_rows,
            "canonical_unknown_rarity_prints": print_unknown_rarity,
            "non_en_prints": language_non_en,
            "spell_of_mask": spell_of_mask,
            "quarantined_assignments_present": quarantined_present,
        },
        "search_v2_pre_index_state": search_state,
        "card_attribute_coverage": coverage,
        "card_attribute_coverage_pct": {
            key: round(value / counts["cards"] * 100, 2)
            for key, value in coverage.items()
            if key != "source_aliases_nonempty"
        },
        "artwork": {
            "total_candidates_embedded": total_artwork_candidates,
            "cards_with_candidates": coverage["cards_with_artwork_candidates"],
            "exact_print_art_mapping_claimed": False,
        },
        "distributions": distributions,
        "database": {
            "bytes": database_bytes,
            "mib": round(database_bytes / 1024 / 1024, 2),
            "limit_mib": 512,
            "remaining_mib": round((512 * 1024 * 1024 - database_bytes) / 1024 / 1024, 2),
        },
        "facet_readiness": {
            "identity": {
                "set": True,
                "collector_number": True,
                "release": True,
                "release_year": linkage["releases_with_date"] > 0,
                "language": True,
            },
            "card": {
                "card_type": coverage["category"] > 0,
                "frame_type": coverage["frame_type"] > 0,
                "attribute": coverage["attribute"] > 0,
                "race": coverage["race"] > 0,
                "archetype": coverage["archetype"] > 0,
                "level": coverage["level"] > 0,
                "rank": coverage["rank"] > 0,
                "atk": coverage["atk"] > 0,
                "def": coverage["def"] > 0,
                "pendulum_scale": coverage["scale"] > 0,
                "link_value": coverage["link_value"] > 0,
                "link_markers": coverage["link_markers_nonempty"] > 0,
                "banlist": coverage["banlist_info_nonempty"] > 0,
            },
            "collecting": {
                "rarity": True,
                "exact_variant": True,
                "finish": False,
                "edition": False,
                "exact_artwork": False,
            },
        },
        "database_writes": 0,
    }
    _write(report_path, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report-path", type=Path, default=None)
    args = parser.parse_args()
    run(report_path=args.report_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
