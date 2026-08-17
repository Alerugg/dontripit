from __future__ import annotations

import argparse
import json
import os
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path

import psycopg2

from app.scripts.benchmark_mtg_hybrid_shadow_v2 import run as build_full_hybrid


LEAN_SCHEMA = "mtg_hybrid_lean"
COMMON_SCHEMA = "mtg_hybrid"


def _write(path: Path | None, payload: dict) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def _relation(cur, schema: str, table: str) -> dict:
    cur.execute(
        """
        SELECT
          pg_relation_size(c.oid)::bigint,
          pg_indexes_size(c.oid)::bigint,
          pg_total_relation_size(c.oid)::bigint,
          COALESCE(c.reltuples,0)::bigint
        FROM pg_class c
        JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname=%s AND c.relname=%s AND c.relkind='r'
        """,
        (schema, table),
    )
    row = cur.fetchone()
    if row is None:
        raise AssertionError(f"Missing relation {schema}.{table}")
    heap, indexes, total, rows = map(int, row)
    return {
        "table": table,
        "heap_bytes": heap,
        "index_bytes": indexes,
        "total_bytes": total,
        "heap_mib": round(heap / 1024 / 1024, 2),
        "index_mib": round(indexes / 1024 / 1024, 2),
        "total_mib": round(total / 1024 / 1024, 2),
        "estimated_rows": rows,
    }


def _indexes(cur, schema: str, table: str) -> list[dict]:
    cur.execute(
        """
        SELECT i.relname, pg_relation_size(i.oid)::bigint
        FROM pg_class i
        JOIN pg_index x ON x.indexrelid=i.oid
        JOIN pg_class t ON t.oid=x.indrelid
        JOIN pg_namespace n ON n.oid=t.relnamespace
        WHERE n.nspname=%s AND t.relname=%s
        ORDER BY pg_relation_size(i.oid) DESC, i.relname
        """,
        (schema, table),
    )
    return [
        {"index": name, "bytes": int(size), "mib": round(int(size) / 1024 / 1024, 3)}
        for name, size in cur.fetchall()
    ]


def _median_ms(cur, sql: str, params: tuple, repeats: int = 7) -> float:
    cur.execute(sql, params)
    cur.fetchall()
    samples = []
    for _ in range(repeats):
        started = time.perf_counter()
        cur.execute(sql, params)
        cur.fetchall()
        samples.append((time.perf_counter() - started) * 1000.0)
    return round(statistics.median(samples), 2)


