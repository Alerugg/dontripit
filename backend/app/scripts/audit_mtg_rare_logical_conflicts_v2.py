from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

from app.ingest.connectors.scryfall_mtg_v2 import ScryfallMtgV2Connector
from app.mtg_identity_v2 import card_identity_key, clean
from app.scripts.build_mtg_v2_snapshot import _face_payload, _is_paper, _iter_bulk_rows, _norm_list


TARGET_FIELDS = (
    "colors",
    "color_identity",
    "produced_mana",
    "faces",
    "layout",
    "mana_cost",
    "mana_value",
    "type_line",
    "oracle_text",
    "power",
    "toughness",
    "loyalty",
    "defense",
)


def _value(card: dict, field: str):
    if field == "colors":
        return _norm_list(card.get("colors"))
    if field == "color_identity":
        return _norm_list(card.get("color_identity"))
    if field == "produced_mana":
        return _norm_list(card.get("produced_mana"))
    if field == "faces":
        return [_face_payload(face) for face in _norm_list(card.get("card_faces")) if isinstance(face, dict)]
    if field == "mana_value":
        return card.get("cmc")
    return clean(card.get(field)) or None


def _fingerprint(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _source_ref(card: dict) -> dict:
    return {
        "scryfall_id": clean(card.get("id")),
        "set": clean(card.get("set")),
        "set_name": clean(card.get("set_name")),
        "collector_number": clean(card.get("collector_number")),
        "released_at": clean(card.get("released_at")) or None,
        "lang": clean(card.get("lang")) or None,
        "layout": clean(card.get("layout")) or None,
        "games": _norm_list(card.get("games")),
    }


def run(*, output_path: Path) -> dict:
    connector = ScryfallMtgV2Connector()
    metadata = connector._bulk_metadata()
    download_url = connector._bulk_download_url(metadata)
    if not download_url:
        raise AssertionError("Scryfall default_cards bulk URL unavailable")

    # field -> card_key -> value fingerprint -> {value, sources[]}
    values: dict[str, dict[str, dict[str, dict]]] = {
        field: defaultdict(dict) for field in TARGET_FIELDS
    }
    card_meta: dict[str, dict] = {}
    paper_objects = 0

    for card in _iter_bulk_rows(connector, download_url):
        if not _is_paper(card):
            continue
        paper_objects += 1
        key = card_identity_key(card)
        card_meta.setdefault(
            key,
            {
                "card_key": key,
                "name": clean(card.get("name")),
                "oracle_id": clean(card.get("oracle_id")) or None,
            },
        )
        source = _source_ref(card)

        for field in TARGET_FIELDS:
            value = _value(card, field)
            fp = _fingerprint(value)
            bucket = values[field][key]
            entry = bucket.get(fp)
            if entry is None:
                entry = {"value": value, "sources": []}
                bucket[fp] = entry
            # Two source examples per distinct value are enough to prove the
            # representation while keeping the report compact and readable.
            if len(entry["sources"]) < 2:
                entry["sources"].append(source)

    conflicts_by_field: dict[str, list[dict]] = {}
    for field in TARGET_FIELDS:
        rows: list[dict] = []
        for key, variants in values[field].items():
            if len(variants) <= 1:
                continue
            rows.append(
                {
                    **card_meta[key],
                    "distinct_values": len(variants),
                    "variants": list(variants.values()),
                }
            )
        rows.sort(key=lambda row: (row.get("name") or "", row["card_key"]))
        conflicts_by_field[field] = rows

    conflict_counts = {field: len(rows) for field, rows in conflicts_by_field.items()}
    stable_rules_fields = (
        "color_identity",
        "layout",
        "mana_cost",
        "mana_value",
        "type_line",
        "oracle_text",
        "power",
        "toughness",
        "loyalty",
        "defense",
    )

    report = {
        "status": "pass",
        "source": {
            "bulk_id": clean(metadata.get("id")) or None,
            "updated_at": clean(metadata.get("updated_at")) or None,
        },
        "paper_source_objects": paper_objects,
        "logical_card_keys": len(card_meta),
        "target_fields": list(TARGET_FIELDS),
        "logical_cards_with_multiple_values_by_field": conflict_counts,
        "stable_rules_fields_zero_conflicts": {
            field: conflict_counts[field] == 0 for field in stable_rules_fields
        },
        "conflicts": {
            field: rows
            for field, rows in conflicts_by_field.items()
            if rows
        },
        "database_writes": 0,
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    compact = {
        "status": report["status"],
        "source": report["source"],
        "paper_source_objects": report["paper_source_objects"],
        "logical_card_keys": report["logical_card_keys"],
        "logical_cards_with_multiple_values_by_field": conflict_counts,
        "stable_rules_fields_zero_conflicts": report["stable_rules_fields_zero_conflicts"],
        "rare_conflicts": {
            field: rows
            for field, rows in report["conflicts"].items()
            if field in {"colors", "produced_mana", "faces", "color_identity"}
        },
        "database_writes": 0,
    }
    print(json.dumps(compact, ensure_ascii=False, indent=2, sort_keys=True))
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit rare logical MTG field conflicts from one Scryfall bulk export")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    run(output_path=args.output)


if __name__ == "__main__":
    main()
