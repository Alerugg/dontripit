from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import psycopg2

NEW_ES_PRINTS = 88198
NEW_JA_PRINTS = 103860
NEW_PRINTS = NEW_ES_PRINTS + NEW_JA_PRINTS
# Each exact finish Print receives its language-accurate image rows and one
# localization + identifier + attributes row. Use a conservative 20% reserve
# on the computed relation-size forecast for JSON/image/index variability.
FORECAST_ROWS = {
    "prints": NEW_PRINTS,
    "print_attributes": NEW_PRINTS,
    "print_images": NEW_PRINTS,
    "print_identifiers": NEW_PRINTS,
    "print_localizations": NEW_PRINTS,
}
HEADROOM_FACTOR = 1.20


def _url() -> str:
    value = os.getenv("DATABASE_URL_UNPOOLED") or os.getenv("DATABASE_URL")
    if not value:
        raise RuntimeError("DATABASE_URL_UNPOOLED or DATABASE_URL is required")
    if value.startswith("postgresql+psycopg2://"):
        return "postgresql://" + value[len("postgresql+psycopg2://"):]
    if value.startswith("postgres://"):
        return "postgresql://" + value[len("postgres://"):]
    return value


def _one(cur, sql: str, params=()):
    cur.execute(sql, params)
    row = cur.fetchone()
    return row[0] if row else None


def _relation(cur, table: str) -> dict:
    exists = bool(_one(cur, "SELECT to_regclass(%s) IS NOT NULL", (f"public.{table}",)))
    if not exists:
        return {"table": table, "exists": False, "rows": 0, "total_bytes": 0, "heap_bytes": 0, "index_bytes": 0, "bytes_per_row": 0.0}
    rows = int(_one(cur, f"SELECT count(*) FROM {table}") or 0)
    total = int(_one(cur, "SELECT pg_total_relation_size(%s::regclass)", (table,)) or 0)
    heap = int(_one(cur, "SELECT pg_relation_size(%s::regclass)", (table,)) or 0)
    indexes = int(_one(cur, "SELECT pg_indexes_size(%s::regclass)", (table,)) or 0)
    return {
        "table": table,
        "exists": True,
        "rows": rows,
        "total_bytes": total,
        "heap_bytes": heap,
        "index_bytes": indexes,
        "bytes_per_row": round(total / rows, 2) if rows else 0.0,
    }


def run(output: Path) -> dict:
    conn = psycopg2.connect(_url(), connect_timeout=20, application_name="dontripit_mtg_multilingual_storage_readonly")
    conn.set_session(readonly=True, autocommit=False)
    try:
        with conn.cursor() as cur:
            if str(_one(cur, "SHOW transaction_read_only")).lower() != "on":
                raise RuntimeError("Read-only guard failed")
            database_size = int(_one(cur, "SELECT pg_database_size(current_database())") or 0)
            max_cluster_size = _one(cur, "SELECT current_setting('neon.max_cluster_size', true)")
            revision = str(_one(cur, "SELECT version_num FROM alembic_version"))
            game_id = int(_one(cur, "SELECT id FROM games WHERE slug='mtg'") or 0)
            if not game_id:
                raise RuntimeError("MTG game row missing")

            mtg_counts = {
                "sets": int(_one(cur, "SELECT count(*) FROM sets WHERE game_id=%s", (game_id,)) or 0),
                "cards": int(_one(cur, "SELECT count(*) FROM cards WHERE game_id=%s", (game_id,)) or 0),
                "prints": int(_one(cur, "SELECT count(*) FROM prints p JOIN cards c ON c.id=p.card_id WHERE c.game_id=%s", (game_id,)) or 0),
                "print_attributes": int(_one(cur, "SELECT count(*) FROM print_attributes pa JOIN prints p ON p.id=pa.print_id JOIN cards c ON c.id=p.card_id WHERE c.game_id=%s", (game_id,)) or 0),
                "print_images": int(_one(cur, "SELECT count(*) FROM print_images pi JOIN prints p ON p.id=pi.print_id JOIN cards c ON c.id=p.card_id WHERE c.game_id=%s", (game_id,)) or 0),
                "print_identifiers": int(_one(cur, "SELECT count(*) FROM print_identifiers pi JOIN prints p ON p.id=pi.print_id JOIN cards c ON c.id=p.card_id WHERE c.game_id=%s", (game_id,)) or 0),
                "print_localizations": int(_one(cur, "SELECT count(*) FROM print_localizations pl JOIN prints p ON p.id=pl.print_id JOIN cards c ON c.id=p.card_id WHERE c.game_id=%s", (game_id,)) or 0),
            }

            relations = {table: _relation(cur, table) for table in FORECAST_ROWS}

            # pg_database_size includes all objects and is the best current
            # operational baseline. Relation averages already include indexes.
            forecast = {}
            raw_increment = 0
            for table, additional_rows in FORECAST_ROWS.items():
                rel = relations[table]
                per_row = float(rel["bytes_per_row"] or 0.0)
                # New/empty relations cannot be estimated from zero. Use 512 B
                # only as a transparent fallback; today print_localizations has
                # Pokémon rows so this path should normally remain unused.
                fallback_used = False
                if per_row <= 0:
                    per_row = 512.0
                    fallback_used = True
                incremental = int(round(per_row * additional_rows))
                raw_increment += incremental
                forecast[table] = {
                    "existing_rows_global": rel["rows"],
                    "additional_rows": additional_rows,
                    "observed_total_bytes_per_row_including_indexes": per_row,
                    "estimated_incremental_bytes": incremental,
                    "estimated_incremental_mb": round(incremental / 1024 / 1024, 2),
                    "fallback_used": fallback_used,
                }

            conservative_increment = int(round(raw_increment * HEADROOM_FACTOR))
            projected_size = database_size + conservative_increment
            max_bytes = None
            if max_cluster_size is not None:
                try:
                    max_bytes = int(max_cluster_size)
                except (TypeError, ValueError):
                    max_bytes = None

            capacity = {
                "current_database_bytes": database_size,
                "current_database_mb": round(database_size / 1024 / 1024, 2),
                "raw_estimated_increment_bytes": raw_increment,
                "raw_estimated_increment_mb": round(raw_increment / 1024 / 1024, 2),
                "headroom_factor": HEADROOM_FACTOR,
                "conservative_increment_bytes": conservative_increment,
                "conservative_increment_mb": round(conservative_increment / 1024 / 1024, 2),
                "projected_database_bytes": projected_size,
                "projected_database_mb": round(projected_size / 1024 / 1024, 2),
                "neon_max_cluster_size_raw": max_cluster_size,
                "neon_max_cluster_size_bytes": max_bytes,
                "projected_within_reported_cluster_limit": (projected_size <= max_bytes) if max_bytes else None,
            }

        conn.rollback()
    finally:
        conn.close()

    report = {
        "status": "pass",
        "mode": "strict-read-only-storage-forecast",
        "database_writes": 0,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "alembic_version": revision,
        "certified_additive_delta": {"es": NEW_ES_PRINTS, "ja": NEW_JA_PRINTS, "total": NEW_PRINTS},
        "mtg_existing_counts": mtg_counts,
        "relations": relations,
        "forecast_by_table": forecast,
        "capacity": capacity,
        "notes": [
            "Observed bytes/row use pg_total_relation_size and therefore include table indexes.",
            "Forecast applies a 20% reserve above observed per-row storage for JSON/image/index variability.",
            "This is a storage forecast only; production writes remain forbidden at this stage.",
        ],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True, default=str))
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("/tmp/mtg-multilingual-storage.json"))
    args = parser.parse_args()
    run(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