def run(*, database_url: str, report_path: Path | None = None) -> dict:
    # Build the already-measured full hybrid first. This gives us the complete
    # current Scryfall corpus and all common Card/SourcePrint/Search relations in
    # a disposable PostgreSQL instance. No Neon credentials are involved.
    full = build_full_hybrid(database_url=database_url, report_path=None)
    sample = dict(full["benchmark_sample"])

    conn = psycopg2.connect(database_url)
    conn.autocommit = True
    try:
        cur = conn.cursor()
        cur.execute(f'DROP SCHEMA IF EXISTS "{LEAN_SCHEMA}" CASCADE')
        cur.execute(f'CREATE SCHEMA "{LEAN_SCHEMA}"')
        cur.execute(f"""
            CREATE TABLE {LEAN_SCHEMA}.prints (
              id BIGINT PRIMARY KEY,
              source_print_id BIGINT NOT NULL REFERENCES {COMMON_SCHEMA}.source_prints(id) ON DELETE CASCADE,
              card_id BIGINT NOT NULL REFERENCES {COMMON_SCHEMA}.cards(id),
              set_id INTEGER NOT NULL REFERENCES {COMMON_SCHEMA}.sets(id),
              finish_code SMALLINT NOT NULL,
              UNIQUE(source_print_id, finish_code)
            );
            CREATE INDEX {LEAN_SCHEMA}_prints_card ON {LEAN_SCHEMA}.prints(card_id);
            CREATE INDEX {LEAN_SCHEMA}_prints_set ON {LEAN_SCHEMA}.prints(set_id);
            CREATE INDEX {LEAN_SCHEMA}_prints_finish ON {LEAN_SCHEMA}.prints(finish_code, id);
        """)
        cur.execute(f"""
            INSERT INTO {LEAN_SCHEMA}.prints (id, source_print_id, card_id, set_id, finish_code)
            SELECT id, source_print_id, card_id, set_id, finish_code
            FROM {COMMON_SCHEMA}.prints
            ORDER BY id
        """)
        cur.execute(f"ANALYZE {LEAN_SCHEMA}.prints")

        cur.execute(f"SELECT COUNT(*) FROM {LEAN_SCHEMA}.prints")
        lean_count = int(cur.fetchone()[0])
        expected = int(full["counts"]["exact_prints"])
        if lean_count != expected:
            raise AssertionError(f"Lean exact Print count mismatch: {lean_count} != {expected}")

        cur.execute(f"""
            SELECT COUNT(*)
            FROM (
              SELECT source_print_id, finish_code
              FROM {LEAN_SCHEMA}.prints
              GROUP BY source_print_id, finish_code
              HAVING COUNT(*) > 1
            ) q
        """)
        collisions = int(cur.fetchone()[0])
        if collisions:
            raise AssertionError(f"Lean exact Print identity collisions: {collisions}")

        lean_prints = _relation(cur, LEAN_SCHEMA, "prints")
        full_prints = next(row for row in full["model"]["tables"] if row["table"] == "prints")
        common_total_bytes = int(full["model"]["total_bytes"]) - int(full_prints["total_bytes"])
        lean_total_bytes = common_total_bytes + int(lean_prints["total_bytes"])

        name_query = f"%{sample['normalized_name']}%"
        source_id = sample["scryfall_id"]
        finish_code = int(sample["finish_code"])
        set_code = sample["set_code"]
        collector = sample["collector_number"]

        benchmarks = {
            "name_search_ms": _median_ms(
                cur,
                f"SELECT source_print_id FROM {COMMON_SCHEMA}.print_search WHERE normalized_name ILIKE %s LIMIT 20",
                (name_query,),
            ),
            "exact_collector_ms": _median_ms(
                cur,
                f"SELECT source_print_id FROM {COMMON_SCHEMA}.print_search WHERE set_code=%s AND collector_number=%s LIMIT 20",
                (set_code, collector),
            ),
            "name_plus_finish_exact_print_ms": _median_ms(
                cur,
                f"""
                SELECT p.id
                FROM {COMMON_SCHEMA}.print_search s
                JOIN {LEAN_SCHEMA}.prints p ON p.source_print_id=s.source_print_id
                WHERE s.normalized_name ILIKE %s AND p.finish_code=%s
                LIMIT 20
                """,
                (name_query, finish_code),
            ),
            "source_id_plus_finish_ms": _median_ms(
                cur,
                f"""
                SELECT p.id
                FROM {COMMON_SCHEMA}.source_prints sp
                JOIN {LEAN_SCHEMA}.prints p ON p.source_print_id=sp.id
                WHERE sp.scryfall_id=%s AND p.finish_code=%s
                LIMIT 1
                """,
                (source_id, finish_code),
            ),
            "exact_print_id_ms": _median_ms(
                cur,
                f"SELECT id FROM {LEAN_SCHEMA}.prints WHERE id=%s",
                (1,),
            ),
        }
        cur.close()
    finally:
        conn.close()

    model_b_mib = 197.68
    model_a_mib = 235.41
    model_c_mib = float(full["model"]["total_mib"])
    lean_mib = round(lean_total_bytes / 1024 / 1024, 2)

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": "ephemeral_postgresql_mtg_lean_hybrid_exact_print_benchmark",
        "status": "pass",
        "counts": full["counts"],
        "model": {
            "description": "Common SourcePrint/Card/Search metadata once; lean exact Print row keeps only canonical physical identity and joins to SourcePrint for collector/language/rarity/image metadata.",
            "common_relations_bytes": common_total_bytes,
            "common_relations_mib": round(common_total_bytes / 1024 / 1024, 2),
            "lean_exact_print_relation": lean_prints,
            "lean_exact_print_indexes": _indexes_for_report(database_url),
            "total_bytes": lean_total_bytes,
            "total_mib": lean_mib,
        },
        "comparison_mib": {
            "A_duplicated_exact_print": model_a_mib,
            "B_source_print_finish_variant": model_b_mib,
            "C_full_hybrid_exact_print": model_c_mib,
            "D_lean_hybrid_exact_print": lean_mib,
            "D_over_B_mib": round(lean_mib - model_b_mib, 2),
            "D_saved_vs_C_mib": round(model_c_mib - lean_mib, 2),
            "D_saved_vs_A_mib": round(model_a_mib - lean_mib, 2),
        },
        "benchmarks_median_ms": benchmarks,
        "benchmark_sample": sample,
        "identity": {
            "exact_market_entity": "lean Print.id",
            "source_provenance_entity": "SourcePrint.id / Scryfall id",
            "exact_physical_key": "Scryfall id + finish; textual display key derived in application code rather than indexed as duplicated text",
            "search_projection": "one row per SourcePrint, expanded to exact Print IDs only when finish is requested",
        },
        "neon_writes": 0,
        "decision_rule": "Model D is preferred over B only if preserving universal exact Print.id is worth its measured incremental storage and latency remains comparable. Production capacity must still include meaningful reserve.",
    }
    _write(report_path, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return report


def _indexes_for_report(database_url: str) -> list[dict]:
    conn = psycopg2.connect(database_url)
    try:
        cur = conn.cursor()
        rows = _indexes(cur, LEAN_SCHEMA, "prints")
        cur.close()
        return rows
    finally:
        conn.close()


def _indexes(cur, schema: str, table: str) -> list[dict]:
    cur.execute(
        """
        SELECT i.relname, pg_relation_size(i.oid)::bigint
        FROM pg_class i
        JOIN pg_index x ON x.indexrelid=i.oid
        JOIN pg_class t ON t.oid=x.indrelid
        JOIN pg_namespace n ON n.oid=t.relnamespace
        WHERE n.nspname=%s AND t.relname=%s
        ORDER BY pg_relation_size(i.oid) DESC, i.relname
        """,
        (schema, table),
    )
    return [
        {"index": name, "bytes": int(size), "mib": round(int(size) / 1024 / 1024, 3)}
        for name, size in cur.fetchall()
    ]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url", default=os.getenv("SHADOW_DATABASE_URL"))
    parser.add_argument("--report-path", type=Path, default=None)
    args = parser.parse_args()
    if not args.database_url:
        raise SystemExit("SHADOW_DATABASE_URL/--database-url is required")
    run(database_url=args.database_url, report_path=args.report_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
