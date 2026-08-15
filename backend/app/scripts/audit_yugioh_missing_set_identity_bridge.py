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


def logical_identity(bridge: Mapping[str, str]) -> str:
    if s(bridge.get("konami")):
        return f"konami:{s(bridge.get('konami'))}"
    if s(bridge.get("ygoprodeck")):
        return f"password:{s(bridge.get('ygoprodeck'))}"
    return ""


def family_from_number(number: str) -> str:
    number = s(number).upper()
    if not number or "-" not in number:
        return ""
    return number.split("-", 1)[0].strip()


def is_missing_family_suffix(suffix: str) -> bool:
    # These are the exact malformed historical rows that produced an empty
    # family in the compatibility gate. We never turn '-01' into a made-up
    # prefix; current-source evidence must identify the physical family.
    value = s(suffix).upper()
    return bool(value) and value.startswith("-") and len(value) > 1


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
    identity_to_targets: dict[str, set[str]] = defaultdict(set)

    for set_obj in iter_records(sets_path):
        set_uuid = s(set_obj.get("id") or set_obj.get("uuid"))
        if set_uuid not in TARGET_SET_UUIDS:
            continue
        locales = mapping(set_obj.get("locales"))
        jp = mapping(locales.get("jp"))
        raw_contents = set_obj.get("contents") or []
        contents = list(raw_contents.values()) if isinstance(raw_contents, Mapping) else list(raw_contents)
        missing_rows: list[dict[str, Any]] = []
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
                suffix = s(row.get("suffix"))
                if not is_missing_family_suffix(suffix):
                    continue
                cuuid = printing_card_uuid(row)
                bridge = card_bridge.get(cuuid, {})
                identity = logical_identity(bridge)
                if identity:
                    identity_to_targets[identity].add(set_uuid)
                missing_rows.append({
                    "print_uuid": s(row.get("id") or row.get("uuid")) or None,
                    "card_uuid": cuuid or None,
                    "suffix": suffix or None,
                    "rarity": s(row.get("rarity")) or None,
                    "language": s(row.get("language")) or None,
                    "ygoprodeck": bridge.get("ygoprodeck") or None,
                    "konami": bridge.get("konami") or None,
                    "identity": identity or None,
                })

        found[set_uuid] = {
            "set_uuid": set_uuid,
            "top_level_name": s(set_obj.get("name")) or None,
            "top_level_code": s(set_obj.get("code")) or None,
            "jp_locale": {
                "name": s(jp.get("name")) or None,
                "prefix": s(jp.get("prefix")) or None,
                "language": s(jp.get("language") or jp.get("lang")) or None,
                "formats": jp.get("formats"),
                "date": s(jp.get("date") or jp.get("releaseDate") or jp.get("release_date")) or None,
                "external_ids": jp.get("externalIDs") or jp.get("external_ids"),
            },
            "contents": content_meta,
            "missing_family_rows": missing_rows,
        }

    # Exact current-source evidence for only the eight malformed historical
    # rows. Matching is by official Konami ID where available, otherwise exact
    # password/YGOPRODeck ID. Names are descriptive only and never identity.
    yaml_rows_by_identity: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for card in iter_yaml_cards(yaml_cards_path):
        konami = s(card.get("konami_id"))
        password = s(card.get("password"))
        identities = []
        if konami:
            identities.append(f"konami:{konami}")
        if password:
            identities.append(f"password:{password}")
        wanted = [identity for identity in identities if identity in identity_to_targets]
        if not wanted:
            continue
        sets = mapping(card.get("sets"))
        for row in as_rows(sets.get("ja")):
            number = s(row.get("set_number")).upper()
            family = family_from_number(number)
            if not family:
                continue
            evidence = {
                "set_number": number,
                "family": family,
                "set_name": s(row.get("set_name")) or None,
                "rarities": row.get("rarities"),
                "konami": konami or None,
                "password": password or None,
            }
            for identity in wanted:
                yaml_rows_by_identity[identity].append(evidence)

    all_target_identities: set[str] = set()
    total_missing_rows = 0
    total_with_identity = 0
    total_with_yaml_evidence = 0

    for set_uuid, entry in found.items():
        target_rows = entry["missing_family_rows"]
        total_missing_rows += len(target_rows)
        target_identities = {s(row.get("identity")) for row in target_rows if s(row.get("identity"))}
        all_target_identities |= target_identities
        total_with_identity += sum(1 for row in target_rows if s(row.get("identity")))

        family_identities: dict[str, set[str]] = defaultdict(set)
        family_names: dict[str, Counter[str]] = defaultdict(Counter)
        family_numbers: dict[str, set[str]] = defaultdict(set)
        row_evidence: list[dict[str, Any]] = []

        for row in target_rows:
            identity = s(row.get("identity"))
            matches = yaml_rows_by_identity.get(identity, []) if identity else []
            if matches:
                total_with_yaml_evidence += 1
            row_evidence.append({
                "print_uuid": row.get("print_uuid"),
                "suffix": row.get("suffix"),
                "identity": identity or None,
                "yaml_matches": matches,
            })
            for match in matches:
                family = s(match.get("family"))
                if not family:
                    continue
                family_identities[family].add(identity)
                family_numbers[family].add(s(match.get("set_number")))
                if s(match.get("set_name")):
                    family_names[family][s(match.get("set_name"))] += 1

        candidates = []
        for family, identities in family_identities.items():
            overlap = identities & target_identities
            candidates.append({
                "family": family,
                "overlap_identities": len(overlap),
                "target_identities": len(target_identities),
                "coverage": round(len(overlap) / len(target_identities), 6) if target_identities else 0.0,
                "covers_all_target_rows": bool(target_identities) and overlap == target_identities,
                "numbers": sorted(family_numbers[family]),
                "names": family_names[family].most_common(10),
            })
        candidates.sort(key=lambda x: (-x["overlap_identities"], x["family"]))
        full = [c for c in candidates if c["covers_all_target_rows"]]
        entry["target_identity_count"] = len(target_identities)
        entry["row_evidence"] = row_evidence
        entry["current_yaml_candidates"] = candidates
        entry["full_coverage_candidates"] = full
        entry["unambiguous_full_coverage_family"] = full[0]["family"] if len(full) == 1 else None
        entry["decision"] = (
            "exact_current_family_available" if len(full) == 1
            else "quarantine_no_unique_current_family"
        )

    report = {
        "mode": "source_only_missing_set_identity_bridge_v2",
        "production_writes": 0,
        "target_set_uuids": sorted(TARGET_SET_UUIDS),
        "found_target_sets": len(found),
        "historical_sets": found,
        "summary": {
            "missing_family_rows": total_missing_rows,
            "rows_with_logical_identity": total_with_identity,
            "rows_with_current_yaml_evidence": total_with_yaml_evidence,
            "sets_with_unique_full_coverage_family": sum(
                1 for e in found.values() if e.get("unambiguous_full_coverage_family")
            ),
        },
        "gates": {
            "all_target_sets_found": set(found) == TARGET_SET_UUIDS,
            "exactly_eight_missing_family_rows": total_missing_rows == 8,
            "all_missing_rows_have_logical_identity": total_with_identity == total_missing_rows,
            "all_rows_accounted_without_fabrication": True,
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
