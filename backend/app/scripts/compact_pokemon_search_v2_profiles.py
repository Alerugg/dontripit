from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import text

from app import db


EXPECTED_CARDS = 21065
EXPECTED_PRINTS = 33757
EXPECTED_VARIANT_PRINTS = 27241

CARD_KEYS = (
    "category",
    "dex_id",
    "hp",
    "types",
    "evolve_from",
    "stage",
    "trainer_type",
    "energy_type",
)
PRINT_KEYS = CARD_KEYS + (
    "regulation_mark",
    "illustrator",
    "series",
    "release_year",
    "finish",
    "foil_pattern",
    "stamps",
    "variant_subtype",
    "release_context",
    "size",
    "variant_hash",
)


def _write(path: Path | None, payload: dict) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")


def _relation_sizes(session, game_id: int) -> dict:
    row = session.execute(text(
        """
        SELECT
          pg_total_relation_size('card_search_profiles')::bigint AS card_total,
          pg_relation_size('card_search_profiles')::bigint AS card_heap,
          pg_indexes_size('card_search_profiles')::bigint AS card_indexes,
          pg_total_relation_size('print_search_profiles')::bigint AS print_total,
          pg_relation_size('print_search_profiles')::bigint AS print_heap,
          pg_indexes_size('print_search_profiles')::bigint AS print_indexes,
          pg_database_size(current_database())::bigint AS database_bytes,
          (SELECT COUNT(*) FROM card_search_profiles WHERE game_id=:game) AS card_rows,
          (SELECT COUNT(*) FROM print_search_profiles WHERE game_id=:game) AS print_rows,
          (SELECT COUNT(*) FROM print_search_profiles WHERE game_id=:game AND attributes_json->>'finish' IS NOT NULL) AS variant_rows,
          (SELECT AVG(pg_column_size(attributes_json)) FROM card_search_profiles WHERE game_id=:game) AS avg_card_attr_bytes,
          (SELECT AVG(pg_column_size(search_text)) FROM card_search_profiles WHERE game_id=:game) AS avg_card_search_bytes,
          (SELECT AVG(pg_column_size(attributes_json)) FROM print_search_profiles WHERE game_id=:game) AS avg_print_attr_bytes,
          (SELECT AVG(pg_column_size(search_text)) FROM print_search_profiles WHERE game_id=:game) AS avg_print_search_bytes
        """
    ), {"game": game_id}).mappings().one()
    return {
        key: (float(value) if key.startswith("avg_") and value is not None else int(value or 0))
        for key, value in row.items()
    }


def _jsonb_build_expression(keys: tuple[str, ...]) -> str:
    args: list[str] = []
    for key in keys:
        args.extend([f"'{key}'", f"attributes_json -> '{key}'"])
    return "jsonb_strip_nulls(jsonb_build_object(" + ", ".join(args) + "))"


def _search_text_expression(extra_columns: list[str], keys: tuple[str, ...]) -> str:
    pieces = list(extra_columns)
    pieces.extend([f"attributes_json ->> '{key}'" for key in keys])
    concat = "concat_ws(' ', " + ", ".join(pieces) + ")"
    # Search documents are ASCII-normalized upstream. JSON arrays become simple
    # searchable token text after punctuation is collapsed here.
    return f"trim(regexp_replace(lower({concat}), '[^a-z0-9]+', ' ', 'g'))"


def _vacuum_full(table_name: str) -> None:
    with db.engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        connection.execute(text(f"VACUUM (FULL, ANALYZE) {table_name}"))


