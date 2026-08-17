from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import text

from app import db
from app.search_v2.yugioh_facets import yugioh_facets
from app.search_v2.yugioh_profiles import (
    iter_yugioh_card_profiles,
    iter_yugioh_print_profiles,
)


EXPECTED_CARDS = 14479
EXPECTED_PRINTS = 44226
NEON_LIMIT_BYTES = 512 * 1024 * 1024
PREFERRED_CEILING_BYTES = 470 * 1024 * 1024
HARD_CEILING_BYTES = 480 * 1024 * 1024
SAMPLE_ROWS = 1200


def _write_jsonl(path: Path, rows) -> tuple[int, int]:
    count = 0
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(
                json.dumps(row, ensure_ascii=False, separators=(",", ":"), default=str)
                + "\n"
            )
            count += 1
    return count, path.stat().st_size


def _sample_jsonl(path: Path, *, total_rows: int, max_rows: int = SAMPLE_ROWS) -> list[dict]:
    if total_rows <= max_rows:
        step = 1
    else:
        step = max(1, total_rows // max_rows)
    sample = []
    with path.open("r", encoding="utf-8") as handle:
        for index, line in enumerate(handle):
            if index % step != 0:
                continue
            sample.append(json.loads(line))
            if len(sample) >= max_rows:
                break
    if not sample:
        raise AssertionError(f"No profile rows sampled from {path}")
    return sample


def _relation_size(session, table_name: str) -> dict:
    row = session.execute(
        text(
            """
            SELECT
              (SELECT COUNT(*) FROM """
            + table_name
            + """") AS rows,
              pg_relation_size(:name) AS heap_bytes,
              pg_indexes_size(:name) AS index_bytes,
              pg_total_relation_size(:name) AS total_bytes
            """
        ),
        {"name": table_name},
    ).mappings().one()
    return {key: int(value or 0) for key, value in dict(row).items()}


def _sample_card_pg_row_bytes(session, *, game_id: int, rows: list[dict]) -> float:
    payload = json.dumps(rows, ensure_ascii=False, separators=(",", ":"))
    value = session.execute(
        text(
            """
            SELECT AVG(
              pg_column_size(
                ROW(
                  x.card_id,
                  CAST(:game_id AS integer),
                  x.normalized_name,
                  x.aliases_json,
                  x.keywords_json,
                  x.attributes_json,
                  x.search_text
                )
              )
            )
            FROM jsonb_to_recordset(CAST(:payload AS jsonb)) AS x(
              card_id bigint,
              normalized_name text,
              aliases_json jsonb,
              keywords_json jsonb,
              attributes_json jsonb,
              search_text text
            )
            """
        ),
        {"game_id": game_id, "payload": payload},
    ).scalar_one()
    return float(value or 0.0)


def _sample_print_pg_row_bytes(session, *, game_id: int, rows: list[dict]) -> float:
    payload = json.dumps(rows, ensure_ascii=False, separators=(",", ":"))
    value = session.execute(
        text(
            """
            SELECT AVG(
              pg_column_size(
                ROW(
                  x.print_id,
                  x.card_id,
                  CAST(:game_id AS integer),
                  x.normalized_name,
                  x.normalized_set_code,
                  x.normalized_collector_number,
                  x.language,
                  x.rarity,
                  x.exact_variant,
                  x.variant_family,
                  x.release_names_json,
                  x.aliases_json,
                  x.keywords_json,
                  x.attributes_json,
                  x.search_text
                )
              )
            )
            FROM jsonb_to_recordset(CAST(:payload AS jsonb)) AS x(
              print_id bigint,
              card_id bigint,
              normalized_name text,
              normalized_set_code text,
              normalized_collector_number text,
              language text,
              rarity text,
              exact_variant text,
              variant_family text,
              release_names_json jsonb,
              aliases_json jsonb,
              keywords_json jsonb,
              attributes_json jsonb,
              search_text text
            )
            """
        ),
        {"game_id": game_id, "payload": payload},
    ).scalar_one()
    return float(value or 0.0)


def _write(path: Path | None, payload: dict) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )


