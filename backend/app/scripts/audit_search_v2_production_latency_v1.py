from __future__ import annotations

import argparse
import hashlib
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
MAX_NETWORK_REQUESTS = 30
MAX_ATTEMPTS_PER_SAMPLE = 3
RETRYABLE_HTTP_STATUSES = {429, 502, 503, 504}
PLACEHOLDER_STRINGS = {
    "unknown",
    "n/a",
    "n.a.",
    "undefined",
    "null",
    "none",
    "not available",
    "tbd",
}


# Keep the broad semantic matrix, but spend repeated samples only on the five
# customer-critical regression probes. Twenty successful requests leave ten of
# the public 30-request window available for bounded transient retries.
CASES = [
    {
        "game": "pokemon",
        "query": "Pikachu",
        "kind": "core",
        "top_contains": "pikachu",
        "samples": 2,
        "top_metadata": {
            "card_id": 1,
            "print_id": 571,
            "set_code": "bs",
            "collector_number": "58",
            "rarity": "Common",
        },
    },
    {"game": "pokemon", "query": "svp-202", "kind": "core", "top_contains": "kangaskhan", "mode": "exact_identifier"},
    {"game": "pokemon", "query": "Pikchu", "kind": "fuzzy", "top_contains": "pikachu"},
    {"game": "mtg", "query": "Lightning Bolt", "kind": "core", "top_contains": "lightning bolt"},
    {"game": "mtg", "query": "lea-1", "kind": "core", "top_contains": "animate wall", "mode": "exact_identifier"},
    {"game": "mtg", "query": "Lightnng Bolt", "kind": "fuzzy", "top_contains": "lightning bolt"},
    {"game": "yugioh", "query": "Blue-Eyes White Dragon", "kind": "core", "top_contains": "blue-eyes white dragon"},
    {
        "game": "yugioh",
        "query": "LOB-001",
        "kind": "core",
        "top_contains": "blue-eyes white dragon",
        "mode": "exact_identifier",
        "samples": 2,
    },
    {"game": "yugioh", "query": "Blu-Eyes Wite Dragon", "kind": "fuzzy", "top_contains": "blue-eyes white dragon"},
    {"game": "yugioh", "query": "zznotrealcard991", "kind": "fuzzy", "expect_empty": True},
    {"game": "onepiece", "query": "Luffy", "kind": "core", "top_contains": "luffy", "samples": 2},
    {"game": "onepiece", "query": "P-150", "kind": "core", "top_collector": "P-150", "mode": "exact_identifier", "samples": 2},
    {"game": "onepiece", "query": "OP05-119", "kind": "core", "top_collector": "OP05-119", "mode": "exact_identifier", "samples": 2},
    {"game": "onepiece", "query": "Lufy", "kind": "fuzzy", "top_contains": "luffy"},
    {"game": "onepiece", "query": "zznotrealcard991", "kind": "fuzzy", "expect_empty": True},
]


def _latency_ms(response: requests.Response) -> int:
    raw = response.headers.get("x-app-response-time-ms")
    if raw is None:
        raise RuntimeError("production response missing x-app-response-time-ms")
    return int(float(raw))


def _compact_identifier(value: object) -> str:
    return "".join(ch for ch in str(value or "").casefold() if ch.isalnum())


def _is_placeholder(value: object) -> bool:
    return isinstance(value, str) and value.strip().casefold() in PLACEHOLDER_STRINGS


def _assert_required_value(value: object, *, label: str) -> None:
    if value is None or value == "" or _is_placeholder(value):
        raise AssertionError(f"required search metadata {label} is missing/placeholder: {value!r}")


def _stable_item(item: dict) -> dict:
    matched = item.get("matched_print") or item
    return {
        "card_id": item.get("card_id"),
        "card_key": item.get("card_key"),
        "name": item.get("name"),
        "game": item.get("game"),
        "print_id": item.get("print_id") or matched.get("print_id"),
        "set_code": matched.get("set_code"),
        "collector_number": matched.get("collector_number"),
        "language": matched.get("language"),
        "rarity": matched.get("rarity"),
        "exact_variant": matched.get("exact_variant"),
        "variant_family": matched.get("variant_family"),
    }


