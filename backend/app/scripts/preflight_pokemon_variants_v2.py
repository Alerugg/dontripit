from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from pathlib import Path

from sqlalchemy import text

from app import db
from app.pokemon_source_inventory import load_inventory
from app.pokemon_variant_identity import third_party_ids, variant_dimensions
from app.scripts.audit_pokemon_rich_snapshot_v2 import load_snapshot, run as run_rich_audit


LANGUAGE = "en"
FOIL_TYPES = {"holo", "reverse", "lenticular", "metal"}


def _release_date(row: dict) -> date | None:
    raw = str((row.get("set") or {}).get("release_date") or "").strip()
    if not raw:
        return None
    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        return None


def _canonical_ids(snapshot: dict[str, dict], neon_ids: set[str]) -> set[str]:
    rest_ids = set(load_inventory().physical_cards)
    today = datetime.now(timezone.utc).date()
    result = set(rest_ids)
    for source_id, row in snapshot.items():
        if source_id in rest_ids or source_id not in neon_ids:
            continue
        if not str(row.get("name") or "").strip():
            continue
        released = _release_date(row)
        if released is not None and released <= today:
            result.add(source_id)
    return result


def _dimension_key(payload: dict) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _variant_hash(payload: dict) -> str:
    return hashlib.sha1(_dimension_key(payload).encode("utf-8")).hexdigest()[:16]


def _third_party_conflicts(rows: list[dict]) -> dict[str, list[object]]:
    values: dict[str, set[object]] = defaultdict(set)
    for row in rows:
        for provider, external_id in third_party_ids(row).items():
            values[provider].add(external_id)
    return {
        provider: sorted(provider_values, key=str)
        for provider, provider_values in values.items()
        if len(provider_values) > 1
    }


def _detailed_candidates(source_id: str, variants: list[dict]) -> tuple[list[dict], list[dict], list[dict]]:
    groups: dict[str, list[dict]] = defaultdict(list)
    foreign_language: list[dict] = []

    for raw in variants:
        if not isinstance(raw, dict):
            continue
        languages = [str(value).strip().lower() for value in (raw.get("languages") or []) if str(value).strip()]
        if languages and LANGUAGE not in languages:
            foreign_language.append(raw)
            continue
        dimensions = variant_dimensions(source_id, raw)
        groups[_dimension_key(dimensions)].append(raw)

    safe: list[dict] = []
    ambiguous: list[dict] = []
    for key, rows in groups.items():
        dimensions = json.loads(key)
        conflicts = _third_party_conflicts(rows)
        merged_third_party: dict[str, object] = {}
        for row in rows:
            for provider, external_id in third_party_ids(row).items():
                merged_third_party.setdefault(provider, external_id)

        candidate = {
            "source_id": source_id,
            "variant_hash": _variant_hash(dimensions),
            "dimensions": dimensions,
            "third_party": merged_third_party,
            "source_rows": len(rows),
            "is_foil": dimensions["type"] in FOIL_TYPES,
        }
        if conflicts:
            candidate["third_party_conflicts"] = conflicts
            ambiguous.append(candidate)
        else:
            safe.append(candidate)

    return safe, ambiguous, foreign_language


def _legacy_candidates(source_id: str, variants: dict) -> tuple[list[dict], list[dict]]:
    safe: list[dict] = []
    ambiguous: list[dict] = []
    if variants.get("firstEdition") is True:
        # The old boolean schema does not tell us which enabled finish(es) carry
        # the first-edition stamp. Never invent that relationship.
        ambiguous.append({
            "source_id": source_id,
            "reason": "legacy_first_edition_boolean_has_no_finish_binding",
            "raw": variants,
        })
        return safe, ambiguous

    for variant_type in ("normal", "reverse", "holo"):
        if variants.get(variant_type) is not True:
            continue
        dimensions = {
            "type": variant_type,
            "subtype": None,
            "stamps": [],
            "foil": None,
            "size": None,
            "language": LANGUAGE,
            "release_context": None,
        }
        safe.append({
            "source_id": source_id,
            "variant_hash": _variant_hash(dimensions),
            "dimensions": dimensions,
            "third_party": {},
            "source_rows": 1,
            "is_foil": variant_type in FOIL_TYPES,
            "source_shape": "legacy_flags",
        })
    return safe, ambiguous