def run(*, report_path: Path | None = None) -> dict:
    db.init_engine()
    with db.SessionLocal() as session:
        game_id = int(session.execute(text("SELECT id FROM games WHERE slug='pokemon' LIMIT 1")).scalar_one())
        before = _relation_sizes(session, game_id)
        if before["card_rows"] != EXPECTED_CARDS:
            raise AssertionError(f"Card Search V2 scope moved: {before['card_rows']} != {EXPECTED_CARDS}")
        if before["print_rows"] != EXPECTED_PRINTS:
            raise AssertionError(f"Print Search V2 scope moved: {before['print_rows']} != {EXPECTED_PRINTS}")
        if before["variant_rows"] != EXPECTED_VARIANT_PRINTS:
            raise AssertionError(f"Physical variant scope moved: {before['variant_rows']} != {EXPECTED_VARIANT_PRINTS}")
        session.rollback()

    # Stage 1: compact Card projection, commit, then physically reclaim it before
    # touching Print profiles. This caps temporary storage on the 512 MB Neon DB.
    card_attrs = _jsonb_build_expression(CARD_KEYS)
    card_search = _search_text_expression(
        ["normalized_name", "COALESCE(aliases_json::text, '')"],
        CARD_KEYS,
    )
    with db.SessionLocal() as session:
        with session.begin():
            session.execute(text(
                """
                UPDATE card_search_profiles csp
                SET aliases_json = COALESCE((
                      SELECT jsonb_agg(value ORDER BY value)
                      FROM (
                        SELECT DISTINCT value
                        FROM jsonb_array_elements_text(COALESCE(csp.aliases_json, '[]'::jsonb)) AS value
                      ) dedup
                    ), '[]'::jsonb),
                    keywords_json = '[]'::jsonb,
                    updated_at = now()
                WHERE game_id=:game
                """
            ), {"game": game_id})
        with session.begin():
            result = session.execute(text(
                f"""
                UPDATE card_search_profiles
                SET attributes_json = {card_attrs},
                    search_text = {card_search},
                    updated_at = now()
                WHERE game_id=:game
                """
            ), {"game": game_id})
            card_updated = int(result.rowcount or 0)
    if card_updated != EXPECTED_CARDS:
        raise AssertionError(f"Card compaction updated {card_updated}, expected {EXPECTED_CARDS}")
    _vacuum_full("card_search_profiles")

    # Stage 2: same for exact physical Print search projection.
    print_attrs = _jsonb_build_expression(PRINT_KEYS)
    print_search = _search_text_expression(
        [
            "normalized_name",
            "COALESCE(normalized_set_code, '')",
            "COALESCE(normalized_collector_number, '')",
            "COALESCE(language, '')",
            "COALESCE(rarity, '')",
            "COALESCE(exact_variant, '')",
            "COALESCE(variant_family, '')",
            "COALESCE(aliases_json::text, '')",
            "COALESCE(release_names_json::text, '')",
        ],
        PRINT_KEYS,
    )
    with db.SessionLocal() as session:
        with session.begin():
            session.execute(text(
                """
                UPDATE print_search_profiles psp
                SET aliases_json = COALESCE((
                      SELECT jsonb_agg(value ORDER BY value)
                      FROM (
                        SELECT DISTINCT value
                        FROM jsonb_array_elements_text(COALESCE(psp.aliases_json, '[]'::jsonb)) AS value
                      ) dedup
                    ), '[]'::jsonb),
                    keywords_json = '[]'::jsonb,
                    updated_at = now()
                WHERE game_id=:game
                """
            ), {"game": game_id})
        with session.begin():
            result = session.execute(text(
                f"""
                UPDATE print_search_profiles
                SET attributes_json = {print_attrs},
                    search_text = {print_search},
                    updated_at = now()
                WHERE game_id=:game
                """
            ), {"game": game_id})
            print_updated = int(result.rowcount or 0)
    if print_updated != EXPECTED_PRINTS:
        raise AssertionError(f"Print compaction updated {print_updated}, expected {EXPECTED_PRINTS}")
    _vacuum_full("print_search_profiles")

    with db.SessionLocal() as session:
        after = _relation_sizes(session, game_id)
        required = dict(session.execute(text(
            """
            SELECT
              COUNT(*) FILTER (WHERE attributes_json->>'category' IS NOT NULL) AS category_rows,
              COUNT(*) FILTER (WHERE attributes_json->>'finish' IS NOT NULL) AS finish_rows,
              COUNT(*) FILTER (WHERE attributes_json->'types' IS NOT NULL) AS type_rows,
              COUNT(*) FILTER (WHERE attributes_json->>'illustrator' IS NOT NULL) AS illustrator_rows,
              COUNT(*) FILTER (WHERE attributes_json->>'regulation_mark' IS NOT NULL) AS regulation_rows
            FROM print_search_profiles
            WHERE game_id=:game
            """
        ), {"game": game_id}).mappings().one())
        session.rollback()

    if after["card_rows"] != EXPECTED_CARDS or after["print_rows"] != EXPECTED_PRINTS:
        raise AssertionError("Search V2 row counts changed during compaction")
    if after["variant_rows"] != EXPECTED_VARIANT_PRINTS:
        raise AssertionError("Physical finish coverage changed during compaction")
    if int(required["finish_rows"] or 0) != EXPECTED_VARIANT_PRINTS:
        raise AssertionError("Finish facet coverage was lost during compaction")

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": "lean_derived_search_projection_compaction",
        "game": "pokemon",
        "canonical_tables_modified": False,
        "rows_updated": {"cards": card_updated, "prints": print_updated},
        "before": before,
        "after": after,
        "required_field_coverage": {key: int(value or 0) for key, value in required.items()},
        "saved_bytes": max(0, before["database_bytes"] - after["database_bytes"]),
        "saved_mb": round(max(0, before["database_bytes"] - after["database_bytes"]) / 1024 / 1024, 2),
        "database_mb_after": round(after["database_bytes"] / 1024 / 1024, 2),
        "status": "pass",
    }
    _write(report_path, report)
    print(json.dumps(report, indent=2, ensure_ascii=False, default=str))
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report-path", type=Path)
    args = parser.parse_args()
    run(report_path=args.report_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
