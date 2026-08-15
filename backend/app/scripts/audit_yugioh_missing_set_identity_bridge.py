#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Mapping

from app.scripts.audit_yugioh_multilingual_db_compatibility import (
    ext_ids,
    find_file,
    iter_records,
    mapping,
    s,
)
from app.scripts.audit_yugioh_multilingual_regional_sets import iter_cards as iter_yaml_cards

TARGET_SET_UUIDS = {
    "89bc2b1f-544f-431c-b236-2dc8e6f05079",
    "9af9360d-3df0-4167-836e-58336ad638b2",
}


def as_rows(value: Any) -> list[Mapping[str, Any]]:
    if isinstance(value, Mapping):
        return [value]
    if isinstance(value, list):
        return [x for x in value if isinstance(x, Mapping)]
    return []


def content_locales(content: Mapping[str, Any]) -> set[str]:
    raw = content.get("locales")
    if isinstance(raw, Mapping):
        return {str(k).strip().lower() for k, v in raw.items() if v not in (False, None, "")}
    if isinstance(raw, list):
        return {s(v).lower() for v in raw if s(v)}
    return {s(raw).lower()} if s(raw) else set()


def printing_card_uuid(row: Mapping[str, Any]) -> str:
    value = row.get("card")
    if isinstance(value, Mapping):
        return s(value.get("id") or value.get("uuid"))
    return s(value)


def run(ygojson_root: Path, yaml_cards_path: Path, report_path: Path) -> dict[str, Any]:
    cards_path = find_file(ygojson_root, "cards.json")
    sets_path = find_file(ygojson_root, "sets.json")

    card_bridge: dict[str, dict[str, str]] = {}
    for card in iter_records(cards_path):
        uuid = s(card.get("id") or card.get("uuid"))
        if not uuid:
            continue
        ygo, konami = ext_ids(card)
        card_bridge[uuid] = {"ygoprodeck": s(ygo), "konami": s(konami)}

    found: dict[str, dict[str, Any]] = {}
    target_konami: set[str] = set()
    target_ygo: set[str] = set()
    for set_obj in iter_records(sets_path):
        set_uuid = s(set_obj.get("id") or set_obj.get("uuid"))
        if set_uuid not in TARGET_SET_UUIDS:
            continue
        locales = mapping(set_obj.get("locales"))
        jp = mapping(locales.get("jp"))
        raw_contents = set_obj.get("contents") or []
        contents = list(raw_contents.values()) if isinstance(raw_contents, Mapping) else list(raw_contents)
        cards_out: list[dict[str, Any]] = []
        content_meta: list[dict[str, Any]] = []
        for content in [x for x in contents if isinstance(x, Mapping)]:
            scoped = content_locales(content)
            content_meta.append({
                "id": s(content.get("id") or content.get("uuid")) or None,
                "name": s(content.get("name")) or None,
                "locales": sorted(scoped),
                "formats": content.get("formats"),
            })
            if scoped and "jp" not in scoped:
                continue
            rows = content.get("cards") or []
            if isinstance(rows, Mapping):
                rows = list(rows.values())
            for row in rows if isinstance(rows, list) else []:
                if not isinstance(row, Mapping):
                    continue
                cuuid = printing_card_uuid(row)
                bridge = card_bridge.get(cuuid, {})
                if bridge.get("konami"):
                    target_konami.add(bridge["konami"])
                if bridge.get("ygoprodeck"):
                    target_ygo.add(bridge["ygoprodeck"])
                cards_out.append({
                    "print_uuid": s(row.get("id") or row.get("uuid")) or None,
                    "card_uuid": cuuid or None,
                    "suffix": s(row.get("suffix")) or None,
                    "rarity": s(row.get("rarity")) or None,
                    "language": s(row.get("language")) or None,
                    "ygoprodeck": bridge.get("ygoprodeck") or None,
                    "konami": bridge.get("konami") or None,
                })
        found[set_uuid] = {
            "set_uuid": set_uuid,
            "top_level_name": s(set_obj.get("name")) or None,
            "top_level_code": s(set_obj.get("code")) or None,
            "top_level_date": s(set_obj.get("date") or set_obj.get("releaseDate") or set_obj.get("release_date")) or None,
            "top_level_formats": set_obj.get("formats"),
            "jp_locale": {
                "name": s(jp.get("name")) or None,
                "prefix": s(jp.get("prefix")) or None,
                "language": s(jp.get("language") or jp.get("lang")) or None,
                "formats": jp.get("formats"),
                "date": s(jp.get("date") or jp.get("releaseDate") or jp.get("release_date")) or None,
                "external_ids": jp.get("externalIDs") or jp.get("external_ids"),
            },
            "contents": content_meta,
            "cards": cards_out,
        }

    # Current-source bridge. We do not infer by name. A candidate family is
    # accepted only as evidence here when target cards' exact official Konami
    # IDs co-occur in one Japanese YAML set family.
    yaml_family_cards: dict[str, set[str]] = defaultdict(set)
    yaml_family_names: dict[str, Counter[str]] = defaultdict(Counter)
    yaml_family_numbers: dict[str, set[str]] = defaultdict(set)
    for card in iter_yaml_cards(yaml_cards_path):
        konami = s(card.get("konami_id"))
        password = s(card.get("password"))
        if konami not in target_konami and password not in target_ygo:
            continue
        identity = f"konami:{konami}" if konami else f"password:{password}"
        sets = mapping(card.get("sets"))
        for row in as_rows(sets.get("ja")):
            number = s(row.get("set_number")).upper()
            if not number or "-" not in number:
                continue
            family = number.split("-", 1)[0].strip()
            if not family:
                continue
            yaml_family_cards[family].add(identity)
            yaml_family_numbers[family].add(number)
            if s(row.get("set_name")):
                yaml_family_names[family][s(row.get("set_name"))] += 1

    target_identities = {
        f"konami:{k}" for k in target_konami if k
    } | {
        f"password:{p}" for p in target_ygo if p and not target_konami
    }
    current_candidates = []
    for family, identities in yaml_family_cards.items():
        overlap = identities & target_identities
        if not overlap:
            continue
        current_candidates.append({
            "family": family,
            "overlap_identities": len(overlap),
            "target_identities": len(target_identities),
            "coverage": round(len(overlap) / len(target_identities), 6) if target_identities else 0.0,
            "numbers": sorted(yaml_family_numbers[family])[:20],
            "names": yaml_family_names[family].most_common(10),
        })
    current_candidates.sort(key=lambda x: (-x["overlap_identities"], x["family"]))

    report = {
        "mode": "source_only_missing_set_identity_bridge",
        "production_writes": 0,
        "target_set_uuids": sorted(TARGET_SET_UUIDS),
        "found_target_sets": len(found),
        "historical_sets": found,
        "current_yaml_candidates": current_candidates[:100],
        "gates": {
            "all_target_sets_found": set(found) == TARGET_SET_UUIDS,
            "no_family_fabricated": True,
        },
    }
    report["gate_pass"] = all(report["gates"].values())
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return report


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ygojson-dir", type=Path, required=True)
    ap.add_argument("--yaml-cards", type=Path, required=True)
    ap.add_argument("--report", type=Path, required=True)
    args = ap.parse_args()
    run(args.ygojson_dir, args.yaml_cards, args.report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
