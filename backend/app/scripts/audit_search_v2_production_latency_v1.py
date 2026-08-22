from __future__ import annotations

import argparse
import json
import statistics
import time
from pathlib import Path
from urllib.parse import urlencode

import requests


CORE_BUDGET_MS = 300
FUZZY_BUDGET_MS = 500
HARD_SAMPLE_CEILING_MS = 1500
REQUEST_TIMEOUT_SECONDS = 12


CASES = [
    {"game": "pokemon", "query": "Pikachu", "kind": "core", "top_contains": "pikachu"},
    {"game": "pokemon", "query": "svp-202", "kind": "core", "top_contains": "kangaskhan", "mode": "exact_identifier"},
    {"game": "pokemon", "query": "Pikchu", "kind": "fuzzy", "top_contains": "pikachu"},
    {"game": "mtg", "query": "Lightning Bolt", "kind": "core", "top_contains": "lightning bolt"},
    {"game": "mtg", "query": "lea-1", "kind": "core", "top_contains": "animate wall", "mode": "exact_identifier"},
    {"game": "mtg", "query": "Lightnng Bolt", "kind": "fuzzy", "top_contains": "lightning bolt"},
    {"game": "yugioh", "query": "Blue-Eyes White Dragon", "kind": "core", "top_contains": "blue-eyes white dragon"},
    # Yu-Gi-Oh exact collectors are resolved inside its specialized ranked
    # engine; the physical result is exact even though the outer API mode is
    # currently labelled ranked_fallback.
    {"game": "yugioh", "query": "LOB-001", "kind": "core", "top_contains": "blue-eyes white dragon"},
    {"game": "yugioh", "query": "Blu-Eyes Wite Dragon", "kind": "fuzzy", "top_contains": "blue-eyes white dragon"},
    {"game": "yugioh", "query": "zznotrealcard991", "kind": "fuzzy", "expect_empty": True},
    {"game": "onepiece", "query": "Luffy", "kind": "core", "top_contains": "luffy"},
    {"game": "onepiece", "query": "P-135", "kind": "core", "mode": "exact_identifier"},
    {"game": "onepiece", "query": "Lufy", "kind": "fuzzy", "top_contains": "luffy"},
    {"game": "onepiece", "query": "zznotrealcard991", "kind": "fuzzy", "expect_empty": True},
]


def _latency_ms(response: requests.Response) -> int:
    raw = response.headers.get("x-app-response-time-ms")
    if raw is None:
        raise RuntimeError("production response missing x-app-response-time-ms")
    return int(float(raw))


def _validate_payload(case: dict, payload: dict) -> None:
    items = payload.get("items") or []
    if case.get("expect_empty"):
        if payload.get("count") != 0 or items:
            raise AssertionError(f"expected empty result for {case['game']}:{case['query']}")
        return

    if not items:
        raise AssertionError(f"expected result for {case['game']}:{case['query']}")

    expected_mode = case.get("mode")
    if expected_mode and payload.get("pagination_mode") != expected_mode:
        raise AssertionError(
            f"expected mode {expected_mode} for {case['game']}:{case['query']}, "
            f"got {payload.get('pagination_mode')}"
        )

    top_contains = case.get("top_contains")
    if top_contains:
        top_name = str(items[0].get("name") or "").casefold()
        if top_contains.casefold() not in top_name:
            raise AssertionError(
                f"unexpected top result for {case['game']}:{case['query']}: {items[0].get('name')!r}"
            )


def _run_case(base_url: str, case: dict) -> dict:
    params = urlencode({"q": case["query"], "game": case["game"], "limit": 24})
    url = f"{base_url.rstrip('/')}/api/v2/search?{params}"
    samples: list[int] = []
    response_modes: list[str | None] = []

    # Two samples keeps the complete audit below the public 30-request window,
    # while the median smooths one cold-start spike. A separate hard ceiling
    # still catches pathological regressions such as the historical 2-7s paths.
    for sample_index in range(2):
        response = requests.get(url, timeout=REQUEST_TIMEOUT_SECONDS)
        response.raise_for_status()
        payload = response.json()
        _validate_payload(case, payload)
        samples.append(_latency_ms(response))
        response_modes.append(payload.get("pagination_mode"))
        if sample_index == 0:
            time.sleep(0.12)

    median_ms = float(statistics.median(samples))
    max_ms = max(samples)
    budget_ms = CORE_BUDGET_MS if case["kind"] == "core" else FUZZY_BUDGET_MS
    status = "pass" if median_ms <= budget_ms and max_ms <= HARD_SAMPLE_CEILING_MS else "fail"
    return {
        "game": case["game"],
        "query": case["query"],
        "kind": case["kind"],
        "budget_ms": budget_ms,
        "hard_sample_ceiling_ms": HARD_SAMPLE_CEILING_MS,
        "samples_ms": samples,
        "median_ms": median_ms,
        "max_ms": max_ms,
        "pagination_modes": response_modes,
        "status": status,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only production Search V2 latency audit")
    parser.add_argument("--base-url", default="https://api.dontripit.com")
    parser.add_argument("--report", default="/tmp/search-v2-production-latency-v1.json")
    args = parser.parse_args()

    rows = [_run_case(args.base_url, case) for case in CASES]
    report = {
        "status": "pass" if all(row["status"] == "pass" for row in rows) else "fail",
        "production_writes": 0,
        "base_url": args.base_url,
        "core_budget_ms": CORE_BUDGET_MS,
        "fuzzy_budget_ms": FUZZY_BUDGET_MS,
        "hard_sample_ceiling_ms": HARD_SAMPLE_CEILING_MS,
        "cases": rows,
    }
    Path(args.report).write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps(report, indent=2, sort_keys=True))
    if report["status"] != "pass":
        failed = [f"{row['game']}:{row['query']}={row['samples_ms']}ms" for row in rows if row["status"] != "pass"]
        raise SystemExit("Search V2 production latency gate failed: " + ", ".join(failed))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
