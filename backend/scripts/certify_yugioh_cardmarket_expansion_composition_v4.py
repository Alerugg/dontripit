#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path


PROFILE = {
    "min_overlap": 8,
    "min_cm_coverage": 0.95,
    "min_f1": 0.90,
    "min_margin_f1": 0.10,
}


def accepts(row: dict) -> bool:
    return (
        row.get("best_pid") is not None
        and int(row.get("overlap") or 0) >= PROFILE["min_overlap"]
        and float(row.get("cm_coverage") or 0.0) >= PROFILE["min_cm_coverage"]
        and float(row.get("f1") or 0.0) >= PROFILE["min_f1"]
        and float(row.get("margin_f1") or 0.0) >= PROFILE["min_margin_f1"]
        and not bool(row.get("tied_best"))
    )


def evidence(row: dict) -> dict:
    return {
        "cardmarket_expansion_id": str(row["expansion"]),
        "konami_pid": str(row["best_pid"]),
        "konami_release_name": str(row.get("best_release_name") or ""),
        "cm_unique_names": int(row.get("cm_unique_names") or 0),
        "official_unique_names": int(row.get("official_unique_names") or 0),
        "overlap": int(row.get("overlap") or 0),
        "cm_coverage": float(row.get("cm_coverage") or 0.0),
        "official_recall": float(row.get("official_recall") or 0.0),
        "f1": float(row.get("f1") or 0.0),
        "jaccard": float(row.get("jaccard") or 0.0),
        "runner_pid": row.get("runner_pid"),
        "runner_release_name": row.get("runner_release_name"),
        "margin_f1": float(row.get("margin_f1") or 0.0),
        "min_date_added": row.get("min_date_added"),
        "max_date_added": row.get("max_date_added"),
        "is_gold": bool(row.get("is_gold")),
        "method": "card_name_composition_fixed_profile_v4",
    }


def _int_counter(mapping: dict, key: str, default: int) -> int:
    value = mapping.get(key)
    return default if value is None else int(value)


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Certify the fixed, conservative YGO Cardmarket expansion composition profile. Read-only."
    )
    ap.add_argument("calibration_report", type=Path)
    ap.add_argument("--report", type=Path, required=True)
    args = ap.parse_args()

    source = json.loads(args.calibration_report.read_text(encoding="utf-8"))
    if source.get("mode") != "read_only_calibration":
        raise SystemExit("Expected a read_only_calibration V4 report")
    if source.get("game") != "yugioh":
        raise SystemExit("Expected a yugioh V4 report")

    source_summary = source.get("summary") or {}
    if _int_counter(source_summary, "production_writes", -1) != 0:
        raise SystemExit("Calibration report is not proven read-only")
    if _int_counter(source_summary, "raw_gold_wrong", -1) != 0:
        raise SystemExit("Raw composition top-1 has a goldset error; refusing certification")
    if _int_counter(source_summary, "raw_gold_correct", 0) < 300:
        raise SystemExit("Insufficient raw gold coverage for V4 certification")

    predictions = list(source.get("predictions") or [])
    accepted = [row for row in predictions if accepts(row)]
    gold = [row for row in accepted if row.get("is_gold")]
    new = [row for row in accepted if not row.get("is_gold")]
    wrong_gold = [row for row in gold if row.get("gold_correct") is not True]

    by_expansion: dict[str, set[str]] = {}
    for row in accepted:
        by_expansion.setdefault(str(row["expansion"]), set()).add(str(row["best_pid"]))
    contradictory_expansions = {
        expansion: sorted(pids)
        for expansion, pids in by_expansion.items()
        if len(pids) != 1
    }

    certification_passed = (
        len(gold) >= 200
        and not wrong_gold
        and not contradictory_expansions
        and len(new) > 0
    )

    payload = {
        "mode": "read_only_certification",
        "game": "yugioh",
        "resolver": "cardmarket_konami_expansion_composition_v4",
        "fixed_profile": PROFILE,
        "summary": {
            "raw_gold_predictions": _int_counter(source_summary, "gold_predictions", 0),
            "raw_gold_correct": _int_counter(source_summary, "raw_gold_correct", 0),
            "raw_gold_wrong": _int_counter(source_summary, "raw_gold_wrong", 0),
            "fixed_profile_gold_accepted": len(gold),
            "fixed_profile_gold_correct": len(gold) - len(wrong_gold),
            "fixed_profile_gold_wrong": len(wrong_gold),
            "fixed_profile_gold_precision": ((len(gold) - len(wrong_gold)) / len(gold)) if gold else None,
            "certified_new_expansions": len(new),
            "certified_total_expansions": len(accepted),
            "contradictory_expansions": len(contradictory_expansions),
            "certification_passed": certification_passed,
            "production_writes": 0,
        },
        "wrong_gold": [evidence(row) for row in wrong_gold],
        "contradictory_expansions": contradictory_expansions,
        "certified_new_expansions": [evidence(row) for row in new],
        "certified_gold_expansions": [evidence(row) for row in gold],
        "certified_expansions": [evidence(row) for row in accepted],
    }

    args.report.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print("YGO_EXPANSION_COMPOSITION_V4_CERT=" + json.dumps(payload["summary"], separators=(",", ":")))
    return 0 if certification_passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
