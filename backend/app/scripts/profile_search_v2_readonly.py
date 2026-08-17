from __future__ import annotations

import json
import os
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import event, text

from app import db
from app.search_v2.pokemon_query import normal_pokemon_search
from app.search_v2.pokemon_advanced import advanced_pokemon_search
from app.search_v2.query import normal_search, facet_definitions
from app.search_v2.yugioh_query import normal_yugioh_search
from app.search_v2.mtg_query import normal_mtg_search


OUTPUT = Path(os.getenv("SEARCH_V2_PROFILE_OUTPUT", "artifacts/search-v2-sql-profile.json"))
STATEMENT_TIMEOUT_MS = int(os.getenv("SEARCH_V2_PROFILE_TIMEOUT_MS", "30000"))


def _jsonable(value):
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _plan_nodes(plan: dict) -> list[dict]:
    nodes: list[dict] = []

    def walk(node: dict, depth: int = 0):
        nodes.append(
            {
                "depth": depth,
                "node_type": node.get("Node Type"),
                "relation": node.get("Relation Name"),
                "alias": node.get("Alias"),
                "index": node.get("Index Name"),
                "join_type": node.get("Join Type"),
                "actual_startup_ms": node.get("Actual Startup Time"),
                "actual_total_ms": node.get("Actual Total Time"),
                "actual_rows": node.get("Actual Rows"),
                "actual_loops": node.get("Actual Loops"),
                "plan_rows": node.get("Plan Rows"),
                "rows_removed_by_filter": node.get("Rows Removed by Filter"),
                "rows_removed_by_join_filter": node.get("Rows Removed by Join Filter"),
                "shared_hit_blocks": node.get("Shared Hit Blocks"),
                "shared_read_blocks": node.get("Shared Read Blocks"),
                "temp_read_blocks": node.get("Temp Read Blocks"),
                "temp_written_blocks": node.get("Temp Written Blocks"),
                "filter": node.get("Filter"),
                "index_cond": node.get("Index Cond"),
                "recheck_cond": node.get("Recheck Cond"),
                "sort_key": node.get("Sort Key"),
                "sort_method": node.get("Sort Method"),
            }
        )
        for child in node.get("Plans") or []:
            walk(child, depth + 1)

    walk(plan)
    nodes.sort(key=lambda row: float(row.get("actual_total_ms") or 0), reverse=True)
    return nodes[:20]


def _explain(session, statement: str, parameters):
    if not statement.lstrip().upper().startswith(("SELECT", "WITH")):
        return None
    conn = session.connection()
    conn.exec_driver_sql(f"SET LOCAL statement_timeout = '{STATEMENT_TIMEOUT_MS}ms'")
    result = conn.exec_driver_sql(
        "EXPLAIN (ANALYZE, BUFFERS, VERBOSE, FORMAT JSON) " + statement,
        parameters,
    ).scalar_one()
    if isinstance(result, str):
        result = json.loads(result)
    root = result[0]
    return {
        "planning_ms": root.get("Planning Time"),
        "execution_ms": root.get("Execution Time"),
        "top_nodes": _plan_nodes(root["Plan"]),
    }


