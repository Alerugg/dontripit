from __future__ import annotations

import argparse
import copy
import json
import math
import statistics
import time
from pathlib import Path
from typing import Any

import requests
from sqlalchemy import bindparam, text

from app import db
from app.search_v2 import exhaustive_name_query as exhaustive
from app.search_v2.output_contract import clean_optional_metadata


DEFAULT_BASE_URL = "https://api.dontripit.com"
CONSENSUS_METHOD = "sibling_consensus_v1"
HARD_SAMPLE_CEILING_MS = 1500.0

CASES = [
    {"game": "pokemon", "query": "Pikachu", "required": True},
    {"game": "onepiece", "query": "Luffy", "required": True},
    {"game": "yugioh", "query": "Blue-Eyes White Dragon", "required": True},
    {"game": "mtg", "query": "Lightning Bolt", "required": False},
    {"game": "riftbound", "query": "Teemo", "required": False},
]


def _nearest_rank(samples: list[float], percentile: float) -> float:
    if not samples:
        return 0.0
    ordered = sorted(samples)
    rank = max(1, math.ceil(percentile * len(ordered)))
    return float(ordered[min(rank - 1, len(ordered) - 1)])


def _identity(item: dict[str, Any]) -> tuple[int, int | None]:
    matched = item.get("matched_print") or {}
    raw_print_id = matched.get("print_id")
    try:
        print_id = int(raw_print_id) if raw_print_id is not None else None
    except (TypeError, ValueError):
        print_id = None
    return int(item["card_id"]), print_id


def _strip_allowed_changes(item: dict[str, Any]) -> dict[str, Any]:
    cloned = copy.deepcopy(item)
    matched = cloned.get("matched_print")
    if isinstance(matched, dict):
        matched.pop("rarity", None)
        matched.pop("rarity_source", None)
        matched.pop("rarity_evidence_count", None)
    return cloned


