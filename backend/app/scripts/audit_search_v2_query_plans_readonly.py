from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import psycopg2


OUTPUT = Path(
    os.getenv(
        "SEARCH_V2_QUERY_PLAN_OUTPUT",
        "artifacts/search-v2-query-plans-readonly.json",
    )
)

EXPECTED_INDEXES = {
    "ix_print_search_profiles_game_set_collector": "print_search_profiles",
    "ix_card_search_profiles_game_name_exact": "card_search_profiles",
    "ix_print_localizations_card_name_lower_trgm": "print_localizations",
}

CASES = [
    {
        "name": "mtg_exact_set_collector",
        "budget_ms": 250.0,
        "required_indexes": {"ix_print_search_profiles_game_set_collector"},
        "forbidden_seq_relations": {"print_search_profiles"},
        "sql": """
            SELECT psp.card_id
            FROM print_search_profiles psp
            WHERE psp.game_id = (SELECT id FROM games WHERE slug='mtg' LIMIT 1)
              AND psp.normalized_set_code = 'lea'
              AND psp.normalized_collector_number = '1'
            ORDER BY psp.card_id ASC
            LIMIT 24
        """,
    },
    {
        "name": "pokemon_exact_card_name",
        "budget_ms": 250.0,
        "required_indexes": {"ix_card_search_profiles_game_name_exact"},
        "forbidden_seq_relations": {"card_search_profiles"},
        "sql": """
            SELECT csp.card_id
            FROM card_search_profiles csp
            WHERE csp.game_id = (SELECT id FROM games WHERE slug='pokemon' LIMIT 1)
              AND csp.normalized_name = 'pikachu'
            ORDER BY csp.card_id ASC
            LIMIT 24
        """,
    },
    {
        "name": "yugioh_localized_name_trigram",
        "budget_ms": 500.0,
        "required_indexes": {"ix_print_localizations_card_name_lower_trgm"},
        "forbidden_seq_relations": {"print_localizations"},
        "sql": """
            SELECT pl.print_id
            FROM print_localizations pl
            JOIN prints p ON p.id = pl.print_id
            JOIN cards c ON c.id = p.card_id
            WHERE c.game_id = (SELECT id FROM games WHERE slug='yugioh' LIMIT 1)
              AND lower(pl.card_name) LIKE '%青眼の白龍%'
            ORDER BY pl.print_id ASC
            LIMIT 60
        """,
    },
    {
        "name": "onepiece_exact_card_key",
        "budget_ms": 250.0,
        "required_indexes": set(),
        "forbidden_seq_relations": {"cards", "prints"},
        "sql": """
            SELECT p.id
            FROM cards c
            JOIN prints p ON p.card_id = c.id
            WHERE c.game_id = (SELECT id FROM games WHERE slug='onepiece' LIMIT 1)
              AND c.card_key = 'onepiece:p-150'
            ORDER BY p.id ASC
            LIMIT 24
        """,
    },
]


def _walk(node: dict):
    yield node
    for child in node.get("Plans") or []:
        yield from _walk(child)


def _plan_summary(document: dict) -> dict:
    plan = document["Plan"]
    nodes = list(_walk(plan))
    indexes = sorted(
        {
            str(node["Index Name"])
            for node in nodes
            if node.get("Index Name")
        }
    )
    seq_scans = sorted(
        {
            str(node.get("Relation Name"))
            for node in nodes
            if node.get("Node Type") == "Seq Scan" and node.get("Relation Name")
        }
    )
    relation_nodes = [
        {
            "node_type": node.get("Node Type"),
            "relation": node.get("Relation Name"),
            "index": node.get("Index Name"),
            "actual_rows": node.get("Actual Rows"),
            "actual_loops": node.get("Actual Loops"),
        }
        for node in nodes
        if node.get("Relation Name") or node.get("Index Name")
    ]
    return {
        "planning_time_ms": float(document.get("Planning Time") or 0.0),
        "execution_time_ms": float(document.get("Execution Time") or 0.0),
        "indexes": indexes,
        "seq_scan_relations": seq_scans,
        "relation_nodes": relation_nodes,
        "shared_hit_blocks": int(plan.get("Shared Hit Blocks") or 0),
        "shared_read_blocks": int(plan.get("Shared Read Blocks") or 0),
        "actual_rows": int(plan.get("Actual Rows") or 0),
    }


def main() -> int:
    url = (os.getenv("DATABASE_URL_UNPOOLED") or os.getenv("DATABASE_URL") or "").strip()
    if not url:
        raise RuntimeError("DATABASE_URL_UNPOOLED or DATABASE_URL is required")

    conn = psycopg2.connect(url)
    conn.autocommit = False
    cases: list[dict] = []
    try:
        with conn.cursor() as cur:
            cur.execute("SHOW transaction_read_only")
            if cur.fetchone()[0] != "on":
                raise RuntimeError("refusing query-plan audit without transaction_read_only=on")

            cur.execute(
                """
                SELECT tablename, indexname, indexdef
                FROM pg_indexes
                WHERE schemaname = current_schema()
                  AND indexname = ANY(%s)
                ORDER BY indexname
                """,
                (list(EXPECTED_INDEXES),),
            )
            index_rows = cur.fetchall()
            present_indexes = {row[1]: {"table": row[0], "definition": row[2]} for row in index_rows}
            missing_schema_indexes = sorted(set(EXPECTED_INDEXES) - set(present_indexes))
            if missing_schema_indexes:
                raise AssertionError(
                    "missing required Search V2 indexes in Neon: " + ", ".join(missing_schema_indexes)
                )

            for name, expected_table in EXPECTED_INDEXES.items():
                actual_table = present_indexes[name]["table"]
                if actual_table != expected_table:
                    raise AssertionError(
                        f"Search V2 index {name} is on {actual_table}, expected {expected_table}"
                    )

            cur.execute("SET LOCAL statement_timeout = '8s'")
            for case in CASES:
                cur.execute(
                    "EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + case["sql"]
                )
                document = cur.fetchone()[0][0]
                summary = _plan_summary(document)
                used = set(summary["indexes"])
                missing_plan_indexes = sorted(case["required_indexes"] - used)
                forbidden_seq = sorted(
                    set(summary["seq_scan_relations"]) & case["forbidden_seq_relations"]
                )
                failures = []
                if missing_plan_indexes:
                    failures.append("required_indexes_not_used=" + ",".join(missing_plan_indexes))
                if forbidden_seq:
                    failures.append("forbidden_seq_scan=" + ",".join(forbidden_seq))
                if summary["execution_time_ms"] > case["budget_ms"]:
                    failures.append(
                        f"execution_time_ms={summary['execution_time_ms']:.3f}>{case['budget_ms']:.3f}"
                    )
                cases.append(
                    {
                        "name": case["name"],
                        "budget_ms": case["budget_ms"],
                        "required_indexes": sorted(case["required_indexes"]),
                        "forbidden_seq_relations": sorted(case["forbidden_seq_relations"]),
                        "status": "pass" if not failures else "fail",
                        "failures": failures,
                        **summary,
                    }
                )
        conn.rollback()
    finally:
        conn.close()

    report = {
        "status": "pass" if all(case["status"] == "pass" for case in cases) else "fail",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "transaction_read_only": True,
        "production_writes": 0,
        "expected_indexes": present_indexes,
        "cases": cases,
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    if report["status"] != "pass":
        failed = [
            f"{case['name']}:{';'.join(case['failures'])}"
            for case in cases
            if case["status"] != "pass"
        ]
        raise SystemExit("Search V2 query-plan gate failed: " + " | ".join(failed))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
