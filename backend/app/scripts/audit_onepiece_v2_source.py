from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone

from app.ingest.connectors.onepiece_v2 import OnePieceV2Connector


PREVIOUS_RAW_APPEARANCES = 4673


def run_audit() -> dict:
    connector = OnePieceV2Connector()
    payload = connector._load_official_cardlist_remote(limit=None)

    cards = payload.get("cards") or []
    sets = payload.get("sets") or []
    releases = payload.get("releases") or []
    diagnostics = payload.get("diagnostics") or {}
    prints = [row for card in cards for row in card.get("prints") or []]
    appearances = [a for row in prints for a in row.get("release_appearances") or []]

    family_counts = Counter()
    variant_counts = Counter()
    variant_family_counts = Counter()
    rarity_counts = Counter()
    cards_with_aliases = []
    for card in cards:
        collector = str(card.get("collector_number") or "").upper()
        if collector.startswith("P-"):
            family_counts["P"] += 1
        elif collector.startswith("PRB"):
            family_counts["PRB"] += 1
        elif collector.startswith("OP"):
            family_counts["OP"] += 1
        elif collector.startswith("ST"):
            family_counts["ST"] += 1
        elif collector.startswith("EB"):
            family_counts["EB"] += 1
        else:
            family_counts["OTHER"] += 1
        if card.get("source_name_aliases"):
            cards_with_aliases.append({
                "card_key": card.get("id"),
                "collector_number": collector,
                "name": card.get("name"),
                "aliases": card.get("source_name_aliases"),
            })

    for row in prints:
        variant_counts[str(row.get("variant") or "default")] += 1
        variant_family_counts[str(row.get("variant_family") or "default")] += 1
        rarity_counts[str(row.get("rarity") or "unknown")] += 1

    conflict_rows = diagnostics.get("physical_identity_conflicts") or []
    return {
        "schema_version": 2,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "read_only": True,
        "source": "onepiece_official_v2",
        "counts": {
            "source_option_rows": int(diagnostics.get("source_option_rows") or 0),
            "unique_release_ids": int(diagnostics.get("unique_release_ids") or 0),
            "sets": len(sets),
            "logical_cards": len(cards),
            "prints": len(prints),
            "catalog_releases": len(releases),
            "print_release_links": len(appearances),
            "physical_identity_conflicts": len(conflict_rows),
            "cards_with_source_name_aliases": len(cards_with_aliases),
        },
        "collector_families": dict(sorted(family_counts.items())),
        "variants": dict(sorted(variant_counts.items())),
        "variant_families": dict(sorted(variant_family_counts.items())),
        "rarities": dict(sorted(rarity_counts.items())),
        "set_codes": [row.get("code") for row in sets],
        "physical_identity_conflict_samples": conflict_rows[:50],
        "source_name_alias_samples": cards_with_aliases[:50],
        "release_samples": releases[:20],
        "expectations": {
            "previous_raw_appearances": PREVIOUS_RAW_APPEARANCES,
            "previous_skipped_p": 231,
            "previous_skipped_prb": 40,
            "previous_unique_base_collector_numbers_before_missing_families": 1290,
        },
    }


def main() -> int:
    payload = run_audit()
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    counts = payload["counts"]

    failed = False
    failed |= counts["source_option_rows"] <= 0
    failed |= counts["unique_release_ids"] <= 0
    failed |= counts["catalog_releases"] != counts["unique_release_ids"]
    failed |= counts["logical_cards"] <= 0 or counts["prints"] <= 0
    failed |= counts["print_release_links"] != PREVIOUS_RAW_APPEARANCES
    failed |= counts["physical_identity_conflicts"] != 0
    failed |= int(payload["collector_families"].get("P", 0)) <= 0
    failed |= int(payload["collector_families"].get("PRB", 0)) <= 0
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
