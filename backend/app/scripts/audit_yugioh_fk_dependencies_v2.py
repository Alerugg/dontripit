from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import text

from app import db


CORE_ID_SQL = {
    "sets": "SELECT id FROM sets WHERE game_id=:game",
    "cards": "SELECT id FROM cards WHERE game_id=:game",
    "prints": "SELECT p.id FROM prints p JOIN cards c ON c.id=p.card_id WHERE c.game_id=:game",
}

KNOWN_REBUILDABLE = {
    "card_attributes",
    "card_search_profiles",
    "print_attributes",
    "print_field_provenance",
    "print_identifiers",
    "print_images",
    "print_releases",
    "print_search_profiles",
}

KNOWN_DURABLE = {
    "prices",
    "products",
    "holdings",
    "market_observations",
    "market_index_snapshots",
}


def _quote_ident(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _write(path: Path | None, payload: dict) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def run(*, report_path: Path | None = None) -> dict:
    db.init_engine()
    with db.SessionLocal() as session:
        game_id = session.execute(text("SELECT id FROM games WHERE slug='yugioh' LIMIT 1")).scalar_one_or_none()
        if game_id is None:
            report = {
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "mode": "read_only_exhaustive_fk_dependency_audit",
                "game_present": False,
                "status": "pass",
            }
            _write(report_path, report)
            print(json.dumps(report, indent=2))
            return report

        game_id = int(game_id)
        core_ids = {
            table: [int(v) for v in session.execute(text(sql), {"game": game_id}).scalars().all()]
            for table, sql in CORE_ID_SQL.items()
        }

        fk_rows = [dict(row) for row in session.execute(text("""
            SELECT DISTINCT
              tc.constraint_name,
              tc.table_name AS dependent_table,
              kcu.column_name AS dependent_column,
              ccu.table_name AS referenced_table,
              ccu.column_name AS referenced_column,
              rc.delete_rule
            FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu
              ON tc.constraint_name=kcu.constraint_name
             AND tc.constraint_schema=kcu.constraint_schema
            JOIN information_schema.constraint_column_usage ccu
              ON ccu.constraint_name=tc.constraint_name
             AND ccu.constraint_schema=tc.constraint_schema
            JOIN information_schema.referential_constraints rc
              ON rc.constraint_name=tc.constraint_name
             AND rc.constraint_schema=tc.constraint_schema
            WHERE tc.constraint_type='FOREIGN KEY'
              AND tc.table_schema='public'
              AND ccu.table_name IN ('sets','cards','prints')
            ORDER BY ccu.table_name, tc.table_name, kcu.column_name
        """)).mappings().all()]

        dependency_rows = []
        for fk in fk_rows:
            referenced = str(fk["referenced_table"])
            dependent_table = str(fk["dependent_table"])
            dependent_column = str(fk["dependent_column"])
            ids = core_ids.get(referenced, []) or [-1]
            sql = (
                f"SELECT COUNT(*) FROM {_quote_ident(dependent_table)} "
                f"WHERE {_quote_ident(dependent_column)} = ANY(:ids)"
            )
            count = int(session.execute(text(sql), {"ids": ids}).scalar_one() or 0)
            if dependent_table in KNOWN_DURABLE:
                classification = "durable"
            elif dependent_table in KNOWN_REBUILDABLE or dependent_table == "prints":
                classification = "rebuildable_or_core"
            else:
                classification = "unknown_review"
            dependency_rows.append({**fk, "yugioh_rows": count, "classification": classification})

        session.rollback()

    nonzero_unknown = [row for row in dependency_rows if row["yugioh_rows"] > 0 and row["classification"] == "unknown_review"]
    nonzero_durable = [row for row in dependency_rows if row["yugioh_rows"] > 0 and row["classification"] == "durable"]
    nonzero_rebuildable = [row for row in dependency_rows if row["yugioh_rows"] > 0 and row["classification"] == "rebuildable_or_core"]

    # prints->cards and prints->sets are the core graph itself, not external blockers.
    external_rebuildable = [row for row in nonzero_rebuildable if row["dependent_table"] != "prints"]

    blockers = []
    if nonzero_durable:
        blockers.append(f"{len(nonzero_durable)} durable FK relationships contain YGO rows")
    if nonzero_unknown:
        blockers.append(f"{len(nonzero_unknown)} unknown FK relationships contain YGO rows")

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": "read_only_exhaustive_fk_dependency_audit",
        "game_present": True,
        "status": "review_required" if blockers else "pass",
        "core_counts": {key: len(value) for key, value in core_ids.items()},
        "foreign_key_relationships": dependency_rows,
        "nonzero_durable": nonzero_durable,
        "nonzero_unknown": nonzero_unknown,
        "nonzero_rebuildable_external": external_rebuildable,
        "blockers_for_transactional_replace": blockers,
        "safe_for_transactional_replace_after_rebuildable_cleanup": not blockers,
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