def main() -> int:
    database_url = (os.getenv("DATABASE_URL_UNPOOLED") or os.getenv("DATABASE_URL") or "").strip()
    if not database_url:
        raise RuntimeError("DATABASE_URL_UNPOOLED or DATABASE_URL is required")

    engine = db.init_engine(database_url)
    captured: list[dict] = []
    state = {"case": None, "explaining": False}

    @event.listens_for(engine, "before_cursor_execute")
    def before_cursor_execute(conn, cursor, statement, parameters, context, executemany):
        if state["explaining"]:
            return
        context._sv2_profile_started = time.perf_counter()

    @event.listens_for(engine, "after_cursor_execute")
    def after_cursor_execute(conn, cursor, statement, parameters, context, executemany):
        started = getattr(context, "_sv2_profile_started", None)
        if started is None or state["explaining"]:
            return
        elapsed_ms = (time.perf_counter() - started) * 1000
        normalized = statement.lstrip().upper()
        if normalized.startswith(("SELECT", "WITH")):
            captured.append(
                {
                    "case": state["case"],
                    "duration_ms": round(elapsed_ms, 3),
                    "statement": statement,
                    "parameters": parameters,
                }
            )

    report = {
        "status": "pass",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "transaction_read_only": True,
        "production_writes": 0,
        "statement_timeout_ms": STATEMENT_TIMEOUT_MS,
        "cases": [],
    }

    with db.SessionLocal() as session:
        mode = session.execute(text("SHOW transaction_read_only")).scalar_one()
        if mode != "on":
            raise RuntimeError(f"refusing to profile: transaction_read_only={mode!r}")
        session.execute(text(f"SET LOCAL statement_timeout = '{STATEMENT_TIMEOUT_MS}ms'"))

        cases = [
            ("pokemon_natural_pikachu", lambda: normal_pokemon_search(session, query="Pikachu", limit=8)),
            (
                "pokemon_advanced_pikachu_holo",
                lambda: advanced_pokemon_search(
                    session,
                    filters={"finish": "holo"},
                    query="Pikachu",
                    sort="relevance",
                    has_price=False,
                    limit=24,
                    offset=0,
                ),
            ),
            ("onepiece_natural_luffy", lambda: normal_search(session, query="Luffy", game_slug="onepiece", limit=8)),
            ("onepiece_facets", lambda: facet_definitions(session, game_slug="onepiece")),
            ("yugioh_natural_blue_eyes", lambda: normal_yugioh_search(session, query="Blue-Eyes White Dragon", limit=8)),
            ("mtg_natural_black_lotus", lambda: normal_mtg_search(session, query="Black Lotus", limit=8)),
        ]

        for name, fn in cases:
            captured.clear()
            state["case"] = name
            started = time.perf_counter()
            error = None
            output_size = None
            try:
                value = fn()
                if isinstance(value, dict):
                    output_size = len(value.get("items") or value.get("facets") or [])
                elif isinstance(value, list):
                    output_size = len(value)
            except Exception as exc:  # fail-open per case so the artifact explains all paths
                error = f"{type(exc).__name__}: {exc}"
                session.rollback()
                mode = session.execute(text("SHOW transaction_read_only")).scalar_one()
                if mode != "on":
                    raise RuntimeError("read-only mode changed after rollback")
                session.execute(text(f"SET LOCAL statement_timeout = '{STATEMENT_TIMEOUT_MS}ms'"))
            total_ms = (time.perf_counter() - started) * 1000

            statements = sorted(captured, key=lambda row: row["duration_ms"], reverse=True)
            case_report = {
                "name": name,
                "wall_ms": round(total_ms, 3),
                "output_size": output_size,
                "error": error,
                "statements": [
                    {
                        "duration_ms": row["duration_ms"],
                        "sql_preview": " ".join(row["statement"].split())[:1200],
                        "parameters": _jsonable(row["parameters"]),
                    }
                    for row in statements
                ],
            }

            if statements and error is None:
                slowest = statements[0]
                try:
                    state["explaining"] = True
                    case_report["slowest_explain"] = _explain(
                        session,
                        slowest["statement"],
                        slowest["parameters"],
                    )
                except Exception as exc:
                    case_report["explain_error"] = f"{type(exc).__name__}: {exc}"
                    session.rollback()
                    mode = session.execute(text("SHOW transaction_read_only")).scalar_one()
                    if mode != "on":
                        raise RuntimeError("read-only mode changed after EXPLAIN rollback")
                    session.execute(text(f"SET LOCAL statement_timeout = '{STATEMENT_TIMEOUT_MS}ms'"))
                finally:
                    state["explaining"] = False

            report["cases"].append(case_report)

        session.rollback()

    slow_cases = sorted(report["cases"], key=lambda row: row["wall_ms"], reverse=True)
    report["slowest_cases"] = [
        {"name": row["name"], "wall_ms": row["wall_ms"], "error": row["error"]}
        for row in slow_cases
    ]
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
