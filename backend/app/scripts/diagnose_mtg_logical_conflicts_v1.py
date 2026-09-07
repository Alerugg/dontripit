from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

from app.ingest.connectors.scryfall_mtg_v2 import ScryfallMtgV2Connector
from app.mtg_identity_v2 import card_identity_key
from app.scripts.build_mtg_v2_snapshot_v22 import card_attributes


def run(output: Path) -> dict:
    connector = ScryfallMtgV2Connector()
    rows = connector._download_default_cards()
    seen: dict[str, dict] = {}
    conflicts: dict[str, dict] = {}
    samples_by_key: dict[str, list[dict]] = defaultdict(list)

    for card in rows:
        if not connector._is_paper_card(card):
            continue
        key = card_identity_key(card)
        attrs = card_attributes(card)
        previous = seen.get(key)
        if previous is None:
            seen[key] = attrs
        elif previous != attrs:
            differing = sorted(field for field in set(previous) | set(attrs) if previous.get(field) != attrs.get(field))
            entry = conflicts.setdefault(key, {"fields": set(), "samples": []})
            entry["fields"].update(differing)
            if len(entry["samples"]) < 6:
                entry["samples"].append(
                    {
                        "scryfall_id": card.get("id"),
                        "name": card.get("name"),
                        "set": card.get("set"),
                        "collector_number": card.get("collector_number"),
                        "lang": card.get("lang"),
                        "released_at": card.get("released_at"),
                        "differing_fields": differing,
                        "values": {field: attrs.get(field) for field in differing},
                        "baseline": {field: previous.get(field) for field in differing},
                    }
                )

    normalized = {
        key: {"fields": sorted(value["fields"]), "samples": value["samples"]}
        for key, value in sorted(conflicts.items())
    }
    field_counts: dict[str, int] = defaultdict(int)
    for value in normalized.values():
        for field in value["fields"]:
            field_counts[field] += 1
    report = {
        "status": "pass",
        "paper_logical_identities": len(seen),
        "conflicting_logical_identities": len(normalized),
        "conflict_fields": dict(sorted(field_counts.items())),
        "conflicts": normalized,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "conflicts"}, indent=2, sort_keys=True))
    for key, value in list(normalized.items())[:20]:
        print(json.dumps({"card_key": key, **value}, ensure_ascii=False, sort_keys=True))
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    run(args.output)


if __name__ == "__main__":
    main()
