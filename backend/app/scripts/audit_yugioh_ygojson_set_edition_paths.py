#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Mapping

from app.scripts.audit_yugioh_multilingual_db_compatibility import (
    TARGETS,
    cid,
    collector_code,
    collector_quality,
    content_locales,
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


def normalized_pattern(path: list[str], uuid: str) -> str:
    return "/".join("<uuid>" if part == uuid else part for part in path)


def candidate_edition(path: list[str], uuid: str) -> str | None:
    if uuid not in path:
        return None
    idx = path.index(uuid)
    # Exact known structural markers only. Anything else is evidence output,
    # not an inferred edition.
    for marker in ("cardInfo", "card_info", "cardImages", "card_images"):
        if marker in path:
            midx = path.index(marker)
            if midx + 2 == idx:
                value = s(path[midx + 1])
                return value or None
    return None


def run(root: Path, report_path: Path) -> dict[str, Any]:
    sets_path = find_file(root, "sets.json")
    rows_by_target: dict[str, dict[str, dict[str, Any]]] = {k: {} for k in TARGETS}

    for set_obj in iter_records(sets_path):
        set_uuid = s(set_obj.get("id") or set_obj.get("uuid"))
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
                    collector = PRINT_COLLECTOR_OVERRIDES.get(
                        puid,
                        collector_code(prefix, s(row.get("suffix"))).upper(),
                    )
                    if collector_quality(collector) != "exact" or not family_from_collector(collector):
                        continue
                    rows_by_target[target][puid] = {
                        "print_uuid": puid,
                        "card_uuid": cid(row),
                        "set_uuid": set_uuid,
                        "family": family_from_collector(collector),
                        "collector": collector,
                        "rarity": sl(row.get("rarity")) or "unknown",
                    }

    groups_by_target: dict[str, list[dict[str, Any]]] = {k: [] for k in TARGETS}
    wanted_by_set: dict[str, set[str]] = defaultdict(set)
    for target, rows in rows_by_target.items():
        grouped: dict[tuple[str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
        for row in rows.values():
            grouped[(row["family"], row["collector"], row["card_uuid"], row["rarity"])].append(row)
        for key, group in grouped.items():
            if len(group) <= 1:
                continue
            groups_by_target[target].append({
                "family": key[0], "collector": key[1], "card_uuid": key[2], "rarity": key[3], "rows": group,
            })
            for row in group:
                wanted_by_set[row["set_uuid"]].add(row["print_uuid"])

    paths_by_uuid: dict[str, list[list[str]]] = defaultdict(list)
    editions_by_uuid: dict[str, set[str]] = defaultdict(set)
    set_shape_samples = []
    pattern_counts = Counter()
    for set_obj in iter_records(sets_path):
        set_uuid = s(set_obj.get("id") or set_obj.get("uuid"))
        wanted = wanted_by_set.get(set_uuid)
        if not wanted:
            continue
        hits = find_uuid_paths(set_obj, wanted)
        if hits and len(set_shape_samples) < 20:
            set_shape_samples.append({
                "set_uuid": set_uuid,
                "top_level_keys": sorted(str(k) for k in set_obj.keys()),
                "locale_keys": sorted(str(k) for k in mapping(set_obj.get("locales")).keys()),
            })
        for puid, paths in hits.items():
            for path in paths:
                paths_by_uuid[puid].append(path)
                pattern_counts[normalized_pattern(path, puid)] += 1
                edition = candidate_edition(path, puid)
                if edition:
                    editions_by_uuid[puid].add(edition)

    targets: dict[str, Any] = {}
    for target, groups in groups_by_target.items():
        unique_edition_groups = 0
        groups_with_paths = 0
        all_rows_have_paths = 0
        samples = []
        for group in groups:
            rows = group["rows"]
            any_paths = any(paths_by_uuid.get(row["print_uuid"]) for row in rows)
            all_paths = all(paths_by_uuid.get(row["print_uuid"]) for row in rows)
            if any_paths:
                groups_with_paths += 1
            if all_paths:
                all_rows_have_paths += 1
            labels = []
            single = True
            for row in rows:
                editions = editions_by_uuid.get(row["print_uuid"], set())
                if len(editions) != 1:
                    single = False
                    break
                labels.append(next(iter(editions)))
            resolved = single and len(set(labels)) == len(labels)
            if resolved:
                unique_edition_groups += 1
            if len(samples) < 120:
                samples.append({
                    **{k: group[k] for k in ("family", "collector", "card_uuid", "rarity")},
                    "resolved_by_exact_set_path_edition": resolved,
                    "rows": [
                        {
                            "print_uuid": row["print_uuid"],
                            "editions": sorted(editions_by_uuid.get(row["print_uuid"], set())),
                            "paths": paths_by_uuid.get(row["print_uuid"], [])[:15],
                        }
                        for row in rows
                    ],
                })
        targets[target] = {
            "same_rarity_multi_uuid_groups": len(groups),
            "physical_uuids_in_groups": sum(len(g["rows"]) for g in groups),
            "groups_with_any_set_path": groups_with_paths,
            "groups_all_rows_have_set_path": all_rows_have_paths,
            "groups_uniquely_resolved_by_exact_set_path_edition": unique_edition_groups,
            "uuids_with_any_set_path": sum(
                1 for g in groups for row in g["rows"] if paths_by_uuid.get(row["print_uuid"])
            ),
            "uuids_with_single_exact_edition": sum(
                1 for g in groups for row in g["rows"] if len(editions_by_uuid.get(row["print_uuid"], set())) == 1
            ),
            "samples": samples,
        }

    report = {
        "mode": "source_only_ygojson_set_object_edition_paths",
        "production_writes": 0,
        "targets": targets,
        "path_patterns": pattern_counts.most_common(80),
        "set_shape_samples": set_shape_samples,
        "policy": {
            "match": "exact printing UUID searched within its exact YGOJSON set object",
            "edition": "accepted only from an explicit cardInfo/cardImages edition path immediately above the exact UUID",
            "fallback": "no inferred edition when structure does not prove one",
        },
    }
    report["gate_pass"] = all(t["same_rarity_multi_uuid_groups"] > 0 for t in targets.values())
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({
        "gate_pass": report["gate_pass"],
        "path_patterns": report["path_patterns"][:20],
        "targets": {k:{kk:vv for kk,vv in v.items() if kk != "samples"} for k,v in targets.items()},
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
