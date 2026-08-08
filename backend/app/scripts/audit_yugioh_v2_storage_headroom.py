from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import text

from app import db


NEON_LIMIT_BYTES = 512 * 1024 * 1024

# Frozen canonical snapshot run 31265827097. Evidence-only files that are NOT
# inserted as separate permanent tables (artwork_candidates/source_conflicts) are excluded.
SNAPSHOT = {
    "sets": {"rows": 646, "jsonl_bytes": 117858},
    "cards": {"rows": 14479, "jsonl_bytes": 1951841},
    "card_attributes": {"rows": 14479, "jsonl_bytes": 19056724},
    "prints": {"rows": 44226, "jsonl_bytes": 17126991},
    "print_attributes": {"rows": 44226, "jsonl_bytes": 36979072},
    "print_images": {"rows": 44226, "jsonl_bytes": 9061530},
    "catalog_releases": {"rows": 1032, "jsonl_bytes": 283386},
    "print_releases": {"rows": 44226, "jsonl_bytes": 15109426},
}

FROZEN_PERSISTED_JSONL_BYTES = sum(row["jsonl_bytes"] for row in SNAPSHOT.values())


def _write(path: Path | None, payload: dict) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def _exists(session, table_name: str) -> bool:
    return bool(
        session.execute(
            text("SELECT to_regclass(:name) IS NOT NULL"),
            {"name": f"public.{table_name}"},
        ).scalar_one()
    )


def _relation_stats(session, table_name: str) -> dict:
    if not _exists(session, table_name):
        return {
            "exists": False,
            "rows": 0,
            "heap_bytes": 0,
            "index_bytes": 0,
            "total_bytes": 0,
        }
    row = session.execute(
        text(
            """
            SELECT
              (SELECT COUNT(*) FROM """ + table_name + """") AS rows,
              pg_relation_size(:regclass) AS heap_bytes,
              pg_indexes_size(:regclass) AS index_bytes,
              pg_total_relation_size(:regclass) AS total_bytes
            """
        ),
        {"regclass": table_name},
    ).mappings().one()
    return {
        "exists": True,
        "rows": int(row["rows"] or 0),
        "heap_bytes": int(row["heap_bytes"] or 0),
        "index_bytes": int(row["index_bytes"] or 0),
        "total_bytes": int(row["total_bytes"] or 0),
    }


def _ygo_counts(session, game_id: int) -> dict:
    queries = {
        "sets": "SELECT COUNT(*) FROM sets WHERE game_id=:game",
        "cards": "SELECT COUNT(*) FROM cards WHERE game_id=:game",
        "prints": "SELECT COUNT(*) FROM prints p JOIN cards c ON c.id=p.card_id WHERE c.game_id=:game",
        "print_images": "SELECT COUNT(*) FROM print_images pi JOIN prints p ON p.id=pi.print_id JOIN cards c ON c.id=p.card_id WHERE c.game_id=:game",
        "card_attributes": "SELECT COUNT(*) FROM card_attributes ca JOIN cards c ON c.id=ca.card_id WHERE c.game_id=:game",
        "print_attributes": "SELECT COUNT(*) FROM print_attributes pa JOIN prints p ON p.id=pa.print_id JOIN cards c ON c.id=p.card_id WHERE c.game_id=:game",
        "catalog_releases": "SELECT COUNT(*) FROM catalog_releases WHERE game_id=:game",
        "print_releases": "SELECT COUNT(*) FROM print_releases pr JOIN prints p ON p.id=pr.print_id JOIN cards c ON c.id=p.card_id WHERE c.game_id=:game",
        "search_documents": "SELECT COUNT(*) FROM search_documents WHERE game_id=:game",
        "card_search_profiles": "SELECT COUNT(*) FROM card_search_profiles WHERE game_id=:game",
        "print_search_profiles": "SELECT COUNT(*) FROM print_search_profiles WHERE game_id=:game",
    }
    output = {}
    for table_name, sql in queries.items():
        if not _exists(session, table_name):
            output[table_name] = 0
            continue
        output[table_name] = int(session.execute(text(sql), {"game": game_id}).scalar_one() or 0)
    return output