def _semantic_fingerprint(payload: dict) -> str:
    stable = {
        "pagination_mode": payload.get("pagination_mode"),
        "count": payload.get("count"),
        "total": payload.get("total"),
        "items": [_stable_item(item) for item in (payload.get("items") or [])],
    }
    encoded = json.dumps(stable, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _validate_payload(case: dict, payload: dict) -> None:
    items = payload.get("items") or []
    if not isinstance(items, list):
        raise AssertionError(f"items must be a list for {case['game']}:{case['query']}")
    if payload.get("count") != len(items):
        raise AssertionError(
            f"count/items mismatch for {case['game']}:{case['query']}: "
            f"count={payload.get('count')!r} items={len(items)}"
        )

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

    # Every result exposed by Search V2 represents a real logical Card with a
    # concrete physical Print. Optional descriptive fields may be null, but
    # identity fields must never silently degrade to placeholders.
    seen_physical: set[tuple[object, object]] = set()
    for index, item in enumerate(items):
        matched = item.get("matched_print") or item
        required = {
            "card_id": item.get("card_id"),
            "name": item.get("name"),
            "game": item.get("game"),
            "print_id": item.get("print_id") or matched.get("print_id"),
            "set_code": matched.get("set_code"),
            "collector_number": matched.get("collector_number"),
        }
        for field, value in required.items():
            _assert_required_value(value, label=f"items[{index}].{field}")
        if str(item.get("game") or "").casefold() != case["game"].casefold():
            raise AssertionError(
                f"cross-game leakage for {case['game']}:{case['query']}: item game={item.get('game')!r}"
            )
        physical_key = (item.get("card_id"), item.get("print_id") or matched.get("print_id"))
        if physical_key in seen_physical:
            raise AssertionError(f"duplicate physical result for {case['game']}:{case['query']}: {physical_key}")
        seen_physical.add(physical_key)

    top_collector = case.get("top_collector")
    if top_collector:
        top_matched = items[0].get("matched_print") or items[0]
        actual_collector = top_matched.get("collector_number")
        if _compact_identifier(actual_collector) != _compact_identifier(top_collector):
            raise AssertionError(
                f"unexpected top collector for {case['game']}:{case['query']}: "
                f"{actual_collector!r}"
            )

    top_contains = case.get("top_contains")
    if top_contains:
        top_name = str(items[0].get("name") or "").casefold()
        if top_contains.casefold() not in top_name:
            raise AssertionError(
                f"unexpected top result for {case['game']}:{case['query']}: {items[0].get('name')!r}"
            )

    expected_metadata = case.get("top_metadata") or {}
    if expected_metadata:
        top = items[0]
        matched = top.get("matched_print") or top
        actual = {
            "card_id": top.get("card_id"),
            "print_id": top.get("print_id") or matched.get("print_id"),
            "set_code": matched.get("set_code"),
            "collector_number": matched.get("collector_number"),
            "rarity": matched.get("rarity"),
        }
        for field, expected in expected_metadata.items():
            if str(actual.get(field)) != str(expected):
                raise AssertionError(
                    f"metadata regression for {case['game']}:{case['query']} {field}: "
                    f"expected {expected!r}, got {actual.get(field)!r}"
                )


def _retry_delay_seconds(response: requests.Response | None, attempt: int) -> float:
    if response is not None:
        raw = response.headers.get("Retry-After")
        if raw:
            try:
                return min(2.0, max(0.0, float(raw)))
            except ValueError:
                pass
    return min(1.0, 0.2 * (2 ** max(0, attempt - 1)))


def _request_with_retry(
    http,
    url: str,
    *,
    request_state: dict,
    sleep_fn=time.sleep,
) -> tuple[requests.Response, list[dict]]:
    attempts: list[dict] = []
    last_error: Exception | None = None

    for attempt in range(1, MAX_ATTEMPTS_PER_SAMPLE + 1):
        if request_state["used"] >= MAX_NETWORK_REQUESTS:
            raise RuntimeError(
                f"certifier network request budget exhausted ({MAX_NETWORK_REQUESTS}); "
                "transient instability consumed the reserved retry capacity"
            )
        request_state["used"] += 1
        response = None
        try:
            response = http.get(url, timeout=REQUEST_TIMEOUT_SECONDS)
            status_code = int(response.status_code)
            if status_code not in RETRYABLE_HTTP_STATUSES:
                response.raise_for_status()
                attempts.append({"attempt": attempt, "status_code": status_code, "transient": False})
                return response, attempts

            attempts.append({"attempt": attempt, "status_code": status_code, "transient": True})
            last_error = requests.HTTPError(f"transient HTTP {status_code}", response=response)
        except (requests.Timeout, requests.ConnectionError) as exc:
            last_error = exc
            attempts.append(
                {
                    "attempt": attempt,
                    "status_code": None,
                    "transient": True,
                    "error": type(exc).__name__,
                }
            )
        except requests.RequestException:
            # 4xx (except 429) and non-retryable HTTP failures are deterministic
            # certifier failures; retrying them would hide a real contract issue.
            raise

        if attempt < MAX_ATTEMPTS_PER_SAMPLE:
            sleep_fn(_retry_delay_seconds(response, attempt))

    assert last_error is not None
    raise RuntimeError(
        f"transient request did not recover after {MAX_ATTEMPTS_PER_SAMPLE} attempts: {last_error}"
    ) from last_error


def _run_case(base_url: str, case: dict, *, http, request_state: dict, sleep_fn=time.sleep) -> dict:
    params = urlencode({"q": case["query"], "game": case["game"], "limit": 24})
    url = f"{base_url.rstrip('/')}/api/v2/search?{params}"
    samples: list[int] = []
    response_modes: list[str | None] = []
    fingerprints: list[str] = []
    attempt_history: list[list[dict]] = []
    sample_count = max(1, int(case.get("samples", 1)))

    for sample_index in range(sample_count):
        response, attempts = _request_with_retry(
            http,
            url,
            request_state=request_state,
            sleep_fn=sleep_fn,
        )
        payload = response.json()
        _validate_payload(case, payload)
        samples.append(_latency_ms(response))
        response_modes.append(payload.get("pagination_mode"))
        fingerprints.append(_semantic_fingerprint(payload))
        attempt_history.append(attempts)
        if sample_index + 1 < sample_count:
            sleep_fn(0.12)

    unique_fingerprints = sorted(set(fingerprints))
    if len(unique_fingerprints) != 1:
        raise AssertionError(
            f"non-deterministic response fingerprint for {case['game']}:{case['query']}: "
            f"{unique_fingerprints}"
        )

    median_ms = float(statistics.median(samples))
    max_ms = max(samples)
    budget_ms = CORE_BUDGET_MS if case["kind"] == "core" else FUZZY_BUDGET_MS
    performance_status = "pass" if median_ms <= budget_ms and max_ms <= HARD_SAMPLE_CEILING_MS else "fail"
    transient_attempts = sum(
        1
        for sample_attempts in attempt_history
        for attempt in sample_attempts
        if attempt.get("transient")
    )
    return {
        "game": case["game"],
        "query": case["query"],
        "kind": case["kind"],
        "budget_ms": budget_ms,
        "hard_sample_ceiling_ms": HARD_SAMPLE_CEILING_MS,
        "successful_samples": sample_count,
        "samples_ms": samples,
        "median_ms": median_ms,
        "max_ms": max_ms,
        "pagination_modes": response_modes,
        "semantic_fingerprint": unique_fingerprints[0],
        "transient_attempts": transient_attempts,
        "attempt_history": attempt_history,
        "status": performance_status,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only production Search V2 latency/quality/determinism audit")
    parser.add_argument("--base-url", default="https://api.dontripit.com")
    parser.add_argument("--report", default="/tmp/search-v2-production-latency-v1.json")
    args = parser.parse_args()

    request_state = {"used": 0}
    rows = []
    for case in CASES:
        try:
            rows.append(
                _run_case(
                    args.base_url,
                    case,
                    http=requests.Session(),
                    request_state=request_state,
                )
            )
        except Exception as exc:
            rows.append(
                {
                    "game": case["game"],
                    "query": case["query"],
                    "kind": case["kind"],
                    "budget_ms": CORE_BUDGET_MS if case["kind"] == "core" else FUZZY_BUDGET_MS,
                    "hard_sample_ceiling_ms": HARD_SAMPLE_CEILING_MS,
                    "status": "fail",
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                }
            )

    successful_samples = [
        sample
        for row in rows
        for sample in row.get("samples_ms", [])
    ]
    transient_attempts = sum(int(row.get("transient_attempts") or 0) for row in rows)
    failed_rows = [row for row in rows if row.get("status") != "pass"]
    report = {
        "status": "pass" if not failed_rows else "fail",
        "stability_status": "recovered_transients" if transient_attempts and not failed_rows else ("fail" if failed_rows else "clean"),
        "production_writes": 0,
        "base_url": args.base_url,
        "planned_success_samples": sum(max(1, int(case.get("samples", 1))) for case in CASES),
        "network_request_budget": MAX_NETWORK_REQUESTS,
        "network_requests_used": request_state["used"],
        "retry_capacity_reserved": MAX_NETWORK_REQUESTS - sum(max(1, int(case.get("samples", 1))) for case in CASES),
        "transient_attempts": transient_attempts,
        "core_budget_ms": CORE_BUDGET_MS,
        "fuzzy_budget_ms": FUZZY_BUDGET_MS,
        "hard_sample_ceiling_ms": HARD_SAMPLE_CEILING_MS,
        "active_sample_summary": {
            "successful_sample_count": len(successful_samples),
            "median_ms": float(statistics.median(successful_samples)) if successful_samples else None,
            "max_ms": max(successful_samples) if successful_samples else None,
            "note": "No p95/p99 is asserted from this small active sample; accumulated production metrics own percentile certification.",
        },
        "cases": rows,
    }
    Path(args.report).parent.mkdir(parents=True, exist_ok=True)
    Path(args.report).write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps(report, indent=2, sort_keys=True))

    if failed_rows:
        failed = [
            f"{row['game']}:{row['query']}={row.get('samples_ms') or row.get('error')}"
            for row in failed_rows
        ]
        raise SystemExit("Search V2 production certification failed: " + ", ".join(failed))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
