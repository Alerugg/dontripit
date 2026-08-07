from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone

from app.pokemon_source_inventory import POCKET_SERIES_NAME, load_inventory


def _set_counts(cards: dict[str, dict]) -> Counter:
    return Counter(row.get("set_id") for row in cards.values() if row.get("set_id"))


def run() -> dict:
    inventory = load_inventory()
    physical = inventory.physical_cards
    pocket = inventory.pocket_cards
    physical_set_counts = _set_counts(physical)
    pocket_set_counts = _set_counts(pocket)

    physical_sets = inventory.physical_sets
    pocket_sets = inventory.pocket_sets

    mismatches = []
    empty_source_sets = []
    for row in physical_sets:
        set_id = row["set_id"]
        actual = physical_set_counts[set_id]
        declared = row.get("declared_total")
        if actual == 0:
            empty_source_sets.append({**row, "global_cards": actual})
        if isinstance(declared, int) and actual != declared:
            mismatches.append({
                "set_id": set_id,
                "set_name": row.get("set_name"),
                "series": row.get("series"),
                "declared_total": declared,
                "global_cards": actual,
                "set_endpoint_cards": row.get("set_endpoint_cards"),
                "difference": declared - actual,
            })

    missing_images = [row for row in physical.values() if not row.get("image")]
    missing_local_ids = [row for row in physical.values() if not row.get("local_id")]
    missing_names = [row for row in physical.values() if not row.get("name")]

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": "TCGdex REST v2 / en",
        "classification": {
            "physical_game": "Pokémon Trading Card Game",
            "excluded_series": POCKET_SERIES_NAME,
            "reason": "Pocket is a separate game and must not pollute the physical Pokémon TCG catalog.",
        },
        "physical": {
            "sets": len(physical_sets),
            "cards": len(physical),
            "missing_images": len(missing_images),
            "missing_local_ids": len(missing_local_ids),
            "missing_names": len(missing_names),
            "declared_count_mismatches": len(mismatches),
            "sets_with_zero_global_cards": len(empty_source_sets),
        },
        "pocket": {
            "sets": len(pocket_sets),
            "cards": len(pocket),
        },
        "all_tcgdex": {
            "sets": len(inventory.sets),
            "cards": len(inventory.cards),
            "unassigned_cards": len(inventory.unassigned_cards),
        },
        "count_mismatches": mismatches,
        "empty_source_sets": empty_source_sets,
        "missing_image_samples": missing_images[:100],
        "unassigned_card_samples": inventory.unassigned_cards[:100],
        "pocket_set_ids": sorted(inventory.pocket_set_ids),
        "set_coverage": [
            {
                "set_id": row["set_id"],
                "set_name": row.get("set_name"),
                "series": row.get("series"),
                "declared_total": row.get("declared_total"),
                "global_cards": (pocket_set_counts if row.get("series") == POCKET_SERIES_NAME else physical_set_counts)[row["set_id"]],
                "set_endpoint_cards": row.get("set_endpoint_cards"),
            }
            for row in inventory.sets
        ],
    }

    failures = []
    if inventory.unassigned_cards:
        failures.append(f"{len(inventory.unassigned_cards)} global cards could not be assigned to a set")
    if missing_local_ids:
        failures.append(f"{len(missing_local_ids)} physical cards have no localId")
    if missing_names:
        failures.append(f"{len(missing_names)} physical cards have no name")

    report["status"] = "pass" if not failures else "fail"
    report["failures"] = failures
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if failures:
        raise AssertionError("; ".join(failures))
    return report


def main() -> int:
    run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
