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


def localized_name(set_obj: Mapping[str, Any], locale: Mapping[str, Any], language: str) -> str:
    name = set_obj.get("name")
    if isinstance(name, Mapping):
        for key in (language, "en"):
            if s(name.get(key)):
                return s(name.get(key))
    if s(locale.get("name")):
        return s(locale.get("name"))
    return s(name)


def scalar_external_ids(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    out = {}
    for key, raw in value.items():
        if isinstance(raw, (str, int, float, bool)):
            out[str(key)] = raw
        elif isinstance(raw, list) and all(isinstance(v, (str, int, float, bool)) for v in raw):
            out[str(key)] = raw
    return out


def run(root: Path, report_path: Path) -> dict[str, Any]:
    sets_path = find_file(root, "sets.json")
    target_sets: dict[str, dict[str, dict[str, Any]]] = {k: {} for k in TARGETS}
    slot_sets: dict[str, dict[tuple[str, str, str, str], set[str]]] = {
        k: defaultdict(set) for k in TARGETS
    }
    intra_slots: dict[str, dict[tuple[str, str, str, str, str], set[str]]] = {
        k: defaultdict(set) for k in TARGETS
    }

    for set_obj in iter_records(sets_path):
        set_uuid = s(set_obj.get("id") or set_obj.get("uuid"))
        if not set_uuid:
            continue
        locales = mapping(set_obj.get("locales"))
        raw_contents = set_obj.get("contents") or []
        contents = list(raw_contents.values()) if isinstance(raw_contents, Mapping) else list(raw_contents)
        contents = [x for x in contents if isinstance(x, Mapping)]

        for target, spec in TARGETS.items():
            loc = locales.get(spec["locale"])
            if not isinstance(loc, Mapping):
                continue
            loc_lang = sl(loc.get("language") or loc.get("lang"))
            prefix = s(loc.get("prefix"))
            rows_by_uuid: dict[str, dict[str, str]] = {}
            membership_rows = 0
            for content in contents:
                scoped = content_locales(content)
                if not scoped or spec["locale"] not in scoped:
                    continue
                for row in iter_printings(content):
                    effective_lang = sl(row.get("language")) or loc_lang
                    if effective_lang != spec["language"]:
                        continue
                    membership_rows += 1
                    puid = s(row.get("id") or row.get("uuid"))
                    if not puid:
                        continue
                    collector = PRINT_COLLECTOR_OVERRIDES.get(
                        puid,
                        collector_code(prefix, s(row.get("suffix"))).upper(),
                    )
                    if collector_quality(collector) != "exact":
                        continue
                    fam = family_from_collector(collector)
                    if not fam:
                        continue
                    rows_by_uuid.setdefault(
                        puid,
                        {
                            "print_uuid": puid,
                            "card_uuid": cid(row),
                            "collector": collector,
                            "family": fam,
                            "rarity": sl(row.get("rarity")) or "unknown",
                        },
                    )

            if not rows_by_uuid:
                continue
            families = sorted({row["family"] for row in rows_by_uuid.values()})
            record = {
                "set_uuid": set_uuid,
                "name": localized_name(set_obj, loc, spec["language"]),
                "date": s(loc.get("date") or loc.get("releaseDate") or loc.get("release_date")) or None,
                "locale_prefix": prefix or None,
                "families": families,
                "exact_print_uuids": len(rows_by_uuid),
                "membership_rows": membership_rows,
                "locale_editions": sorted({sl(v) for v in (loc.get("editions") or []) if s(v)}),
                "locale_formats": sorted({sl(v) for v in (loc.get("formats") or []) if s(v)}),
                "locale_external_ids": scalar_external_ids(loc.get("externalIDs") or loc.get("external_ids")),
                "set_external_ids": scalar_external_ids(set_obj.get("externalIDs") or set_obj.get("external_ids")),
            }
            target_sets[target][set_uuid] = record

            for row in rows_by_uuid.values():
                cross_key = (row["family"], row["collector"], row["card_uuid"], row["rarity"])
                slot_sets[target][cross_key].add(set_uuid)
                intra_key = (set_uuid, row["family"], row["collector"], row["card_uuid"], row["rarity"])
                intra_slots[target][intra_key].add(row["print_uuid"])

    targets: dict[str, Any] = {}
    for target in TARGETS:
        sets = target_sets[target]
        family_sets: dict[str, set[str]] = defaultdict(set)
        for set_uuid, record in sets.items():
            for fam in record["families"]:
                family_sets[fam].add(set_uuid)
        multi_families = {fam: uuids for fam, uuids in family_sets.items() if len(uuids) > 1}
        cross_collisions = {key: uuids for key, uuids in slot_sets[target].items() if len(uuids) > 1}
        intra_collisions = {key: uuids for key, uuids in intra_slots[target].items() if len(uuids) > 1}

        source_set_counts = Counter(len(uuids) for uuids in family_sets.values())
        family_samples = []
        for fam, uuids in sorted(multi_families.items(), key=lambda item: (-len(item[1]), item[0]))[:100]:
            family_samples.append({
                "family": fam,
                "source_set_count": len(uuids),
                "source_sets": [sets[uuid] for uuid in sorted(uuids)],
            })
        collision_samples = []
        for key, uuids in sorted(cross_collisions.items(), key=lambda item: (-len(item[1]), item[0]))[:100]:
            collision_samples.append({
                "family": key[0],
                "collector": key[1],
                "card_uuid": key[2],
                "rarity": key[3],
                "source_set_count": len(uuids),
                "source_sets": [
                    {
                        "set_uuid": uuid,
                        "name": sets[uuid]["name"],
                        "date": sets[uuid]["date"],
                        "locale_editions": sets[uuid]["locale_editions"],
                    }
                    for uuid in sorted(uuids)
                ],
            })

        targets[target] = {
            "physical_source_sets": len(sets),
            "family_codes": len(family_sets),
            "families_with_multiple_source_sets": len(multi_families),
            "source_sets_inside_multi_set_families": len(set().union(*multi_families.values())) if multi_families else 0,
            "max_source_sets_for_one_family": max((len(v) for v in family_sets.values()), default=0),
            "family_source_set_count_distribution": dict(sorted(source_set_counts.items())),
            "cross_source_set_same_collector_card_rarity_groups": len(cross_collisions),
            "intra_source_set_same_collector_card_rarity_groups": len(intra_collisions),
            "cross_collision_groups_span_multiple_source_sets": all(len(v) > 1 for v in cross_collisions.values()),
            "family_samples": family_samples,
            "cross_collision_samples": collision_samples,
            "intra_collision_samples": [
                {
                    "set_uuid": key[0], "family": key[1], "collector": key[2],
                    "card_uuid": key[3], "rarity": key[4], "print_uuids": sorted(uuids),
                }
                for key, uuids in list(intra_collisions.items())[:50]
            ],
        }

    gates = {
        "source_sets_present": all(t["physical_source_sets"] > 0 for t in targets.values()),
        "no_intra_source_set_same_slot_collisions": all(
            t["intra_source_set_same_collector_card_rarity_groups"] == 0 for t in targets.values()
        ),
        "cross_collisions_are_explained_by_distinct_source_sets": all(
            t["cross_collision_groups_span_multiple_source_sets"] for t in targets.values()
        ),
        "code_family_is_not_unique_product_identity": any(
            t["families_with_multiple_source_sets"] > 0 for t in targets.values()
        ),
    }
    report = {
        "mode": "source_only_ygojson_set_object_physical_identity",
        "production_writes": 0,
        "identity_conclusion": (
            "YGOJSON set UUID must remain part of canonical physical set identity. "
            "Locale family/code is display/search metadata and may be reused by multiple physical source sets."
        ),
        "targets": targets,
        "gates": gates,
        "gate_pass": all(gates.values()),
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({
        "gate_pass": report["gate_pass"],
        "gates": gates,
        "targets": {
            k: {kk: vv for kk, vv in v.items() if kk not in {"family_samples", "cross_collision_samples", "intra_collision_samples"}}
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