def run(*, output_dir: Path, report_path: Path | None = None) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    db.init_engine()
    with db.SessionLocal() as session:
        game_id = int(
            session.execute(
                text("SELECT id FROM games WHERE slug='yugioh' LIMIT 1")
            ).scalar_one()
        )
        ygo_search_rows = {
            "card_search_profiles": int(
                session.execute(
                    text("SELECT COUNT(*) FROM card_search_profiles WHERE game_id=:game"),
                    {"game": game_id},
                ).scalar_one()
                or 0
            ),
            "print_search_profiles": int(
                session.execute(
                    text("SELECT COUNT(*) FROM print_search_profiles WHERE game_id=:game"),
                    {"game": game_id},
                ).scalar_one()
                or 0
            ),
            "facet_definitions": int(
                session.execute(
                    text("SELECT COUNT(*) FROM facet_definitions WHERE game_id=:game"),
                    {"game": game_id},
                ).scalar_one()
                or 0
            ),
        }
        if any(ygo_search_rows.values()):
            raise AssertionError(
                f"YGO Search V2 must be empty before estimator: {ygo_search_rows}"
            )

        database_bytes = int(
            session.execute(text("SELECT pg_database_size(current_database())")).scalar_one()
        )
        current_card_relation = _relation_size(session, "card_search_profiles")
        current_print_relation = _relation_size(session, "print_search_profiles")

        card_path = output_dir / "card_search_profiles.jsonl"
        print_path = output_dir / "print_search_profiles.jsonl"
        card_count, card_jsonl_bytes = _write_jsonl(
            card_path, iter_yugioh_card_profiles(session)
        )
        print_count, print_jsonl_bytes = _write_jsonl(
            print_path, iter_yugioh_print_profiles(session)
        )

        if card_count != EXPECTED_CARDS:
            raise AssertionError(
                f"YGO card profile count={card_count} expected={EXPECTED_CARDS}"
            )
        if print_count != EXPECTED_PRINTS:
            raise AssertionError(
                f"YGO print profile count={print_count} expected={EXPECTED_PRINTS}"
            )

        card_sample = _sample_jsonl(card_path, total_rows=card_count)
        print_sample = _sample_jsonl(print_path, total_rows=print_count)
        card_avg_pg_row_bytes = _sample_card_pg_row_bytes(
            session, game_id=game_id, rows=card_sample
        )
        print_avg_pg_row_bytes = _sample_print_pg_row_bytes(
            session, game_id=game_id, rows=print_sample
        )
        session.rollback()

    facets = yugioh_facets()
    active_facets = [row for row in facets if bool(row.get("active", True))]
    facet_path = output_dir / "facet_definitions.json"
    facet_path.write_text(
        json.dumps(facets, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    facet_bytes = facet_path.stat().st_size

    raw_profile_bytes = card_jsonl_bytes + print_jsonl_bytes + facet_bytes
    raw_payload_safety_increment = int(raw_profile_bytes * 1.25)

    # Heap: measured on actual YGO profile values using PostgreSQL's own binary-row
    # sizing without inserting a row. Add 30% for tuple/page/TOAST variance.
    estimated_card_heap = int(card_avg_pg_row_bytes * card_count * 1.30)
    estimated_print_heap = int(print_avg_pg_row_bytes * print_count * 1.30)

    # Indexes: same table/index definitions already exist. Extrapolate measured index
    # bytes per current row, then add 15% for YGO key-length distribution variance.
    card_index_per_row = (
        current_card_relation["index_bytes"] / current_card_relation["rows"]
        if current_card_relation["rows"]
        else 350.0
    )
    print_index_per_row = (
        current_print_relation["index_bytes"] / current_print_relation["rows"]
        if current_print_relation["rows"]
        else 700.0
    )
    estimated_card_indexes = int(card_index_per_row * card_count * 1.15)
    estimated_print_indexes = int(print_index_per_row * print_count * 1.15)
    measured_relation_increment = (
        estimated_card_heap
        + estimated_print_heap
        + estimated_card_indexes
        + estimated_print_indexes
        + max(facet_bytes * 4, 64 * 1024)
    )

    conservative_increment = max(raw_payload_safety_increment, measured_relation_increment)
    projected_database_bytes = database_bytes + conservative_increment
    hard_pass = projected_database_bytes < HARD_CEILING_BYTES
    preferred_pass = projected_database_bytes < PREFERRED_CEILING_BYTES

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": "read_only_yugioh_search_v2_size_estimate_pg_measured",
        "status": "pass" if hard_pass else "fail",
        "preferred_margin": "pass" if preferred_pass else "caution",
        "database_before": {
            "bytes": database_bytes,
            "mib": round(database_bytes / 1024 / 1024, 2),
        },
        "generated_profiles": {
            "cards": card_count,
            "prints": print_count,
            "facets_total": len(facets),
            "facets_active": len(active_facets),
            "card_jsonl_bytes": card_jsonl_bytes,
            "print_jsonl_bytes": print_jsonl_bytes,
            "facet_json_bytes": facet_bytes,
            "total_raw_bytes": raw_profile_bytes,
            "total_raw_mib": round(raw_profile_bytes / 1024 / 1024, 2),
        },
        "postgres_sample": {
            "card_rows_sampled": len(card_sample),
            "print_rows_sampled": len(print_sample),
            "avg_card_binary_row_bytes": round(card_avg_pg_row_bytes, 2),
            "avg_print_binary_row_bytes": round(print_avg_pg_row_bytes, 2),
            "estimated_card_heap_with_30pct_margin_mib": round(
                estimated_card_heap / 1024 / 1024, 2
            ),
            "estimated_print_heap_with_30pct_margin_mib": round(
                estimated_print_heap / 1024 / 1024, 2
            ),
        },
        "existing_search_relations": {
            "card_search_profiles": current_card_relation,
            "print_search_profiles": current_print_relation,
            "card_index_bytes_per_existing_row": round(card_index_per_row, 2),
            "print_index_bytes_per_existing_row": round(print_index_per_row, 2),
        },
        "estimate": {
            "raw_jsonl_1_25x_increment_mib": round(
                raw_payload_safety_increment / 1024 / 1024, 2
            ),
            "measured_heap_plus_index_increment_mib": round(
                measured_relation_increment / 1024 / 1024, 2
            ),
            "chosen_conservative_increment_mib": round(
                conservative_increment / 1024 / 1024, 2
            ),
            "projected_database_mib": round(
                projected_database_bytes / 1024 / 1024, 2
            ),
            "remaining_to_512_mib": round(
                (NEON_LIMIT_BYTES - projected_database_bytes) / 1024 / 1024, 2
            ),
            "preferred_ceiling_mib": 470,
            "hard_ceiling_mib": 480,
        },
        "index_design": {
            "card_profile_duplicates_rich_description": False,
            "print_profile_duplicates_card_attributes": False,
            "print_alias_arrays_redundantly_populated": False,
            "print_keyword_arrays_redundantly_populated": False,
            "print_attributes_only": ["release_year"],
            "print_release_names": "one source-backed commercial release name per Print",
            "banlist_facet_active": False,
            "finish_facet_present": False,
            "edition_facet_present": False,
            "exact_artwork_facet_present": False,
        },
        "database_writes": 0,
    }
    _write(report_path, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not hard_pass:
        raise AssertionError(
            "YGO Search V2 measured conservative estimate exceeds 480 MiB hard ceiling"
        )
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--report-path", type=Path, default=None)
    args = parser.parse_args()
    run(output_dir=args.output_dir, report_path=args.report_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