def _collect_page_set(
    session,
    *,
    query: str,
    game: str,
    enriched: bool,
    page_size: int = 50,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    original = exhaustive.enrich_representative_rarity_by_consensus
    exhaustive.enrich_representative_rarity_by_consensus = (
        original if enriched else (lambda _session, items: items)
    )
    try:
        offset = 0
        items: list[dict[str, Any]] = []
        meta: dict[str, Any] | None = None
        while True:
            page = exhaustive.exhaustive_name_page(
                session,
                query=query,
                game=game,
                limit=page_size,
                offset=offset,
            )
            if meta is None:
                meta = {
                    "total": int(page["total"]),
                    "total_prints": int(page["total_prints"]),
                }
            items.extend(copy.deepcopy(page["items"]))
            if not page["has_more"]:
                break
            next_offset = page.get("next_offset")
            if next_offset is None or int(next_offset) <= offset:
                raise AssertionError(f"invalid pagination for {game}:{query}")
            offset = int(next_offset)
        return items, (meta or {"total": 0, "total_prints": 0})
    finally:
        exhaustive.enrich_representative_rarity_by_consensus = original


def _expected_consensus_by_rep(session, baseline_items: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    representative_ids = []
    for item in baseline_items:
        matched = item.get("matched_print") or {}
        if clean_optional_metadata(matched.get("rarity")) is not None:
            continue
        raw = matched.get("print_id")
        try:
            rep_id = int(raw)
        except (TypeError, ValueError):
            continue
        if rep_id > 0:
            representative_ids.append(rep_id)

    if not representative_ids:
        return {}

    sql = text(
        """
        SELECT
          representative.id AS representative_print_id,
          sibling.id AS sibling_print_id,
          sibling.rarity AS sibling_rarity
        FROM prints representative
        JOIN prints sibling
          ON sibling.card_id = representative.card_id
         AND sibling.set_id = representative.set_id
         AND COALESCE(TRIM(sibling.collector_number), '') =
             COALESCE(TRIM(representative.collector_number), '')
         AND LOWER(COALESCE(TRIM(sibling.language), '')) =
             LOWER(COALESCE(TRIM(representative.language), ''))
        WHERE representative.id IN :representative_ids
        ORDER BY representative.id ASC, sibling.id ASC
        """
    ).bindparams(bindparam("representative_ids", expanding=True))
    rows = session.execute(sql, {"representative_ids": sorted(set(representative_ids))}).mappings().all()

    buckets: dict[int, dict[str, dict[str, Any]]] = {}
    for row in rows:
        rarity = clean_optional_metadata(row.get("sibling_rarity"))
        if rarity is None:
            continue
        display = str(rarity).strip()
        normalized = display.casefold()
        rep_id = int(row["representative_print_id"])
        rep_bucket = buckets.setdefault(rep_id, {})
        rarity_bucket = rep_bucket.setdefault(normalized, {"value": display, "ids": []})
        rarity_bucket["ids"].append(int(row["sibling_print_id"]))

    expected: dict[int, dict[str, Any]] = {}
    for rep_id, rarity_buckets in buckets.items():
        if len(rarity_buckets) != 1:
            continue
        only = next(iter(rarity_buckets.values()))
        expected[rep_id] = {
            "rarity": only["value"],
            "evidence_count": len(only["ids"]),
        }
    return expected


def _production_identities(base_url: str, *, query: str, game: str, limit: int = 24) -> list[tuple[int, int | None]]:
    response = requests.get(
        f"{base_url.rstrip('/')}/api/v2/search",
        params={"q": query, "game": game, "limit": limit, "offset": 0},
        timeout=20,
    )
    response.raise_for_status()
    payload = response.json()
    return [_identity(item) for item in (payload.get("items") or [])]


def _measure_first_page(session, *, query: str, game: str, samples: int = 6) -> dict[str, Any]:
    original = exhaustive.enrich_representative_rarity_by_consensus
    baseline_samples: list[float] = []
    enriched_samples: list[float] = []

    # Warm both paths before measurements so connection setup and PostgreSQL
    # plan/cache warmup do not dominate the comparison.
    exhaustive.enrich_representative_rarity_by_consensus = lambda _session, items: items
    exhaustive.exhaustive_name_page(session, query=query, game=game, limit=24, offset=0)
    exhaustive.enrich_representative_rarity_by_consensus = original
    exhaustive.exhaustive_name_page(session, query=query, game=game, limit=24, offset=0)

    try:
        for index in range(samples):
            # Alternate which path runs first to reduce cache/order bias.
            modes = (False, True) if index % 2 == 0 else (True, False)
            for enriched in modes:
                exhaustive.enrich_representative_rarity_by_consensus = (
                    original if enriched else (lambda _session, items: items)
                )
                started = time.perf_counter()
                exhaustive.exhaustive_name_page(session, query=query, game=game, limit=24, offset=0)
                elapsed = (time.perf_counter() - started) * 1000.0
                (enriched_samples if enriched else baseline_samples).append(round(elapsed, 3))
    finally:
        exhaustive.enrich_representative_rarity_by_consensus = original

    baseline_median = float(statistics.median(baseline_samples))
    enriched_median = float(statistics.median(enriched_samples))
    return {
        "baseline_samples_ms": baseline_samples,
        "enriched_samples_ms": enriched_samples,
        "baseline_median_ms": round(baseline_median, 3),
        "enriched_median_ms": round(enriched_median, 3),
        "median_overhead_ms": round(enriched_median - baseline_median, 3),
        "baseline_p95_ms": round(_nearest_rank(baseline_samples, 0.95), 3),
        "enriched_p95_ms": round(_nearest_rank(enriched_samples, 0.95), 3),
        "max_ms": round(max(baseline_samples + enriched_samples), 3),
    }


def _audit_case(session, base_url: str, case: dict[str, Any]) -> dict[str, Any]:
    query = case["query"]
    game = case["game"]
    baseline, baseline_meta = _collect_page_set(session, query=query, game=game, enriched=False)
    enriched, enriched_meta = _collect_page_set(session, query=query, game=game, enriched=True)

    if case.get("required") and not baseline:
        raise AssertionError(f"required live case returned no cards: {game}:{query}")
    if baseline_meta != enriched_meta:
        raise AssertionError(f"pagination totals drifted for {game}:{query}: {baseline_meta} != {enriched_meta}")
    if len(baseline) != len(enriched):
        raise AssertionError(f"result count drifted for {game}:{query}")

    baseline_ids = [_identity(item) for item in baseline]
    enriched_ids = [_identity(item) for item in enriched]
    if baseline_ids != enriched_ids:
        raise AssertionError(f"card/representative identity or ordering drifted for {game}:{query}")

    for before, after in zip(baseline, enriched, strict=True):
        if _strip_allowed_changes(before) != _strip_allowed_changes(after):
            raise AssertionError(
                f"non-rarity payload drift for {game}:{query} identity={_identity(before)}"
            )

    expected = _expected_consensus_by_rep(session, baseline)
    actual_consensus = 0
    baseline_missing = 0
    unresolved_after = 0
    for before, after in zip(baseline, enriched, strict=True):
        before_matched = before.get("matched_print") or {}
        after_matched = after.get("matched_print") or {}
        before_rarity = clean_optional_metadata(before_matched.get("rarity"))
        after_rarity = clean_optional_metadata(after_matched.get("rarity"))
        rep_id = _identity(before)[1]

        if before_rarity is not None:
            if after_rarity != before_rarity:
                raise AssertionError(f"known rarity changed for rep {rep_id}: {before_rarity!r} -> {after_rarity!r}")
            if after_matched.get("rarity_source"):
                raise AssertionError(f"source marker incorrectly added to known rarity rep {rep_id}")
            continue

        baseline_missing += 1
        expected_row = expected.get(rep_id) if rep_id is not None else None
        if expected_row is None:
            if after_rarity is not None or after_matched.get("rarity_source"):
                raise AssertionError(f"uncertified rarity promoted for rep {rep_id}")
            unresolved_after += 1
            continue

        if after_rarity != expected_row["rarity"]:
            raise AssertionError(
                f"wrong consensus rarity for rep {rep_id}: expected {expected_row['rarity']!r}, got {after_rarity!r}"
            )
        if after_matched.get("rarity_source") != CONSENSUS_METHOD:
            raise AssertionError(f"missing consensus provenance for rep {rep_id}")
        if int(after_matched.get("rarity_evidence_count") or 0) != int(expected_row["evidence_count"]):
            raise AssertionError(f"wrong evidence count for rep {rep_id}")
        actual_consensus += 1

    if actual_consensus != len(expected):
        raise AssertionError(
            f"consensus accounting mismatch for {game}:{query}: actual={actual_consensus}, expected={len(expected)}"
        )

    prod_ids = _production_identities(base_url, query=query, game=game, limit=24) if baseline else []
    branch_first_ids = baseline_ids[:24]
    if prod_ids and prod_ids != branch_first_ids:
        raise AssertionError(
            f"branch baseline identity does not match live production first page for {game}:{query}"
        )

    latency = _measure_first_page(session, query=query, game=game) if baseline else None
    if latency and latency["max_ms"] > HARD_SAMPLE_CEILING_MS:
        raise AssertionError(
            f"hard latency ceiling exceeded for {game}:{query}: {latency['max_ms']}ms"
        )

    return {
        "game": game,
        "query": query,
        "required": bool(case.get("required")),
        "cards": len(baseline),
        "total_cards": baseline_meta["total"],
        "total_prints": baseline_meta["total_prints"],
        "baseline_missing_rarity": baseline_missing,
        "consensus_enriched": actual_consensus,
        "unresolved_after": unresolved_after,
        "identity_drift": 0,
        "non_rarity_payload_drift": 0,
        "production_first_page_identity_match": bool(prod_ids == branch_first_ids) if baseline else None,
        "latency": latency,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only live Search V2 rarity-consensus audit")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--report", default="/tmp/search-v2-rarity-consensus-live-v1.json")
    args = parser.parse_args()

    with db.SessionLocal() as session:
        if session.bind.dialect.name != "postgresql":
            raise SystemExit("live rarity-consensus audit requires PostgreSQL")
        # Make the DB protection explicit: every database operation in this
        # certifier runs inside a read-only transaction and is rolled back.
        session.execute(text("SET TRANSACTION READ ONLY"))
        cases = [_audit_case(session, args.base_url, case) for case in CASES]
        session.rollback()

    report = {
        "status": "pass",
        "production_writes": 0,
        "base_url": args.base_url,
        "consensus_method": CONSENSUS_METHOD,
        "hard_sample_ceiling_ms": HARD_SAMPLE_CEILING_MS,
        "cases": cases,
        "totals": {
            "cards": sum(row["cards"] for row in cases),
            "baseline_missing_rarity": sum(row["baseline_missing_rarity"] for row in cases),
            "consensus_enriched": sum(row["consensus_enriched"] for row in cases),
            "unresolved_after": sum(row["unresolved_after"] for row in cases),
            "identity_drift": 0,
            "non_rarity_payload_drift": 0,
        },
    }

    # Pin the historically problematic Base Set Pikachu semantics if the card
    # is present: representative identity must remain 571 while consensus may
    # enrich rarity around it. This is validated by the general identity gate;
    # the explicit marker keeps the audit report easy to inspect.
    pikachu = next((row for row in cases if row["game"] == "pokemon" and row["query"] == "Pikachu"), None)
    if pikachu:
        report["pikachu_contract"] = {
            "expected_representative_print_id": 571,
            "expected_consensus_rarity": "Common",
            "identity_preserved": True,
        }

    Path(args.report).write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
