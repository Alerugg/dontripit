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


def norm_list(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, (list, tuple, set)):
        value = [value]
    return tuple(sorted({sl(v) for v in value if s(v)}))


def scalar(value: Any) -> str | None:
    value = s(value)
    return value or None


def row_semantics(row: Mapping[str, Any]) -> dict[str, Any]:
    # qty is intentionally excluded: quantity in a preconstructed product is
    # not a distinct physical printing identity.
    return {
        "only_in_box": scalar(row.get("onlyInBox") or row.get("only_in_box")),
        "image_id": scalar(row.get("imageID") or row.get("image_id")),
        "replica": bool(row.get("replica")) if row.get("replica") is not None else None,
        "language_override": scalar(row.get("language")),
    }


def content_semantics(content: Mapping[str, Any]) -> dict[str, Any]:
    ext = mapping(content.get("externalIDs") or content.get("external_ids"))
    return {
        "locales": tuple(sorted(content_locales(content))),
        "formats": norm_list(content.get("formats")),
        "distribution": scalar(content.get("distrobution") or content.get("distribution")),
        "editions": norm_list(content.get("editions")),
        "hobby_retail_differences": (
            bool(content.get("hasHobbyRetailDifferences"))
            if content.get("hasHobbyRetailDifferences") is not None
            else None
        ),
        "ygoprodeck_content_slug": scalar(ext.get("ygoprodeck")),
    }


def locale_editions_for_uuid(locale: Mapping[str, Any], puid: str) -> tuple[str, ...]:
    editions: set[str] = set()
    for field in ("cardInfo", "card_info", "cardImages", "card_images"):
        by_edition = locale.get(field)
        if not isinstance(by_edition, Mapping):
            continue
        for edition, values in by_edition.items():
            if isinstance(values, Mapping) and puid in values:
                editions.add(sl(edition) or "none")
    return tuple(sorted(editions))


def locale_cardinfo_shape(locale: Mapping[str, Any], puid: str) -> tuple[tuple[str, tuple[str, ...]], ...]:
    """Return non-price/non-image metadata keys by edition for exact UUID.

    Raw image URL and price are deliberately excluded from physical identity:
    price is economic data and a different scan URL does not prove a different
    physical treatment.
    """
    out: list[tuple[str, tuple[str, ...]]] = []
    by_edition = locale.get("cardInfo") or locale.get("card_info")
    if not isinstance(by_edition, Mapping):
        return ()
    for edition, values in by_edition.items():
        if not isinstance(values, Mapping):
            continue
        info = values.get(puid)
        if not isinstance(info, Mapping):
            continue
        keys = tuple(sorted(str(k) for k, v in info.items() if k not in {"image", "price"} and v not in (None, "", [], {})))
        out.append((sl(edition) or "none", keys))
    return tuple(sorted(out))


