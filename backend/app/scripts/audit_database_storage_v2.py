from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import text

from app import db


def _write(path: Path | None, payload: dict) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")


def run(*, report_path: Path | None = None) -> dict:
    db.init_engine()
    with db.SessionLocal() as session:
        database = session.execute(text("SELECT current_database()" )).scalar_one()
        database_size = int(session.execute(text("SELECT pg_database_size(current_database())")).scalar_one())
        max_cluster_size = session.execute(text("SELECT current_setting('neon.max_cluster_size', true)")).scalar_one_or_none()

        tables = [dict(row) for row in session.execute(text(
            """
            SELECT
              schemaname,
              relname,
              pg_total_relation_size(relid)::bigint AS total_bytes,
              pg_relation_size(relid)::bigint AS heap_bytes,
              pg_indexes_size(relid)::bigint AS index_bytes,
              COALESCE(n_live_tup, 0)::bigint AS live_rows_estimate,
              COALESCE(n_dead_tup, 0)::bigint AS dead_rows_estimate
            FROM pg_stat_user_tables
            ORDER BY pg_total_relation_size(relid) DESC, relname ASC
            """
        )).mappings().all()]

        indexes = [dict(row) for row in session.execute(text(
            """
            SELECT
              schemaname,
              relname AS table_name,
              indexrelname AS index_name,
              pg_relation_size(indexrelid)::bigint AS bytes,
              idx_scan::bigint AS scans
            FROM pg_stat_user_indexes
            ORDER BY pg_relation_size(indexrelid) DESC, indexrelname ASC
            """
        )).mappings().all()]

        pokemon_counts = dict(session.execute(text(
            """
            SELECT
              (SELECT COUNT(*) FROM cards c JOIN games g ON g.id=c.game_id WHERE g.slug='pokemon') AS cards,
              (SELECT COUNT(*) FROM prints p JOIN cards c ON c.id=p.card_id JOIN games g ON g.id=c.game_id WHERE g.slug='pokemon') AS prints,
              (SELECT COUNT(*) FROM card_attributes ca JOIN cards c ON c.id=ca.card_id JOIN games g ON g.id=c.game_id WHERE g.slug='pokemon') AS card_attributes,
              (SELECT COUNT(*) FROM print_attributes pa JOIN prints p ON p.id=pa.print_id JOIN cards c ON c.id=p.card_id JOIN games g ON g.id=c.game_id WHERE g.slug='pokemon') AS print_attributes,
              (SELECT COUNT(*) FROM card_search_profiles csp JOIN games g ON g.id=csp.game_id WHERE g.slug='pokemon') AS card_search_profiles,
              (SELECT COUNT(*) FROM print_search_profiles psp JOIN games g ON g.id=psp.game_id WHERE g.slug='pokemon') AS print_search_profiles
            """
        )).mappings().one())
        session.rollback()

    top_tables = tables[:30]
    top_indexes = indexes[:40]
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": "read_only_storage_audit",
        "database": database,
        "database_size_bytes": database_size,
        "database_size_mb": round(database_size / 1024 / 1024, 2),
        "neon_max_cluster_size": max_cluster_size,
        "pokemon_counts": {key: int(value or 0) for key, value in pokemon_counts.items()},
        "top_tables": top_tables,
        "top_indexes": top_indexes,
        "largest_10_tables_mb": [
            {"table": row["relname"], "mb": round(int(row["total_bytes"]) / 1024 / 1024, 2)}
            for row in top_tables[:10]
        ],
        "largest_10_indexes_mb": [
            {"index": row["index_name"], "table": row["table_name"], "mb": round(int(row["bytes"]) / 1024 / 1024, 2), "scans": int(row["scans"] or 0)}
            for row in top_indexes[:10]
        ],
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
