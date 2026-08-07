from __future__ import annotations

import json
import statistics
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from app import db
from app.search_v2.normalization import compact_search_text, normalize_search_text
from app.search_v2.query import normal_search


@dataclass(frozen=True)
class BenchmarkCase:
    query: str
    kind: str
    hard_gate: bool = False
    expected_name: str | None = None
    expected_collector: str | None = None
    expected_set: str | None = None
    expected_language: str | None = None
    expected_rarity: str | None = None
    expected_trait: str | None = None
    expected_color: str | None = None
    expected_card_type: str | None = None
    max_rank: int = 10


def _name(row: dict) -> str:
    return normalize_search_text(str(row.get("name") or ""))


def _print(row: dict) -> dict:
    return row.get("matched_print") or {}


def _attributes(row: dict) -> dict:
    return row.get("attributes") or {}


def _collector_matches(row: dict, expected: str) -> bool:
    actual = str(_print(row).get("collector_number") or "")
    return compact_search_text(actual) == compact_search_text(expected)


def _matches(case: BenchmarkCase, row: dict) -> bool:
    attrs = _attributes(row)
    if case.expected_name and normalize_search_text(case.expected_name) not in _name(row):
        return False
    if case.expected_collector and not _collector_matches(row, case.expected_collector):
        return False
    if case.expected_set and str(_print(row).get("set_code") or "").lower() != case.expected_set.lower():
        return False
    if case.expected_language and str(_print(row).get("language") or "").lower() != case.expected_language.lower():
        return False
    if case.expected_rarity and str(_print(row).get("rarity") or "").upper() != case.expected_rarity.upper():
        return False
    if case.expected_trait:
        traits = [normalize_search_text(str(value)) for value in (attrs.get("traits") or [])]
        if normalize_search_text(case.expected_trait) not in traits:
            return False
    if case.expected_color:
        colors = [normalize_search_text(str(value)) for value in (attrs.get("color") or [])]
        if normalize_search_text(case.expected_color) not in colors:
            return False
    if case.expected_card_type and normalize_search_text(str(attrs.get("card_type") or "")) != normalize_search_text(case.expected_card_type):
        return False
    return True


def _rank(case: BenchmarkCase, rows: list[dict]) -> int | None:
    for index, row in enumerate(rows, start=1):
        if _matches(case, row):
            return index
    return None


def _compact_rows(rows: list[dict], limit: int = 5) -> list[dict]:
    result: list[dict] = []
    for row in rows[:limit]:
        matched = _print(row)
        attrs = _attributes(row)
        result.append(
            {
                "name": row.get("name"),
                "card_key": row.get("card_key"),
                "collector_number": matched.get("collector_number"),
                "set_code": matched.get("set_code"),
                "language": matched.get("language"),
                "rarity": matched.get("rarity"),
                "variant": matched.get("exact_variant"),
                "color": attrs.get("color"),
                "card_type": attrs.get("card_type"),
                "traits": attrs.get("traits"),
                "score": row.get("score"),
            }
        )
    return result


def _exact_collector_case(query: str) -> BenchmarkCase:
    return BenchmarkCase(
        query=query,
        kind="exact_collector",
        hard_gate=True,
        expected_collector=query,
        max_rank=1,
    )


