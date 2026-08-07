from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone

from app.ingest.connectors.onepiece_v2 import OnePieceV2Connector


def run_audit() -> dict:
    connector = OnePieceV2Connector()
    payload = connector._load_official_cardlist_remote(limit=None)

    cards = payload.get("cards") or []
    sets = payload.get("sets") or []
    releases = payload.get("releases") or []
    diagnostics = payload.get("diagnostics") or {}

    prints = [print_row for card in cards for print_row in card.get("prints") or []]
    appearances = [
        appearance
        for print_row in prints
        for appearance in print_row.get("release_appearances") or []
    ]

    family_counts = Counter()
    variant_counts = Counter()
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
            cards_with_aliases.append(
                {
                    "card_key": card.get("id"),
                    "collector_number": collector,
                    "name": card.get("name"),
                    "aliases": card.get("source_name_aliases"),
                }
            )

    for print_row in prints:
        variant_counts[str(print_row.get("variant") or "default")] += 1
        rarity_counts[str(print_row.get("rarity") or "unknown")] += 1

    conflict_rows = diagnostics.get("physical_identity_conflicts") or []

    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "read_only": True,
        "source": "onepiece_official_v2",
        "counts": {
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
        "rarities": dict(sorted(rarity_counts.items())),
        "set_codes": [row.get("code") for row in sets],
        "physical_identity_conflict_samples": conflict_rows[:50],
        "source_name_alias_samples": cards_with_aliases[:50],
        "release_samples": releases[:20],
        "expectations": {
            "official_series_expected": 84,
            "previous_raw_appearances": 4673,
            "previous_skipped_p": 231,
            "previous_skipped_prb": 40,
            "previous_unique_base_collector_numbers_before_missing_families": 1290,
        },
    }


def main() -> int:
    payload = run_audit()
    print(json.dumps(payload, ensure_ascii=False, indent=2))

    counts = payload["counts"]
    if counts["catalog_releases"] <= 0 or counts["logical_cards"] <= 0 or counts["prints"] <= 0:
        return 1
    if counts["catalog_releases"] != payload["expectations"]["official_series_expected"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
