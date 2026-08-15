#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Mapping

from app.scripts.audit_yugioh_multilingual_db_compatibility import (
    TARGETS,
    cid,
    collector_code,
    collector_quality,
    content_locales,
    ext_ids,
    family_from_collector,
    find_file,
    iter_printings,
    iter_records,
    mapping,
    s,
    sl,
)

PRINT_COLLECTOR_OVERRIDES = {
    "600984ec-0fc8-4605-a74b-5427cd1b23a0": "WJ-01",
    "bede3bc5-8752-4919-b615-7a88eb08896d": "WJ-02",
    "6ff126c5-a653-4942-9124-b860dbdca0f7": "WJ-03",
    "a623d1e1-6a7a-441c-b029-940d8305688d": "WJ-04",
    "dd16b07d-81d4-4cbb-aec9-81d7b22bce35": "VJ-01",
    "04b3dbdc-557e-4fcb-b605-6008b3041e38": "VJ-02",
    "ba4611db-aa03-4d0e-842a-c86d2cfca288": "VJ-03",
    "701f33c6-a5ff-4b56-a621-8dc70df24940": "VJ-04",
}


def logical_identity(card: Mapping[str, str], fallback: str) -> str:
    if s(card.get("konami")):
        return f"konami:{s(card.get('konami'))}"
    if s(card.get("ygoprodeck")):
        return f"ygoprodeck:{s(card.get('ygoprodeck'))}"
    return f"source:{fallback}"


def find_uuid_paths(value: Any, wanted: set[str], path: tuple[str, ...] = ()) -> dict[str, list[list[str]]]:
    hits: dict[str, list[list[str]]] = defaultdict(list)
    if isinstance(value, Mapping):
        for key, child in value.items():
            skey = str(key)
            if skey in wanted:
                hits[skey].append(list(path + (skey,)))
            child_hits = find_uuid_paths(child, wanted, path + (skey,))
            for puid, paths in child_hits.items():
                hits[puid].extend(paths)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            child_hits = find_uuid_paths(child, wanted, path + (f"[{index}]",))
            for puid, paths in child_hits.items():
                hits[puid].extend(paths)
    elif isinstance(value, str) and value in wanted:
        hits[value].append(list(path + ("=<value>",)))
    return hits


def edition_from_path(path: list[str]) -> str | None:
    # YGOJSON cardInfo is documented/observed as cardInfo -> edition ->
    # printing UUID -> metadata. Keep this strict; do not guess from arbitrary
    # path components.
    for marker in ("cardInfo", "card_info"):
        if marker in path:
            idx = path.index(marker)
            if idx + 2 < len(path):
                edition = s(path[idx + 1])
                puid = s(path[idx + 2])
                if edition and puid:
                    return edition
    return None