def run(snapshot_path: Path, manifest_path: Path) -> dict:
    reconciliation = run_rich_audit(snapshot_path, manifest_path)
    if reconciliation.get("status") != "pass":
        raise AssertionError("Rich Pokémon identity reconciliation must be green before variant preflight")

    snapshot = load_snapshot(snapshot_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    db.init_engine()
    with db.SessionLocal() as session:
        neon_rows = session.execute(text(
            """
            SELECT c.tcgdex_id, c.id AS card_id, p.id AS baseline_print_id,
                   p.print_key, p.variant, p.is_foil
            FROM cards c
            JOIN games g ON g.id=c.game_id
            JOIN prints p ON p.card_id=c.id AND p.tcgdex_id=c.tcgdex_id
            WHERE g.slug='pokemon' AND c.tcgdex_id IS NOT NULL
            """
        )).mappings().all()
        neon_by_source = {str(row["tcgdex_id"]): dict(row) for row in neon_rows}
        canonical_ids = _canonical_ids(snapshot, set(neon_by_source))
        existing_print_keys = {
            str(value)
            for value in session.execute(text(
                "SELECT print_key FROM prints p JOIN cards c ON c.id=p.card_id JOIN games g ON g.id=c.game_id WHERE g.slug='pokemon'"
            )).scalars().all()
            if value
        }

    safe_candidates: dict[str, list[dict]] = defaultdict(list)
    ambiguous_candidates: list[dict] = []
    foreign_language_rows: list[dict] = []
    cards_missing_variant_definition: list[str] = []
    cards_legacy_no_enabled_variant: list[str] = []
    shape_counts = Counter()

    for source_id in sorted(canonical_ids):
        row = snapshot[source_id]
        variants = (row.get("attributes") or {}).get("variants")
        if isinstance(variants, list):
            shape_counts["detailed_array"] += 1
            safe, ambiguous, foreign = _detailed_candidates(source_id, variants)
            safe_candidates[source_id].extend(safe)
            ambiguous_candidates.extend(ambiguous)
            foreign_language_rows.extend({"source_id": source_id, "raw": item} for item in foreign)
            if not safe and not ambiguous:
                cards_missing_variant_definition.append(source_id)
        elif isinstance(variants, dict):
            shape_counts["legacy_flags"] += 1
            safe, ambiguous = _legacy_candidates(source_id, variants)
            safe_candidates[source_id].extend(safe)
            ambiguous_candidates.extend(ambiguous)
            if not safe and not ambiguous:
                cards_legacy_no_enabled_variant.append(source_id)
        else:
            shape_counts["missing"] += 1
            cards_missing_variant_definition.append(source_id)

    # A deterministic future Print key is based on source card + variant hash.
    # This preflight only checks collisions; it does not mutate the baseline.
    planned_keys: dict[str, tuple[str, str]] = {}
    key_collisions: list[dict] = []
    for source_id, candidates in safe_candidates.items():
        for candidate in candidates:
            key = f"pokemon:tcgdex:{source_id}:en:v2-{candidate['variant_hash']}"
            owner = planned_keys.get(key)
            if owner and owner != (source_id, candidate["variant_hash"]):
                key_collisions.append({"print_key": key, "owners": [owner, (source_id, candidate["variant_hash"])]})
            planned_keys[key] = (source_id, candidate["variant_hash"])
            if key in existing_print_keys:
                key_collisions.append({
                    "print_key": key,
                    "source_id": source_id,
                    "variant_hash": candidate["variant_hash"],
                    "reason": "already_exists_in_neon",
                })

    cards_with_safe_variants = sum(1 for rows in safe_candidates.values() if rows)
    safe_variant_rows = sum(len(rows) for rows in safe_candidates.values())
    additional_prints_after_reusing_baseline = sum(max(0, len(rows) - 1) for rows in safe_candidates.values())
    ambiguous_cards = sorted({str(row.get("source_id")) for row in ambiguous_candidates if row.get("source_id")})

    dimension_type_counts = Counter()
    stamp_counts = Counter()
    foil_pattern_counts = Counter()
    size_counts = Counter()
    subtype_counts = Counter()
    release_context_counts = Counter()
    for candidates in safe_candidates.values():
        for candidate in candidates:
            dims = candidate["dimensions"]
            dimension_type_counts[dims["type"]] += 1
            for stamp in dims["stamps"]:
                stamp_counts[stamp] += 1
            if dims.get("foil"):
                foil_pattern_counts[dims["foil"]] += 1
            if dims.get("size"):
                size_counts[dims["size"]] += 1
            if dims.get("subtype"):
                subtype_counts[dims["subtype"]] += 1
            if dims.get("release_context"):
                release_context_counts[dims["release_context"]] += 1

    hard_failures = []
    if len(canonical_ids) != 21065:
        hard_failures.append(f"canonical identity moved: {len(canonical_ids)} != 21065")
    if key_collisions:
        hard_failures.append(f"{len(key_collisions)} deterministic variant Print-key collisions")

    review_blockers = []
    if ambiguous_candidates:
        review_blockers.append(
            f"{len(ambiguous_candidates)} normalized variant groups have unresolved source ambiguity across {len(ambiguous_cards)} cards"
        )

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_version": manifest.get("source_version"),
        "status": "fail" if hard_failures else ("review_required" if review_blockers else "pass"),
        "scope": {
            "canonical_english_cards": len(canonical_ids),
            "variant_shape_counts": dict(shape_counts),
        },
        "safe_plan": {
            "cards_with_safe_variant_definitions": cards_with_safe_variants,
            "safe_unique_variant_definitions": safe_variant_rows,
            "baseline_prints_reusable_as_primary_variant": cards_with_safe_variants,
            "additional_prints_if_expanded": additional_prints_after_reusing_baseline,
            "cards_with_no_variant_definition": len(cards_missing_variant_definition),
            "legacy_cards_with_no_enabled_finish": len(cards_legacy_no_enabled_variant),
            "foreign_language_specific_variant_rows_excluded": len(foreign_language_rows),
        },
        "dimensions": {
            "type_counts": dict(dimension_type_counts),
            "stamp_counts": dict(stamp_counts),
            "foil_pattern_counts": dict(foil_pattern_counts),
            "size_counts": dict(size_counts),
            "subtype_counts": dict(subtype_counts),
            "release_context_counts": dict(release_context_counts),
        },
        "ambiguity": {
            "ambiguous_variant_groups": len(ambiguous_candidates),
            "ambiguous_cards": len(ambiguous_cards),
            "samples": ambiguous_candidates[:100],
        },
        "key_collisions": key_collisions[:100],
        "foreign_language_samples": foreign_language_rows[:50],
        "missing_variant_definition_samples": cards_missing_variant_definition[:100],
        "legacy_no_enabled_finish_samples": cards_legacy_no_enabled_variant[:100],
        "hard_failures": hard_failures,
        "review_blockers": review_blockers,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))

    if hard_failures:
        raise AssertionError("; ".join(hard_failures))
    if review_blockers:
        raise AssertionError("Pokémon variant expansion requires review: " + "; ".join(review_blockers))
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
