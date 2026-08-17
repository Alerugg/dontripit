from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import date, datetime, timezone
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


def _source_release_date(row: dict) -> date | None:
    raw = str((row.get("set") or {}).get("release_date") or "").strip()
    if not raw:
        return None
    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        return None


def _compact_extra(row: dict) -> dict:
    set_row = row.get("set") or {}
    return {
        "source_id": row.get("source_id"),
        "source_id_original": row.get("source_id_original"),
        "name": row.get("name"),
        "set_id": set_row.get("id"),
        "set_name": set_row.get("name"),
        "series_name": set_row.get("series_name"),
        "release_date": set_row.get("release_date"),
        "source_file": row.get("source_file"),
    }


def run(snapshot_path: Path, manifest_path: Path) -> dict:
    snapshot = load_snapshot(snapshot_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("status") != "pass":
        raise AssertionError("Rich snapshot exporter did not pass")
    if int(manifest.get("physical_cards") or 0) != len(snapshot):
        raise AssertionError("Snapshot manifest/card row count mismatch")

    inventory = load_inventory()
    rest_baseline_ids = set(inventory.physical_cards)
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
    snapshot_not_in_rest_baseline = sorted(snapshot_ids - rest_baseline_ids)
    rest_baseline_missing_snapshot = sorted(rest_baseline_ids - snapshot_ids)

    today = datetime.now(timezone.utc).date()
    regional_or_no_english: list[dict] = []
    future_unreleased: list[dict] = []
    released_english: list[dict] = []
    english_unknown_release: list[dict] = []

    for source_id in snapshot_not_in_rest_baseline:
        row = snapshot[source_id]
        name = str(row.get("name") or "").strip()
        release_date = _source_release_date(row)
        compact = _compact_extra(row)
        if not name:
            regional_or_no_english.append(compact)
        elif release_date is None:
            english_unknown_release.append(compact)
        elif release_date > today:
            future_unreleased.append(compact)
        else:
            released_english.append(compact)

    released_english_ids = {str(row["source_id"]) for row in released_english}
    unknown_release_ids = {str(row["source_id"]) for row in english_unknown_release}
    accepted_released_english_ids = released_english_ids & neon_ids
    missing_released_english_ids = released_english_ids - neon_ids
    accepted_unknown_release_ids = unknown_release_ids & neon_ids
    missing_unknown_release_ids = unknown_release_ids - neon_ids

    # The canonical English catalog is the REST physical baseline plus reviewed,
    # released-English repository supplements that have been explicitly inserted
    # into Neon. Regional/no-English rows and unreleased rows remain outside the
    # English catalog by design.
    canonical_english_ids = rest_baseline_ids | accepted_released_english_ids | accepted_unknown_release_ids
    canonical_missing_snapshot = sorted(canonical_english_ids - snapshot_ids)
    canonical_snapshot_coverage = (
        round(len(canonical_english_ids & snapshot_ids) / len(canonical_english_ids), 6)
        if canonical_english_ids else 1.0
    )

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
    review_blockers = []

    if rest_baseline_missing_snapshot:
        hard_failures.append(
            f"{len(rest_baseline_missing_snapshot)} REST baseline IDs have no rich-source snapshot row"
        )
    if canonical_missing_snapshot:
        hard_failures.append(
            f"{len(canonical_missing_snapshot)} accepted canonical English IDs have no rich-source snapshot row"
        )
    if manifest.get("duplicate_source_ids"):
        hard_failures.append("snapshot exporter reported duplicate source IDs")
    if manifest.get("import_errors"):
        hard_failures.append("snapshot exporter reported module import errors")

    if missing_released_english_ids:
        review_blockers.append(
            f"{len(missing_released_english_ids)} released English rich-source IDs still need identity reconciliation"
        )
    if missing_unknown_release_ids:
        review_blockers.append(
            f"{len(missing_unknown_release_ids)} English rich-source IDs with unknown release date still need review"
        )

    if hard_failures:
        status = "fail"
    elif review_blockers:
        status = "review_required"
    else:
        status = "pass"

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_repository": manifest.get("source_repository"),
        "source_version": manifest.get("source_version"),
        "status": status,
        "identity": {
            "rest_physical_baseline_cards": len(rest_baseline_ids),
            "canonical_english_cards": len(canonical_english_ids),
            "snapshot_cards": len(snapshot_ids),
            "snapshot_mapped_to_neon": len(mapped),
            "snapshot_ids_not_in_neon": len(snapshot_not_in_neon),
            "snapshot_ids_not_in_rest_baseline": len(snapshot_not_in_rest_baseline),
            "rest_baseline_cards_missing_from_snapshot": len(rest_baseline_missing_snapshot),
            "accepted_canonical_cards_missing_from_snapshot": len(canonical_missing_snapshot),
            "canonical_snapshot_coverage": canonical_snapshot_coverage,
            "name_mismatches": len(name_mismatches),
        },
        "extra_classification": {
            "regional_or_no_english": len(regional_or_no_english),
            "future_unreleased": len(future_unreleased),
            "released_english_total": len(released_english_ids),
            "released_english_accepted_in_neon": len(accepted_released_english_ids),
            "released_english_pending": len(missing_released_english_ids),
            "english_unknown_release_date_total": len(unknown_release_ids),
            "english_unknown_release_date_accepted": len(accepted_unknown_release_ids),
            "english_unknown_release_date_pending": len(missing_unknown_release_ids),
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
        "rest_baseline_missing_snapshot_samples": rest_baseline_missing_snapshot[:200],
        "regional_or_no_english_samples": regional_or_no_english[:200],
        "future_unreleased_samples": future_unreleased[:200],
        "released_english_accepted_samples": [
            _compact_extra(snapshot[source_id]) for source_id in sorted(accepted_released_english_ids)[:200]
        ],
        "released_english_pending_samples": [
            _compact_extra(snapshot[source_id]) for source_id in sorted(missing_released_english_ids)[:200]
        ],
        "english_unknown_release_date_pending_samples": [
            _compact_extra(snapshot[source_id]) for source_id in sorted(missing_unknown_release_ids)[:200]
        ],
        "name_mismatch_samples": name_mismatches[:100],
        "hard_failures": hard_failures,
        "review_blockers": review_blockers,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))

    if hard_failures:
        raise AssertionError("; ".join(hard_failures))
    if review_blockers:
        raise AssertionError("Rich Pokémon source reconciliation requires review: " + "; ".join(review_blockers))
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
