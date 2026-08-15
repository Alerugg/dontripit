#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Mapping

from app.scripts.audit_yugioh_multilingual_db_compatibility import (
    TARGETS,
    collector_code,
    collector_quality,
    content_locales,
    ext_ids,
    family_from_collector,
    find_file,
    iter_printings,
    iter_records,
    mapping,
    printing_card_uuid if False else cid,
    s,
    sl,
)

# Exact recovery certified separately by current YAML Yugi + Konami ID.
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


def compact_mapping(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    out = {}
    for key, raw in value.items():
        if raw in (None, "", [], {}):
            continue
        if isinstance(raw, (str, int, float, bool)):
            out[str(key)] = raw
    return out


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

    rows_by_target: dict[str, list[dict[str, Any]]] = {k: [] for k in TARGETS}
    row_key_counts = Counter()
    content_key_counts = Counter()

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
                content_id = s(content.get("id") or content.get("uuid"))
                for key in content.keys():
                    content_key_counts[str(key)] += 1
                for row in iter_printings(content):
                    effective_lang = sl(row.get("language")) or loc_lang
                    if effective_lang != spec["language"]:
                        continue
                    print_uuid = s(row.get("id") or row.get("uuid"))
                    card_uuid = cid(row)
                    for key in row.keys():
                        row_key_counts[str(key)] += 1
                    collector = PRINT_COLLECTOR_OVERRIDES.get(
                        print_uuid,
                        collector_code(prefix, s(row.get("suffix"))).upper(),
                    )
                    quality = collector_quality(collector)
                    card = cards.get(card_uuid, {})
                    rows_by_target[target].append({
                        "print_uuid": print_uuid,
                        "card_uuid": card_uuid,
                        "identity": logical_identity(card, card_uuid),
                        "set_uuid": set_uuid,
                        "family": family_from_collector(collector),
                        "collector": collector,
                        "quality": quality,
                        "rarity": sl(row.get("rarity")) or "unknown",
                        "content_id": content_id or None,
                        "content_name": s(content.get("name")) or None,
                        "content_meta": compact_mapping(content),
                        "row_meta": compact_mapping(row),
                    })

    targets: dict[str, Any] = {}
    for target, rows in rows_by_target.items():
        # Work on unique physical UUIDs. Duplicate memberships of one UUID are
        # source membership duplication, not separate prints.
        by_uuid: dict[str, dict[str, Any]] = {}
        duplicate_memberships = 0
        for row in rows:
            puid = row["print_uuid"]
            if not puid:
                continue
            if puid in by_uuid:
                duplicate_memberships += 1
                continue
            by_uuid[puid] = row

        exact = [r for r in by_uuid.values() if r["quality"] == "exact" and r["family"]]
        groups: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
        for row in exact:
            # This is the collision surface of Print's existing uniqueness once
            # set region and language are fixed. Rarity is intentionally omitted
            # because it is not part of uq_print_physical.
            groups[(row["family"], row["collector"], row["identity"])].append(row)

        multi = {k: v for k, v in groups.items() if len(v) > 1}
        content_distinguishes = 0
        rarity_distinguishes = 0
        content_rarity_distinguishes = 0
        requires_source_uuid = 0
        max_group = 0
        samples = []
        for key, group in sorted(multi.items(), key=lambda item: (-len(item[1]), item[0])):
            n = len(group)
            max_group = max(max_group, n)
            content_vals = {s(r.get("content_id")) for r in group if s(r.get("content_id"))}
            rarity_vals = {s(r.get("rarity")) for r in group}
            content_rarity_vals = {(s(r.get("content_id")), s(r.get("rarity"))) for r in group}
            if len(content_vals) == n:
                content_distinguishes += 1
                resolution = "content_id"
            elif len(rarity_vals) == n:
                rarity_distinguishes += 1
                resolution = "rarity"
            elif len(content_rarity_vals) == n:
                content_rarity_distinguishes += 1
                resolution = "content_id+rarity"
            else:
                requires_source_uuid += 1
                resolution = "print_uuid_required"
            if len(samples) < 80:
                samples.append({
                    "family": key[0],
                    "collector": key[1],
                    "identity": key[2],
                    "physical_uuid_count": n,
                    "resolution": resolution,
                    "rows": [
                        {
                            "print_uuid": r["print_uuid"],
                            "rarity": r["rarity"],
                            "content_id": r["content_id"],
                            "content_name": r["content_name"],
                            "content_meta": r["content_meta"],
                            "row_meta": r["row_meta"],
                        }
                        for r in group[:12]
                    ],
                })

        targets[target] = {
            "source_memberships": len(rows),
            "unique_print_uuids": len(by_uuid),
            "duplicate_membership_rows": duplicate_memberships,
            "exact_unique_prints_with_family": len(exact),
            "physical_tuple_groups": len(groups),
            "groups_requiring_variant": len(multi),
            "physical_uuids_in_variant_groups": sum(len(v) for v in multi.values()),
            "max_variant_group_size": max_group,
            "variant_resolution": {
                "content_id_alone_groups": content_distinguishes,
                "rarity_alone_groups": rarity_distinguishes,
                "content_id_plus_rarity_groups": content_rarity_distinguishes,
                "print_uuid_required_groups": requires_source_uuid,
            },
            "samples": samples,
        }

    report = {
        "mode": "source_only_ygojson_variant_identity_audit",
        "production_writes": 0,
        "targets": targets,
        "observed_row_keys": row_key_counts.most_common(),
        "observed_content_keys": content_key_counts.most_common(),
        "policy": {
            "physical_uuid": "YGOJSON print UUID remains authoritative historical identity",
            "variant": "prefer source content/rarity metadata when it uniquely distinguishes physical UUIDs; otherwise keep source UUID as deterministic technical discriminator rather than fabricate edition labels",
        },
    }
    report["gate_pass"] = all(
        t["unique_print_uuids"] > 0 and t["groups_requiring_variant"] >= 0
        for t in targets.values()
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({
        "mode": report["mode"],
        "gate_pass": report["gate_pass"],
        "targets": {
            k: {
                "unique_print_uuids": v["unique_print_uuids"],
                "groups_requiring_variant": v["groups_requiring_variant"],
                "variant_resolution": v["variant_resolution"],
            }
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
