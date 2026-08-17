from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import text

from app import db


TABLES = ("sets", "cards", "prints", "print_images", "print_identifiers", "source_records")
EXTERNAL_COLUMNS = {
    "sets": ("tcgdex_id", "yugioh_id", "riftbound_id"),
    "cards": ("oracle_id", "tcgdex_id", "yugoprodeck_id", "riftbound_id", "card_key"),
    "prints": ("scryfall_id", "tcgdex_id", "yugioh_id", "riftbound_id", "print_key"),
}


def _write(path: Path | None, payload: dict) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def run(*, report_path: Path | None = None) -> dict:
    db.init_engine()
    with db.SessionLocal() as session:
        database_bytes = int(session.execute(text("SELECT pg_database_size(current_database())")).scalar_one())
        tables: dict[str, dict] = {}
        indexes: list[dict] = []
        null_profiles: dict[str, dict] = {}

        for table in TABLES:
            exists = bool(session.execute(text("SELECT to_regclass(:name) IS NOT NULL"), {"name": f"public.{table}"}).scalar_one())
            if not exists:
                continue
            row = dict(session.execute(text("""
                SELECT
                  pg_total_relation_size(:table)::bigint AS total_bytes,
                  pg_relation_size(:table)::bigint AS heap_bytes,
                  pg_indexes_size(:table)::bigint AS index_bytes
            """), {"table": table}).mappings().one())
            count = int(session.execute(text(f'SELECT COUNT(*) FROM "{table}"')).scalar_one() or 0)
            tables[table] = {
                "rows": count,
                "total_bytes": int(row["total_bytes"] or 0),
                "heap_bytes": int(row["heap_bytes"] or 0),
                "index_bytes": int(row["index_bytes"] or 0),
                "total_mib": round(int(row["total_bytes"] or 0) / 1024 / 1024, 2),
                "heap_mib": round(int(row["heap_bytes"] or 0) / 1024 / 1024, 2),
                "index_mib": round(int(row["index_bytes"] or 0) / 1024 / 1024, 2),
            }

            rows = [dict(r) for r in session.execute(text("""
                SELECT
                  i.indexrelname AS index_name,
                  pg_relation_size(i.indexrelid)::bigint AS bytes,
                  COALESCE(i.idx_scan,0)::bigint AS scans,
                  x.indisunique AS is_unique,
                  x.indisprimary AS is_primary,
                  x.indisvalid AS is_valid,
                  x.indisready AS is_ready,
                  pg_get_indexdef(i.indexrelid) AS definition,
                  COALESCE(c.reltuples,0)::bigint AS estimated_entries
                FROM pg_stat_user_indexes i
                JOIN pg_index x ON x.indexrelid=i.indexrelid
                JOIN pg_class c ON c.oid=i.indexrelid
                WHERE i.schemaname='public' AND i.relname=:table
                ORDER BY pg_relation_size(i.indexrelid) DESC, i.indexrelname
            """), {"table": table}).mappings().all()]
            for item in rows:
                item["table"] = table
                item["bytes"] = int(item["bytes"] or 0)
                item["mib"] = round(item["bytes"] / 1024 / 1024, 3)
                item["scans"] = int(item["scans"] or 0)
                item["estimated_entries"] = int(item["estimated_entries"] or 0)
                item["is_unique"] = bool(item["is_unique"])
                item["is_primary"] = bool(item["is_primary"])
                item["is_valid"] = bool(item["is_valid"])
                item["is_ready"] = bool(item["is_ready"])
                indexes.append(item)

        for table, columns in EXTERNAL_COLUMNS.items():
            if table not in tables:
                continue
            profile = {}
            total = tables[table]["rows"]
            for column in columns:
                row = dict(session.execute(text(
                    f'SELECT COUNT(*) FILTER (WHERE "{column}" IS NOT NULL) AS nonnull, '
                    f'COUNT(*) FILTER (WHERE "{column}" IS NULL) AS nulls, '
                    f'COUNT(DISTINCT "{column}") FILTER (WHERE "{column}" IS NOT NULL) AS distinct_nonnull '
                    f'FROM "{table}"'
                )).mappings().one())
                nonnull = int(row["nonnull"] or 0)
                profile[column] = {
                    "nonnull": nonnull,
                    "nulls": int(row["nulls"] or 0),
                    "distinct_nonnull": int(row["distinct_nonnull"] or 0),
                    "nonnull_pct": round(100.0 * nonnull / total, 3) if total else 0.0,
                }
            null_profiles[table] = profile

        constraints = [dict(row) for row in session.execute(text("""
            SELECT
              tc.table_name,
              tc.constraint_name,
              tc.constraint_type,
              pg_get_constraintdef(pc.oid) AS definition
            FROM information_schema.table_constraints tc
            JOIN pg_constraint pc ON pc.conname=tc.constraint_name
            JOIN pg_namespace pn ON pn.oid=pc.connamespace AND pn.nspname=tc.constraint_schema
            WHERE tc.table_schema='public'
              AND tc.table_name = ANY(:tables)
              AND tc.constraint_type IN ('UNIQUE','PRIMARY KEY')
            ORDER BY tc.table_name, tc.constraint_type, tc.constraint_name
        """), {"tables": list(TABLES)}).mappings().all()]
        session.rollback()

    external_index_candidates = []
    for row in indexes:
        definition = str(row["definition"] or "")
        matched_columns = []
        for column in EXTERNAL_COLUMNS.get(row["table"], ()):
            if column in definition:
                matched_columns.append(column)
        if not matched_columns:
            continue
        external_index_candidates.append({
            "table": row["table"],
            "index_name": row["index_name"],
            "mib": row["mib"],
            "scans": row["scans"],
            "is_unique": row["is_unique"],
            "definition": definition,
            "matched_columns": matched_columns,
            "column_density": {column: null_profiles[row["table"]][column] for column in matched_columns},
            "partial_index_opportunity": any(null_profiles[row["table"]][column]["nonnull_pct"] < 70.0 for column in matched_columns),
        })

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": "read_only_core_catalog_index_storage_audit",
        "status": "pass",
        "database_mib": round(database_bytes / 1024 / 1024, 2),
        "tables": tables,
        "indexes": indexes,
        "external_id_null_profiles": null_profiles,
        "unique_constraints": constraints,
        "external_index_candidates": external_index_candidates,
        "external_candidate_index_mib": round(sum(float(row["mib"]) for row in external_index_candidates), 3),
        "database_writes": 0,
        "decision_rule": "Do not alter uniqueness/index design until each candidate is classified as redundant, safely replaceable by a partial unique index, or required cross-dialect behavior. Reindex-only compaction must be separate from semantic index migration.",
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