CASES: tuple[BenchmarkCase, ...] = (
    BenchmarkCase("Luffy", "canonical_name", hard_gate=True, expected_name="Monkey D Luffy", max_rank=1),
    BenchmarkCase("Zoro", "canonical_name", hard_gate=True, expected_name="Roronoa Zoro", max_rank=1),
    BenchmarkCase("Ace", "canonical_name", expected_name="Portgas D Ace", max_rank=3),
    BenchmarkCase("Law", "character_name", expected_name="Law", max_rank=5),
    BenchmarkCase("Shanks", "character_name", expected_name="Shanks", max_rank=3),
    BenchmarkCase("Nami", "character_name", expected_name="Nami", max_rank=3),
    _exact_collector_case("OP05-119"),
    _exact_collector_case("OP01-001"),
    _exact_collector_case("ST01-001"),
    _exact_collector_case("P-001"),
    BenchmarkCase("Luffy OP05", "compound", hard_gate=True, expected_name="Luffy", expected_set="op-05", max_rank=3),
    BenchmarkCase(
        "Luffy OP05 English SEC",
        "compound",
        hard_gate=True,
        expected_name="Luffy",
        expected_set="op-05",
        expected_language="en",
        expected_rarity="SEC",
        max_rank=5,
    ),
    BenchmarkCase("monky lufi", "typo", hard_gate=True, expected_name="Luffy", max_rank=3),
    BenchmarkCase("lufi", "typo", expected_name="Luffy", max_rank=5),
    BenchmarkCase("zolo", "typo", expected_name="Zoro", max_rank=5),
    BenchmarkCase("Straw Hat Crew", "semantic_trait", expected_trait="Straw Hat Crew", max_rank=5),
    BenchmarkCase("red leader", "natural_properties", expected_color="Red", expected_card_type="Leader", max_rank=10),
)


def run() -> dict:
    db.init_engine()
    generated_at = datetime.now(timezone.utc).isoformat()
    results: list[dict] = []
    elapsed_values: list[float] = []

    with db.SessionLocal() as session:
        # Warm the connection/index path so the benchmark mostly measures query
        # behavior rather than the first TLS/database handshake.
        normal_search(session, query="Luffy", game_slug="onepiece", limit=5)

        for case in CASES:
            started = time.perf_counter()
            rows = normal_search(session, query=case.query, game_slug="onepiece", limit=20)
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            elapsed_values.append(elapsed_ms)

            rank = _rank(case, rows)
            semantic_hit = rank is not None and rank <= case.max_rank
            exact_collector_singleton = True
            if case.kind == "exact_collector":
                exact_collector_singleton = len(rows) == 1 and rank == 1

            passed = semantic_hit and exact_collector_singleton
            results.append(
                {
                    "query": case.query,
                    "kind": case.kind,
                    "hard_gate": case.hard_gate,
                    "passed": passed,
                    "target_rank": rank,
                    "max_rank": case.max_rank,
                    "result_count": len(rows),
                    "elapsed_ms": round(elapsed_ms, 2),
                    "top": _compact_rows(rows),
                }
            )

    hard = [row for row in results if row["hard_gate"]]
    soft = [row for row in results if not row["hard_gate"]]
    hard_failures = [row for row in hard if not row["passed"]]

    sorted_elapsed = sorted(elapsed_values)
    p95_index = max(0, min(len(sorted_elapsed) - 1, round(0.95 * (len(sorted_elapsed) - 1))))
    soft_passed = sum(1 for row in soft if row["passed"])
    report = {
        "generated_at": generated_at,
        "database": "neon",
        "game": "onepiece",
        "case_count": len(results),
        "hard_gate": {
            "passed": len(hard) - len(hard_failures),
            "total": len(hard),
            "pass_rate": round((len(hard) - len(hard_failures)) / len(hard), 4) if hard else 1.0,
        },
        "soft_quality": {
            "passed": soft_passed,
            "total": len(soft),
            "pass_rate": round(soft_passed / len(soft), 4) if soft else 1.0,
        },
        "latency_ms": {
            "median": round(statistics.median(elapsed_values), 2),
            "p95": round(sorted_elapsed[p95_index], 2),
            "max": round(max(elapsed_values), 2),
        },
        "results": results,
        "status": "pass" if not hard_failures else "fail",
    }

    print(json.dumps(report, ensure_ascii=False, indent=2))

    if hard_failures:
        failed = ", ".join(f"{row['query']} (rank={row['target_rank']}, count={row['result_count']})" for row in hard_failures)
        raise AssertionError(f"Search V2 benchmark hard-gate failures: {failed}")

    return report


def main() -> int:
    run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