def run(*, report_path: Path | None = None) -> dict:
    db.init_engine()
    with db.SessionLocal() as session:
        game_id = session.execute(
            text("SELECT id FROM games WHERE slug='yugioh' LIMIT 1")
        ).scalar_one()
        game_id = int(game_id)
        current_database_bytes = int(
            session.execute(text("SELECT pg_database_size(current_database())")).scalar_one()
        )

        tables = sorted(set(SNAPSHOT) | {"search_documents", "card_search_profiles", "print_search_profiles"})
        relation_stats = {name: _relation_stats(session, name) for name in tables}
        ygo_counts = _ygo_counts(session, game_id)
        session.rollback()

    # JSONL repeats field names, so it is not a DB-size estimate by itself. We use
    # multiple deliberately conservative multipliers as a peak envelope. Peak does
    # NOT subtract legacy YGO because old row versions remain during an atomic replace.
    scenarios = {}
    for label, multiplier in (
        ("payload_only_1_00x", 1.00),
        ("conservative_1_50x", 1.50),
        ("stress_1_75x", 1.75),
        ("extreme_2_00x", 2.00),
    ):
        estimated_increment = int(FROZEN_PERSISTED_JSONL_BYTES * multiplier)
        projected_peak = current_database_bytes + estimated_increment
        scenarios[label] = {
            "multiplier": multiplier,
            "estimated_increment_bytes": estimated_increment,
            "projected_peak_bytes": projected_peak,
            "projected_peak_mib": round(projected_peak / 1024 / 1024, 2),
            "remaining_to_512_mib": round((NEON_LIMIT_BYTES - projected_peak) / 1024 / 1024, 2),
            "within_512_mib": projected_peak < NEON_LIMIT_BYTES,
        }

    current_mib = current_database_bytes / 1024 / 1024
    # Gate: even the 1.75x stress envelope must fit. 2x is reported as an additional
    # warning signal but is intentionally not the hard gate because JSONL field names
    # make it materially more verbose than PostgreSQL rows.
    stress = scenarios["stress_1_75x"]
    hard_pass = bool(stress["within_512_mib"])
    caution = stress["projected_peak_mib"] >= 470

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": "read_only_yugioh_v2_storage_headroom",
        "status": "pass" if hard_pass else "fail",
        "caution": caution,
        "neon_limit_mib": 512,
        "current_database_bytes": current_database_bytes,
        "current_database_mib": round(current_mib, 2),
        "current_utilization_pct": round(current_database_bytes / NEON_LIMIT_BYTES * 100, 2),
        "frozen_persisted_snapshot_jsonl_bytes": FROZEN_PERSISTED_JSONL_BYTES,
        "frozen_persisted_snapshot_jsonl_mib": round(FROZEN_PERSISTED_JSONL_BYTES / 1024 / 1024, 2),
        "snapshot_target_rows": SNAPSHOT,
        "current_yugioh_rows": ygo_counts,
        "relation_stats": relation_stats,
        "peak_scenarios": scenarios,
        "hard_gate": "stress_1_75x projected peak must remain below 512 MiB",
        "replacement_design": {
            "database_staging_copy": False,
            "reason": "validated external artifact will be streamed directly into permanent canonical tables inside one transaction; avoid a 100MB in-DB staging duplicate",
            "legacy_rows_subtracted_from_peak": False,
            "reason_legacy": "MVCC retains old versions during rollback-safe transaction; peak estimate must not pretend they are already reclaimed",
        },
        "database_writes": 0,
    }
    _write(report_path, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not hard_pass:
        raise AssertionError("Yu-Gi-Oh V2 storage headroom gate failed")
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report-path", type=Path, default=None)
    args = parser.parse_args()
    run(report_path=args.report_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
