#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path


DEFAULT_EXPECTED = {"pokemon": 48, "mtg": 24, "yugioh": 24, "onepiece": 12}


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _iter_ndjson(path: Path):
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            line = line.strip()
            if not line:
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise RuntimeError(f"Invalid NDJSON at {path}:{line_number}") from exc
            if isinstance(value, dict):
                yield value


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results-root", required=True, type=Path)
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--expected-json", default=json.dumps(DEFAULT_EXPECTED))
    args = parser.parse_args()
    expected = {str(k): int(v) for k, v in json.loads(args.expected_json).items()}
    args.out_dir.mkdir(parents=True, exist_ok=True)

    summaries: dict[tuple[str, int], dict] = {}
    duplicate_summaries: list[dict] = []
    for summary_path in args.results_root.rglob("summary.json"):
        summary = _read_json(summary_path)
        game = str(summary.get("game") or "")
        shard_index = int(summary.get("shard_index", -1))
        key = (game, shard_index)
        if key in summaries:
            duplicate_summaries.append({"game": game, "shard_index": shard_index, "path": str(summary_path)})
            continue
        summary["_artifact_dir"] = str(summary_path.parent)
        summaries[key] = summary

    missing_shards = []
    incomplete_shards = []
    wrong_shard_count = []
    for game, count in expected.items():
        for shard_index in range(count):
            summary = summaries.get((game, shard_index))
            if summary is None:
                missing_shards.append({"game": game, "shard_index": shard_index})
                continue
            if int(summary.get("shard_count") or -1) != count:
                wrong_shard_count.append(
                    {
                        "game": game,
                        "shard_index": shard_index,
                        "reported": summary.get("shard_count"),
                        "expected": count,
                    }
                )
            if summary.get("status") != "pass":
                incomplete_shards.append(
                    {
                        "game": game,
                        "shard_index": shard_index,
                        "status": summary.get("status"),
                        "final_error_objects": summary.get("final_error_objects"),
                        "source_identity_conflicts": summary.get("source_identity_conflicts"),
                    }
                )

    source_identity_owner: dict[tuple[str, str], str] = {}
    cross_shard_source_conflicts: list[dict] = []
    fingerprints_by_game: dict[str, set[str]] = defaultdict(set)
    descriptor_records_by_game: dict[str, int] = defaultdict(int)
    market_fingerprints: dict[tuple[str, str, str], set[str]] = defaultdict(set)
    market_sources: dict[tuple[str, str, str], set[str]] = defaultdict(set)
    market_claim_rows_by_game: dict[str, int] = defaultdict(int)

    for (game, shard_index), summary in summaries.items():
        artifact_dir = Path(summary["_artifact_dir"])
        descriptor_path = artifact_dir / "descriptors.ndjson"
        market_path = artifact_dir / "market_claims.ndjson"
        if descriptor_path.exists():
            for row in _iter_ndjson(descriptor_path):
                fingerprint = str(row.get("fingerprint") or "")
                source = str(row.get("source") or "")
                source_print_id = str(row.get("source_print_id") or "")
                if not fingerprint or not source or not source_print_id:
                    cross_shard_source_conflicts.append(
                        {
                            "type": "malformed_descriptor",
                            "game": game,
                            "shard_index": shard_index,
                            "row": row,
                        }
                    )
                    continue
                key = (source, source_print_id)
                previous = source_identity_owner.get(key)
                if previous is not None and previous != fingerprint:
                    cross_shard_source_conflicts.append(
                        {
                            "type": "cross_shard_source_identity_conflict",
                            "game": game,
                            "source": source,
                            "source_print_id": source_print_id,
                            "first_fingerprint": previous,
                            "second_fingerprint": fingerprint,
                            "shard_index": shard_index,
                        }
                    )
                else:
                    source_identity_owner[key] = fingerprint
                fingerprints_by_game[game].add(fingerprint)
                descriptor_records_by_game[game] += 1

        if market_path.exists():
            for row in _iter_ndjson(market_path):
                market = str(row.get("market") or "")
                external_id = str(row.get("external_product_id") or "")
                fingerprint = str(row.get("fingerprint") or "")
                if not market or not external_id or not fingerprint:
                    continue
                key = (game, market, external_id)
                market_fingerprints[key].add(fingerprint)
                market_sources[key].add(str(row.get("evidence_source") or "unknown"))
                market_claim_rows_by_game[game] += 1

    evidence_path = args.out_dir / "market_evidence.ndjson"
    relationship_counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    direct_products_by_game: dict[str, int] = defaultdict(int)
    with evidence_path.open("w", encoding="utf-8") as handle:
        for (game, market, external_id), fingerprints in sorted(market_fingerprints.items()):
            relationship = "exact_one_physical" if len(fingerprints) == 1 else "grouped_physical"
            relationship_counts[game][relationship] += 1
            direct_products_by_game[game] += 1
            record = {
                "game": game,
                "market": market,
                "external_product_id": external_id,
                "relationship": relationship,
                "physical_fingerprints": sorted(fingerprints),
                "evidence_sources": sorted(market_sources[(game, market, external_id)]),
            }
            handle.write(json.dumps(record, separators=(",", ":")) + "\n")

    totals_by_game = {}
    for game, count in expected.items():
        game_summaries = [summary for (g, _), summary in summaries.items() if g == game]
        totals_by_game[game] = {
            "expected_shards": count,
            "received_shards": len(game_summaries),
            "manifest_rows": sum(int(row.get("manifest_rows") or 0) for row in game_summaries),
            "completed_objects": sum(int(row.get("completed_objects") or 0) for row in game_summaries),
            "final_error_objects": sum(int(row.get("final_error_objects") or 0) for row in game_summaries),
            "descriptor_records": descriptor_records_by_game.get(game, 0),
            "unique_physical_fingerprints": len(fingerprints_by_game.get(game, set())),
            "market_claim_rows": market_claim_rows_by_game.get(game, 0),
            "direct_cardmarket_products": direct_products_by_game.get(game, 0),
            "market_relationships": dict(relationship_counts.get(game, {})),
            "production_writes": 0,
        }

    integrity_errors = (
        len(missing_shards)
        + len(incomplete_shards)
        + len(wrong_shard_count)
        + len(duplicate_summaries)
        + len(cross_shard_source_conflicts)
    )
    status = "pass" if integrity_errors == 0 else "fail"
    report = {
        "status": status,
        "certifier": "physical_identity_v2_shards",
        "expected": expected,
        "received_summary_files": len(summaries),
        "integrity_errors": integrity_errors,
        "missing_shards": missing_shards,
        "incomplete_shards": incomplete_shards,
        "wrong_shard_count": wrong_shard_count,
        "duplicate_summaries": duplicate_summaries,
        "cross_shard_source_conflicts": cross_shard_source_conflicts[:1000],
        "games": totals_by_game,
        "production_writes": 0,
        "coverage_gate_note": "This certifier gates complete lossless shadow ingestion. The >=99% Cardmarket accounting gate remains a later resolver/cutover certification.",
    }
    (args.out_dir / "summary.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print("IDENTITY_V2_GLOBAL_CERT=" + json.dumps({
        "status": status,
        "integrity_errors": integrity_errors,
        "games": totals_by_game,
        "production_writes": 0,
    }, separators=(",", ":")))
    return 0 if status == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
