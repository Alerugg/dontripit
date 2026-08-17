from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

from app.ingest.connectors.scryfall_mtg_v2 import ScryfallMtgV2Connector
from app.mtg_identity_v2 import card_identity_key, clean
from app.scripts.build_mtg_v2_snapshot import _card_attributes, _is_paper, _iter_bulk_rows


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _source_ref(card: dict) -> dict:
    return {
        "scryfall_id": clean(card.get("scryfall_id") or card.get("id")),
        "set": clean(card.get("set")),
        "collector_number": clean(card.get("collector_number")),
    }


def run(*, output_path: Path) -> dict:
    connector = ScryfallMtgV2Connector()
    metadata = connector._bulk_metadata()
    download_url = connector._bulk_download_url(metadata)
    if not download_url:
        raise AssertionError("Scryfall default_cards bulk URL unavailable")

    first_by_key: dict[str, dict] = {}
    differing_fields: Counter[str] = Counter()
    conflict_keys: set[str] = set()
    conflict_keys_by_field: dict[str, set[str]] = defaultdict(set)
    samples: list[dict] = []
    field_samples: dict[str, list[dict]] = defaultdict(list)
    objects = 0

    for card in _iter_bulk_rows(connector, download_url):
        if not _is_paper(card):
            continue
        objects += 1
        key = card_identity_key(card)
        attrs = _card_attributes(card)
        current = {
            "scryfall_id": clean(card.get("id")),
            "name": clean(card.get("name")),
            "oracle_id": clean(card.get("oracle_id")) or None,
            "set": clean(card.get("set")),
            "collector_number": clean(card.get("collector_number")),
            "attributes": attrs,
        }
        baseline = first_by_key.get(key)
        if baseline is None:
            first_by_key[key] = current
            continue

        changed = []
        all_fields = sorted(set(baseline["attributes"]) | set(attrs))
        for field in all_fields:
            before = baseline["attributes"].get(field)
            after = attrs.get(field)
            if _canonical(before) != _canonical(after):
                differing_fields[field] += 1
                conflict_keys_by_field[field].add(key)
                difference = {
                    "field": field,
                    "baseline": before,
                    "current": after,
                }
                changed.append(difference)

                # Keep targeted evidence for every field. This is intentionally
                # capped per field so rare conflicts cannot be hidden behind the
                # very common legality/reserved differences.
                if len(field_samples[field]) < 20:
                    field_samples[field].append(
                        {
                            "card_key": key,
                            "name": current["name"],
                            "oracle_id": current["oracle_id"],
                            "baseline_source": {
                                "scryfall_id": baseline["scryfall_id"],
                                "set": baseline["set"],
                                "collector_number": baseline["collector_number"],
                            },
                            "current_source": {
                                "scryfall_id": current["scryfall_id"],
                                "set": current["set"],
                                "collector_number": current["collector_number"],
                            },
                            "baseline": before,
                            "current": after,
                        }
                    )

        if changed:
            conflict_keys.add(key)
            if len(samples) < 50:
                samples.append(
                    {
                        "card_key": key,
                        "name": current["name"],
                        "oracle_id": current["oracle_id"],
                        "baseline_source": {
                            "scryfall_id": baseline["scryfall_id"],
                            "set": baseline["set"],
                            "collector_number": baseline["collector_number"],
                        },
                        "current_source": {
                            "scryfall_id": current["scryfall_id"],
                            "set": current["set"],
                            "collector_number": current["collector_number"],
                        },
                        "differences": changed,
                    }
                )

    report = {
        "status": "pass",
        "source": {
            "bulk_id": clean(metadata.get("id")) or None,
            "updated_at": clean(metadata.get("updated_at")) or None,
        },
        "paper_source_objects": objects,
        "logical_card_keys": len(first_by_key),
        "logical_cards_with_attribute_conflicts": len(conflict_keys),
        "differing_field_comparisons": dict(sorted(differing_fields.items())),
        "logical_cards_with_conflict_by_field": {
            field: len(keys) for field, keys in sorted(conflict_keys_by_field.items())
        },
        "field_samples": {field: rows for field, rows in sorted(field_samples.items())},
        "samples": samples,
        "database_writes": 0,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit which Scryfall fields vary inside one logical MTG Card identity")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    run(output_path=args.output)


if __name__ == "__main__":
    main()
