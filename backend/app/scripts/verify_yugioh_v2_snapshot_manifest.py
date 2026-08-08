from __future__ import annotations

import argparse
import json
from pathlib import Path


FROZEN_COUNTS = {
    "source_cards": 14480,
    "sets": 646,
    "canonical_cards": 14479,
    "prints": 44226,
    "releases": 1032,
    "print_releases": 44226,
    "card_attributes": 14479,
    "print_attributes": 44226,
    "representative_print_images": 44226,
    "artwork_candidates": 14644,
    "cards_without_print_evidence": 490,
    "noisy_rarity_rows": 206,
    "no_hyphen_family_fallback_rows": 12,
    "source_card_aliases_merged": 1,
    "excluded_source_print_rows": 9,
    "deduplicated_source_print_rows": 52,
}

EXPECTED_MODE = "deterministic_yugioh_v2_canonical_snapshot_no_database_writes"


def run(manifest_path: Path) -> dict:
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    if payload.get("status") != "pass":
        raise AssertionError(f"Snapshot status is not pass: {payload.get('status')!r}")
    if payload.get("mode") != EXPECTED_MODE:
        raise AssertionError(f"Snapshot mode moved: {payload.get('mode')!r}")
    if int(payload.get("database_writes") or 0) != 0:
        raise AssertionError("Snapshot builder must remain database-write free")
    if payload.get("constraint_simulation") != "pass":
        raise AssertionError("Snapshot constraint simulation is not pass")

    counts = payload.get("counts") or {}
    moved = {
        key: {"expected": expected, "actual": counts.get(key)}
        for key, expected in FROZEN_COUNTS.items()
        if counts.get(key) != expected
    }
    if moved:
        raise AssertionError(f"Frozen Yu-Gi-Oh V2 counts moved: {moved}")

    source = payload.get("source") or {}
    version_rows = source.get("database_version") or []
    if not isinstance(version_rows, list) or not version_rows:
        raise AssertionError("Snapshot is missing YGOPRODeck database version evidence")

    result = {
        "status": "pass",
        "frozen_counts": FROZEN_COUNTS,
        "source_database_version": version_rows,
        "snapshot_bytes_uncompressed": payload.get("snapshot_bytes_uncompressed"),
        "constraint_simulation": payload.get("constraint_simulation"),
        "database_writes": payload.get("database_writes"),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest_path", type=Path)
    args = parser.parse_args()
    run(args.manifest_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
