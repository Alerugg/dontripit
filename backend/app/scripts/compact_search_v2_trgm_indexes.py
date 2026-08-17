from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import text

from app import db


TARGET_INDEXES = (
    "ix_print_search_profiles_text_trgm",
    "ix_print_search_profiles_name_trgm",
    "ix_card_search_profiles_name_trgm",
)
NEON_CEILING_MIB = 512.0
MAX_START_MIB = 470.0
MIN_PEAK_RESERVE_MIB = 8.0


def _write(path: Path | None, payload: dict) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def _snapshot() -> dict:
    with db.SessionLocal() as session:
        database_bytes = int(session.execute(text("SELECT pg_database_size(current_database())")).scalar_one())
        index_rows = [dict(row) for row in session.execute(text(
            """
            SELECT
              c.relname AS index_name,
              pg_relation_size(c.oid) AS bytes,
              i.indisvalid AS valid,
              i.indisready AS ready
            FROM pg_class c
            JOIN pg_index i ON i.indexrelid=c.oid
            JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname='public'
              AND c.relname = ANY(:names)
            ORDER BY c.relname
            """
        ), {"names": list(TARGET_INDEXES)}).mappings().all()]
        counts = dict(session.execute(text(
            """
            SELECT
              (SELECT COUNT(*) FROM card_search_profiles) AS card_profiles,
              (SELECT COUNT(*) FROM print_search_profiles) AS print_profiles,
              (SELECT COUNT(*) FROM facet_definitions) AS facets,
              (SELECT COUNT(*) FROM card_search_profiles csp JOIN games g ON g.id=csp.game_id WHERE g.slug='yugioh') AS yugioh_card_profiles,
              (SELECT COUNT(*) FROM print_search_profiles psp JOIN games g ON g.id=psp.game_id WHERE g.slug='yugioh') AS yugioh_print_profiles,
              (SELECT COUNT(*) FROM facet_definitions fd JOIN games g ON g.id=fd.game_id WHERE g.slug='yugioh') AS yugioh_facets,
              (SELECT COUNT(*) FROM card_search_profiles csp JOIN games g ON g.id=csp.game_id WHERE g.slug='pokemon') AS pokemon_card_profiles,
              (SELECT COUNT(*) FROM print_search_profiles psp JOIN games g ON g.id=psp.game_id WHERE g.slug='pokemon') AS pokemon_print_profiles
            """
        )).mappings().one())
        session.rollback()
    return {
        "database_bytes": database_bytes,
        "database_mib": round(database_bytes / 1024 / 1024, 2),
        "indexes": {
            row["index_name"]: {
                "bytes": int(row["bytes"]),
                "mib": round(int(row["bytes"]) / 1024 / 1024, 2),
                "valid": bool(row["valid"]),
                "ready": bool(row["ready"]),
            }
            for row in index_rows
        },
        "counts": {key: int(value or 0) for key, value in counts.items()},
    }


def _run_autocommit(sql: str) -> None:
    # PostgreSQL maintenance commands such as REINDEX ... CONCURRENTLY cannot
    # run inside an explicit transaction block. Use SQLAlchemy's supported
    # AUTOCOMMIT isolation mode rather than mutating the pooled wrapper object.
    with db.engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        connection.exec_driver_sql(sql)


def run(*, report_path: Path | None = None) -> dict:
    db.init_engine()
    before = _snapshot()

    missing = [name for name in TARGET_INDEXES if name not in before["indexes"]]
    if missing:
        raise AssertionError(f"Search V2 indexes missing before compaction: {missing}")
    invalid = [name for name, row in before["indexes"].items() if not row["valid"] or not row["ready"]]
    if invalid:
        raise AssertionError(f"Search V2 indexes are not healthy before compaction: {invalid}")
    if before["database_mib"] > MAX_START_MIB:
        raise AssertionError(
            f"Refusing concurrent reindex: database is {before['database_mib']} MiB, above {MAX_START_MIB} MiB preflight ceiling"
        )

    largest_index_mib = max(row["mib"] for row in before["indexes"].values())
    estimated_peak_mib = before["database_mib"] + largest_index_mib
    if estimated_peak_mib > NEON_CEILING_MIB - MIN_PEAK_RESERVE_MIB:
        raise AssertionError(
            "Refusing concurrent reindex: estimated one-index peak "
            f"{estimated_peak_mib:.2f} MiB leaves less than {MIN_PEAK_RESERVE_MIB:.0f} MiB reserve"
        )

    completed: list[str] = []
    for index_name in TARGET_INDEXES:
        # Names are a fixed internal allow-list, never user input.
        _run_autocommit(f'REINDEX INDEX CONCURRENTLY public."{index_name}"')
        completed.append(index_name)

    _run_autocommit("ANALYZE public.card_search_profiles")
    _run_autocommit("ANALYZE public.print_search_profiles")

    after = _snapshot()
    if after["counts"] != before["counts"]:
        raise AssertionError(f"Search V2 row counts changed during index compaction: before={before['counts']} after={after['counts']}")
    missing_after = [name for name in TARGET_INDEXES if name not in after["indexes"]]
    if missing_after:
        raise AssertionError(f"Search V2 indexes missing after compaction: {missing_after}")
    invalid_after = [name for name, row in after["indexes"].items() if not row["valid"] or not row["ready"]]
    if invalid_after:
        raise AssertionError(f"Search V2 indexes are not healthy after compaction: {invalid_after}")

    freed_bytes = before["database_bytes"] - after["database_bytes"]
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": "concurrent_search_v2_trigram_index_compaction",
        "status": "pass",
        "targets": list(TARGET_INDEXES),
        "completed": completed,
        "preflight": {
            "neon_ceiling_mib": NEON_CEILING_MIB,
            "max_start_mib": MAX_START_MIB,
            "largest_target_index_mib": largest_index_mib,
            "estimated_one_index_peak_mib": round(estimated_peak_mib, 2),
            "minimum_peak_reserve_mib": MIN_PEAK_RESERVE_MIB,
        },
        "before": before,
        "after": after,
        "freed_bytes": freed_bytes,
        "freed_mib": round(freed_bytes / 1024 / 1024, 2),
        "canonical_data_writes": 0,
        "search_profile_row_writes": 0,
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