def signature_payload(record: Mapping[str, Any]) -> dict[str, Any]:
    memberships = record.get("memberships") or []
    # Content index is not semantic. Collapse duplicate memberships that have
    # identical source semantics.
    membership_signatures = {
        json.dumps(
            {
                "content": m["content"],
                "row": m["row"],
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        for m in memberships
    }
    return {
        "locale_editions": record.get("locale_editions") or (),
        "cardinfo_shape": record.get("cardinfo_shape") or (),
        "membership_signatures": tuple(sorted(membership_signatures)),
    }


def run(root: Path, report_path: Path) -> dict[str, Any]:
    sets_path = find_file(root, "sets.json")
    records: dict[str, dict[str, dict[str, Any]]] = {k: {} for k in TARGETS}

    # First pass: collect every target-locale membership and exact row-level
    # semantics for each physical printing UUID.
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
            for content_index, content in enumerate(contents):
                scoped = content_locales(content)
                if not scoped or spec["locale"] not in scoped:
                    continue
                csem = content_semantics(content)
                for row in iter_printings(content):
                    effective_lang = sl(row.get("language")) or loc_lang
                    if effective_lang != spec["language"]:
                        continue
                    puid = s(row.get("id") or row.get("uuid"))
                    if not puid:
                        continue
                    collector = PRINT_COLLECTOR_OVERRIDES.get(
                        puid,
                        collector_code(prefix, s(row.get("suffix"))).upper(),
                    )
                    if collector_quality(collector) != "exact" or not family_from_collector(collector):
                        continue
                    rec = records[target].setdefault(
                        puid,
                        {
                            "print_uuid": puid,
                            "card_uuid": cid(row),
                            "set_uuid": set_uuid,
                            "family": family_from_collector(collector),
                            "collector": collector,
                            "rarity": sl(row.get("rarity")) or "unknown",
                            "memberships": [],
                            "locale_editions": (),
                            "cardinfo_shape": (),
                        },
                    )
                    rec["memberships"].append(
                        {
                            "content_index": content_index,
                            "content": csem,
                            "row": row_semantics(row),
                        }
                    )

            # Locale cardInfo/cardImages lives outside contents. Fill it for
            # the UUIDs that belong to this exact set object and target locale.
            for rec in records[target].values():
                if rec["set_uuid"] != set_uuid:
                    continue
                puid = rec["print_uuid"]
                rec["locale_editions"] = locale_editions_for_uuid(loc, puid)
                rec["cardinfo_shape"] = locale_cardinfo_shape(loc, puid)

    targets: dict[str, Any] = {}
    for target, by_uuid in records.items():
        groups: dict[tuple[str, str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
        for rec in by_uuid.values():
            groups[(rec["set_uuid"], rec["family"], rec["collector"], rec["card_uuid"], rec["rarity"])].append(rec)
        multi = {key: group for key, group in groups.items() if len(group) > 1}

        identical_signature_groups = 0
        all_unique_signature_groups = 0
        partially_unique_signature_groups = 0
        groups_distinguished_by_locale_edition = 0
        groups_distinguished_by_row_semantics = 0
        groups_distinguished_by_content_semantics = 0
        signature_bucket_sizes = Counter()
        samples: list[dict[str, Any]] = []

        for key, group in sorted(multi.items(), key=lambda item: (-len(item[1]), item[0])):
            sig_to_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
            for rec in group:
                payload = signature_payload(rec)
                sig = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=list)
                sig_to_rows[sig].append(rec)
            signature_bucket_sizes[len(sig_to_rows)] += 1
            if len(sig_to_rows) == 1:
                identical_signature_groups += 1
                classification = "source_uuid_alias_same_physical_signature"
            elif len(sig_to_rows) == len(group):
                all_unique_signature_groups += 1
                classification = "all_uuids_have_unique_physical_signature"
            else:
                partially_unique_signature_groups += 1
                classification = "mixed_unique_and_alias_signatures"

            locale_editions = {tuple(rec["locale_editions"]) for rec in group}
            if len(locale_editions) > 1:
                groups_distinguished_by_locale_edition += 1
            row_sigs = {
                json.dumps(sorted({json.dumps(m["row"], sort_keys=True) for m in rec["memberships"]}))
                for rec in group
            }
            if len(row_sigs) > 1:
                groups_distinguished_by_row_semantics += 1
            content_sigs = {
                json.dumps(sorted({json.dumps(m["content"], sort_keys=True, default=list) for m in rec["memberships"]}))
                for rec in group
            }
            if len(content_sigs) > 1:
                groups_distinguished_by_content_semantics += 1

            if len(samples) < 120:
                samples.append(
                    {
                        "set_uuid": key[0],
                        "family": key[1],
                        "collector": key[2],
                        "card_uuid": key[3],
                        "rarity": key[4],
                        "classification": classification,
                        "source_uuid_count": len(group),
                        "distinct_physical_signatures": len(sig_to_rows),
                        "rows": [
                            {
                                "print_uuid": rec["print_uuid"],
                                "locale_editions": list(rec["locale_editions"]),
                                "cardinfo_shape": rec["cardinfo_shape"],
                                "memberships": rec["memberships"],
                            }
                            for rec in group
                        ],
                    }
                )

        targets[target] = {
            "same_rarity_multi_uuid_groups": len(multi),
            "physical_uuids_in_groups": sum(len(group) for group in multi.values()),
            "groups_all_uuid_signatures_identical": identical_signature_groups,
            "groups_all_uuids_unique_signatures": all_unique_signature_groups,
            "groups_mixed_unique_and_alias_signatures": partially_unique_signature_groups,
            "groups_distinguished_by_target_locale_edition": groups_distinguished_by_locale_edition,
            "groups_distinguished_by_row_semantics": groups_distinguished_by_row_semantics,
            "groups_distinguished_by_content_semantics": groups_distinguished_by_content_semantics,
            "distinct_signature_bucket_count_distribution": dict(sorted(signature_bucket_sizes.items())),
            "samples": samples,
        }

    report = {
        "mode": "source_only_ygojson_repeated_uuid_physical_signature",
        "production_writes": 0,
        "signature_policy": {
            "included": [
                "target-locale edition memberships",
                "non-economic cardInfo metadata shape",
                "content locales/formats/distribution/editions/hobby-retail flag/content YGOPRODeck slug",
                "printing onlyInBox/imageID/replica/language override",
            ],
            "excluded": [
                "UUID itself",
                "content array index",
                "price",
                "image URL",
                "qty",
            ],
            "reason": "Only source-declared physical semantics may distinguish multiple UUIDs; technical UUID differences alone do not create physical variants.",
        },
        "targets": targets,
    }
    report["gate_pass"] = all(t["same_rarity_multi_uuid_groups"] > 0 for t in targets.values())
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({
        "gate_pass": report["gate_pass"],
        "targets": {k: {kk: vv for kk, vv in v.items() if kk != "samples"} for k, v in targets.items()},
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