def run(root: Path, report_path: Path) -> dict[str, Any]:
    cards_path = find_file(root, "cards.json")
    sets_path = find_file(root, "sets.json")

    cards: dict[str, dict[str, str]] = {}
    for card in iter_records(cards_path):
        card_uuid = s(card.get("id") or card.get("uuid"))
        if not card_uuid:
            continue
        ygo, konami = ext_ids(card)
        cards[card_uuid] = {"ygoprodeck": s(ygo), "konami": s(konami)}

    rows_by_target: dict[str, dict[str, dict[str, Any]]] = {k: {} for k in TARGETS}
    for set_obj in iter_records(sets_path):
        locales = mapping(set_obj.get("locales"))
        raw_contents = set_obj.get("contents") or []
        contents = list(raw_contents.values()) if isinstance(raw_contents, Mapping) else list(raw_contents)
        contents = [x for x in contents if isinstance(x, Mapping)]
        for target, spec in TARGETS.items():
            loc = locales.get(spec["locale"])
            if not isinstance(loc, Mapping):
                continue
            prefix = s(loc.get("prefix"))
            loc_lang = sl(loc.get("language") or loc.get("lang"))
            for content in contents:
                scoped = content_locales(content)
                if not scoped or spec["locale"] not in scoped:
                    continue
                for row in iter_printings(content):
                    effective_lang = sl(row.get("language")) or loc_lang
                    if effective_lang != spec["language"]:
                        continue
                    puid = s(row.get("id") or row.get("uuid"))
                    if not puid or puid in rows_by_target[target]:
                        continue
                    cuid = cid(row)
                    collector = PRINT_COLLECTOR_OVERRIDES.get(
                        puid,
                        collector_code(prefix, s(row.get("suffix"))).upper(),
                    )
                    if collector_quality(collector) != "exact" or not family_from_collector(collector):
                        continue
                    card = cards.get(cuid, {})
                    rows_by_target[target][puid] = {
                        "print_uuid": puid,
                        "card_uuid": cuid,
                        "identity": logical_identity(card, cuid),
                        "family": family_from_collector(collector),
                        "collector": collector,
                        "rarity": sl(row.get("rarity")) or "unknown",
                    }

    unresolved_groups: dict[str, list[dict[str, Any]]] = {k: [] for k in TARGETS}
    wanted_by_card: dict[str, set[str]] = defaultdict(set)
    group_by_uuid: dict[str, tuple[str, int]] = {}
    for target, by_uuid in rows_by_target.items():
        groups: dict[tuple[str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
        for row in by_uuid.values():
            groups[(row["family"], row["collector"], row["identity"], row["rarity"])].append(row)
        for key, group in groups.items():
            if len(group) <= 1:
                continue
            group_index = len(unresolved_groups[target])
            unresolved_groups[target].append({
                "family": key[0],
                "collector": key[1],
                "identity": key[2],
                "rarity": key[3],
                "rows": group,
            })
            for row in group:
                wanted_by_card[row["card_uuid"]].add(row["print_uuid"])
                group_by_uuid[row["print_uuid"]] = (target, group_index)

    uuid_paths: dict[str, list[list[str]]] = defaultdict(list)
    uuid_editions: dict[str, set[str]] = defaultdict(set)
    card_shape_samples = []
    for card in iter_records(cards_path):
        card_uuid = s(card.get("id") or card.get("uuid"))
        wanted = wanted_by_card.get(card_uuid)
        if not wanted:
            continue
        hits = find_uuid_paths(card, wanted)
        if hits and len(card_shape_samples) < 15:
            card_shape_samples.append({
                "card_uuid": card_uuid,
                "top_level_keys": sorted(str(k) for k in card.keys()),
                "locale_keys": sorted(str(k) for k in mapping(card.get("locales")).keys()),
            })
        for puid, paths in hits.items():
            uuid_paths[puid].extend(paths)
            for path in paths:
                edition = edition_from_path(path)
                if edition:
                    uuid_editions[puid].add(edition)

    targets: dict[str, Any] = {}
    for target, groups in unresolved_groups.items():
        edition_resolved = 0
        unique_edition_resolved = 0
        ambiguous_edition = 0
        no_edition = 0
        samples = []
        for group in groups:
            edition_sets = [uuid_editions.get(row["print_uuid"], set()) for row in group["rows"]]
            if all(len(x) == 1 for x in edition_sets):
                edition_resolved += 1
                labels = [next(iter(x)) for x in edition_sets]
                if len(set(labels)) == len(labels):
                    unique_edition_resolved += 1
                    resolution = "unique_cardinfo_edition"
                else:
                    ambiguous_edition += 1
                    resolution = "edition_reused_within_slot"
            else:
                no_edition += 1
                resolution = "missing_or_ambiguous_cardinfo_edition"
            if len(samples) < 100:
                samples.append({
                    **{k: group[k] for k in ("family", "collector", "identity", "rarity")},
                    "resolution": resolution,
                    "rows": [
                        {
                            "print_uuid": row["print_uuid"],
                            "editions": sorted(uuid_editions.get(row["print_uuid"], set())),
                            "paths": uuid_paths.get(row["print_uuid"], [])[:12],
                        }
                        for row in group["rows"]
                    ],
                })
        targets[target] = {
            "same_rarity_multi_uuid_groups": len(groups),
            "physical_uuids_in_groups": sum(len(g["rows"]) for g in groups),
            "groups_all_uuids_have_single_edition": edition_resolved,
            "groups_uniquely_resolved_by_edition": unique_edition_resolved,
            "groups_edition_reused_within_slot": ambiguous_edition,
            "groups_missing_or_ambiguous_edition": no_edition,
            "uuids_with_any_source_path": sum(
                1 for g in groups for row in g["rows"] if uuid_paths.get(row["print_uuid"])
            ),
            "uuids_with_exact_cardinfo_edition": sum(
                1 for g in groups for row in g["rows"] if len(uuid_editions.get(row["print_uuid"], set())) == 1
            ),
            "samples": samples,
        }

    report = {
        "mode": "source_only_ygojson_print_uuid_to_edition_bridge",
        "production_writes": 0,
        "targets": targets,
        "card_shape_samples": card_shape_samples,
        "policy": {
            "edition_inference": "only cardInfo/card_info path segment immediately above exact printing UUID; no name/fuzzy inference",
            "fallback": "unresolved groups remain unresolved; this audit does not fabricate variant labels",
        },
    }
    report["gate_pass"] = all(t["same_rarity_multi_uuid_groups"] > 0 for t in targets.values())
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({
        "gate_pass": report["gate_pass"],
        "targets": {
            k: {kk: vv for kk, vv in v.items() if kk != "samples"}
            for k, v in targets.items()
        },
    }, ensure_ascii=False, indent=2, sort_keys=True))
    return report


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input-dir", type=Path, required=True)
    ap.add_argument("--report", type=Path, required=True)
    args = ap.parse_args()
    report = run(args.input_dir, args.report)
    return 0 if report["gate_pass"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
