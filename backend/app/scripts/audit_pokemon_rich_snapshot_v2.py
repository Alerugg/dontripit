from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from sqlalchemy import text

from app import db
from app.pokemon_source_inventory import load_inventory


def load_snapshot(path: Path) -> dict[str, dict]:
    rows: dict[str, dict] = {}
    duplicates: list[str] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            clean = line.strip()
            if not clean:
                continue
            row = json.loads(clean)
            source_id = str(row.get("source_id") or "").strip()
            if not source_id:
                raise AssertionError(f"Snapshot row {line_number} has no source_id")
            if source_id in rows:
                duplicates.append(source_id)
            rows[source_id] = row
    if duplicates:
        raise AssertionError(f"Snapshot has duplicate IDs: {sorted(set(duplicates))[:20]}")
    return rows


def run(snapshot_path: Path, manifest_path: Path) -> dict:
    snapshot = load_snapshot(snapshot_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("status") != "pass":
        raise AssertionError("Rich snapshot exporter did not pass")
    if int(manifest.get("physical_cards") or 0) != len(snapshot):
        raise AssertionError("Snapshot manifest/card row count mismatch")

    inventory = load_inventory()
    baseline_ids = set(inventory.physical_cards)
    snapshot_ids = set(snapshot)

    db.init_engine()
    with db.SessionLocal() as session:
        game_id = session.execute(text("SELECT id FROM games WHERE slug='pokemon' LIMIT 1")).scalar_one()
        neon_rows = session.execute(text(
            "SELECT id, tcgdex_id, name FROM cards WHERE game_id=:game AND tcgdex_id IS NOT NULL"
        ), {"game": game_id}).mappings().all()
        neon_by_source = {str(row["tcgdex_id"]): dict(row) for row in neon_rows}

    neon_ids = set(neon_by_source)
    mapped = snapshot_ids & neon_ids
    snapshot_not_in_neon = sorted(snapshot_ids - neon_ids)
    snapshot_not_in_baseline = sorted(snapshot_ids - baseline_ids)
    baseline_missing_snapshot = sorted(baseline_ids - snapshot_ids)

    name_mismatches = []
    for source_id in sorted(mapped):
        source_name = str(snapshot[source_id].get("name") or "").strip().casefold()
        neon_name = str(neon_by_source[source_id].get("name") or "").strip().casefold()
        if source_name and neon_name and source_name != neon_name:
            name_mismatches.append({
                "source_id": source_id,
                "snapshot_name": snapshot[source_id].get("name"),
                "neon_name": neon_by_source[source_id].get("name"),
            })

    field_coverage = Counter()
    variant_shapes = Counter()
    variant_type_counts = Counter()
    stamp_counts = Counter()
    foil_pattern_counts = Counter()
    detailed_variant_cards = 0
    total_detailed_variants = 0

    for row in snapshot.values():
        attrs = row.get("attributes") or {}
        for key in (
            "category", "rarity", "illustrator", "regulation_mark", "dex_id",
            "hp", "types", "stage", "suffix", "trainer_type", "energy_type",
            "abilities", "attacks", "weaknesses", "resistances", "retreat", "variants",
        ):
            value = attrs.get(key)
            present = len(value) > 0 if isinstance(value, (list, dict)) else value not in (None, "")
            if present:
                field_coverage[key] += 1

        shape = str(attrs.get("variant_shape") or "missing")
        variant_shapes[shape] += 1
        variants = attrs.get("variants")
        if isinstance(variants, list):
            detailed_variant_cards += 1
            total_detailed_variants += len(variants)
            for variant in variants:
                if not isinstance(variant, dict):
                    continue
                variant_type_counts[str(variant.get("type") or "missing")] += 1
                for stamp in variant.get("stamp") or []:
                    stamp_counts[str(stamp)] += 1
                if variant.get("foil"):
                    foil_pattern_counts[str(variant.get("foil"))] += 1
        elif isinstance(variants, dict):
            for key, enabled in variants.items():
                if enabled is True:
                    variant_type_counts[f"legacy:{key}"] += 1

    hard_failures = []
    if snapshot_not_in_neon:
        hard_failures.append(f"{len(snapshot_not_in_neon)} snapshot IDs are not present in Neon")
    if snapshot_not_in_baseline:
        hard_failures.append(f"{len(snapshot_not_in_baseline)} snapshot IDs are outside current physical baseline")
    if manifest.get("duplicate_source_ids"):
        hard_failures.append("snapshot exporter reported duplicate source IDs")
    if manifest.get("import_errors"):
        hard_failures.append("snapshot exporter reported module import errors")

    report = {
        "source_repository": manifest.get("source_repository"),
        "source_version": manifest.get("source_version"),
        "status": "pass" if not hard_failures else "fail",
        "identity": {
            "physical_baseline_cards": len(baseline_ids),
            "snapshot_cards": len(snapshot_ids),
            "snapshot_mapped_to_neon": len(mapped),
            "snapshot_ids_not_in_neon": len(snapshot_not_in_neon),
            "snapshot_ids_not_in_current_baseline": len(snapshot_not_in_baseline),
            "baseline_cards_missing_from_snapshot": len(baseline_missing_snapshot),
            "snapshot_coverage_of_baseline": round(len(snapshot_ids & baseline_ids) / len(baseline_ids), 6) if baseline_ids else 1.0,
            "name_mismatches": len(name_mismatches),
        },
        "field_coverage": dict(field_coverage),
        "variants": {
            "shape_counts": dict(variant_shapes),
            "cards_with_detailed_variants": detailed_variant_cards,
            "detailed_variant_rows": total_detailed_variants,
            "variant_type_counts": dict(variant_type_counts),
            "stamp_counts": dict(stamp_counts),
            "foil_pattern_counts": dict(foil_pattern_counts),
        },
        "baseline_missing_snapshot_samples": baseline_missing_snapshot[:200],
        "snapshot_not_in_neon_samples": snapshot_not_in_neon[:100],
        "snapshot_not_in_baseline_samples": snapshot_not_in_baseline[:100],
        "name_mismatch_samples": name_mismatches[:100],
        "hard_failures": hard_failures,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if hard_failures:
        raise AssertionError("; ".join(hard_failures))
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    args = parser.parse_args()
    run(args.snapshot, args.manifest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
