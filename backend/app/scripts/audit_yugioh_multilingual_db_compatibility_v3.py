#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from app.scripts import audit_yugioh_multilingual_db_compatibility_v2 as v2

# Exact physical collector recovery for the eight historical YGOJSON rows whose
# suffix was stored as '-01'..'-04'. These are NOT generic prefix guesses.
# audit_yugioh_missing_set_identity_bridge v2 proves, card-by-card via official
# Konami IDs against current YAML Yugi, one unique 4/4 family for each group;
# the same WJ/VJ prefixes are independently present in YGOJSON locale metadata.
PRINT_COLLECTOR_OVERRIDES = {
    # Weekly Shonen Jump promotional cards: YAML Yugi WJ-01 .. WJ-04.
    "600984ec-0fc8-4605-a74b-5427cd1b23a0": "WJ-01",
    "bede3bc5-8752-4919-b615-7a88eb08896d": "WJ-02",
    "6ff126c5-a653-4942-9124-b860dbdca0f7": "WJ-03",
    "a623d1e1-6a7a-441c-b029-940d8305688d": "WJ-04",
    # V Jump promotional cards: YAML Yugi VJ-01 .. VJ-04.
    "dd16b07d-81d4-4cbb-aec9-81d7b22bce35": "VJ-01",
    "04b3dbdc-557e-4fcb-b605-6008b3041e38": "VJ-02",
    "ba4611db-aa03-4d0e-842a-c86d2cfca288": "VJ-03",
    "701f33c6-a5ff-4b56-a621-8dc70df24940": "VJ-04",
}

_ORIGINAL_BUILD_SOURCE = v2.build_source


def build_source_with_exact_collector_recovery(root: Path):
    cards, source_rows, legacy_quarantine = _ORIGINAL_BUILD_SOURCE(root)
    seen: dict[str, str] = {}
    for target, rows in source_rows.items():
        for row in rows:
            print_uuid = v2.s(row.get("print_uuid"))
            recovered = PRINT_COLLECTOR_OVERRIDES.get(print_uuid)
            if not recovered:
                continue
            row["collector"] = recovered
            row["family"] = recovered.split("-", 1)[0]
            row["quality"] = "exact"
            seen[print_uuid] = target
    missing = sorted(set(PRINT_COLLECTOR_OVERRIDES) - set(seen))
    if missing:
        raise AssertionError(f"certified collector overrides absent from source snapshot: {missing}")
    return cards, source_rows, legacy_quarantine


def run(root: Path, report_path: Path) -> dict[str, Any]:
    # v2.run deliberately obtains build_source from its module global. Replace
    # it only for this process so all existing v2 DB/read-only gates remain
    # unchanged and the eight certified rows are evaluated like normal rows.
    old = v2.build_source
    try:
        v2.build_source = build_source_with_exact_collector_recovery
        report = v2.run(root, report_path)
    finally:
        v2.build_source = old

    report["mode"] = "read_only_ygojson_regional_writer_compatibility_v3"
    report["exact_collector_recovery"] = {
        "policy": "explicit print UUID -> collector only; evidence is exact Konami-ID overlap in current YAML Yugi plus matching YGOJSON locale prefix; no name/fuzzy inference",
        "count": len(PRINT_COLLECTOR_OVERRIDES),
        "collectors": dict(sorted(PRINT_COLLECTOR_OVERRIDES.items())),
        "source_diagnostic": "audit_yugioh_missing_set_identity_bridge_v2",
    }
    report["gates"]["all_exact_collector_overrides_present"] = True
    report["structural_pass"] = all(report["gates"].values())
    report["production_rollout_ready"] = bool(
        report["structural_pass"]
        and report["rollout_freshness_pass"]
        and report["database_schema"]["sets_region_column_present"]
    )
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({
        "mode": report["mode"],
        "structural_pass": report["structural_pass"],
        "production_rollout_ready": report["production_rollout_ready"],
        "exact_collector_recovery_count": len(PRINT_COLLECTOR_OVERRIDES),
    }, ensure_ascii=False, sort_keys=True))
    return report


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input-dir", type=Path, required=True)
    ap.add_argument("--report", type=Path, required=True)
    args = ap.parse_args()
    report = run(args.input_dir, args.report)
    return 0 if report["structural_pass"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
