#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from app.scripts import audit_yugioh_multilingual_db_compatibility_v2 as v2
from app.scripts.audit_yugioh_multilingual_db_compatibility_v3 import (
    build_source_with_exact_collector_recovery,
)

TARGET_REGION = {"es": "global", "ja": "jp"}


def canonical_rarity(value: object) -> str:
    # YGOJSON rarity is an enum/token. Keep source semantics and normalize only
    # whitespace/case for identity; display-name enrichment is a later concern.
    return v2.sl(value) or "unknown"


def variant_for_rarity(rarity: str) -> str:
    # Mirrors the existing YGO v2 philosophy: rarity is the discriminator in
    # Print.variant because rarity is not part of uq_print_physical.
    raw = "".join(ch if ch.isalnum() else "-" for ch in rarity.casefold())
    slug = "-".join(part for part in raw.split("-") if part) or "unknown"
    value = f"rarity-{slug}"
    if len(value) <= 100:
        return value
    digest = hashlib.sha1(value.encode("utf-8")).hexdigest()[:12]
    return f"rarity-{slug[:78]}-{digest}"


def canonical_print_id(target: str, logical_card: str, collector: str, rarity: str) -> str:
    # Language is included deliberately: ES/JA are distinct physical prints.
    raw = "|".join((target, logical_card, collector, rarity))
    return "ygo-localized-v1:" + hashlib.sha1(raw.encode("utf-8")).hexdigest()


def run(root: Path, report_path: Path) -> dict[str, Any]:
    cards, source_rows, _legacy = build_source_with_exact_collector_recovery(root)
    quarantines, quarantine_details = v2.source_quarantine(cards, source_rows)

    targets: dict[str, Any] = {}
    global_uuid_targets: dict[str, set[str]] = defaultdict(set)

    for target, spec in v2.TARGETS.items():
        quarantine = quarantines[target]
        exact_rows: list[dict[str, Any]] = []
        for row in source_rows[target]:
            puid = v2.s(row.get("print_uuid"))
            if not puid or puid in quarantine or row.get("quality") != "exact":
                continue
            collector = v2.s(row.get("collector")).upper()
            family = v2.s(row.get("family")).upper()
            if not collector or not family:
                continue
            card_uuid = v2.s(row.get("card_uuid"))
            logical_card = v2.logical_card_identity(cards.get(card_uuid) or {}, card_uuid)
            rarity = canonical_rarity(row.get("rarity"))
            exact_rows.append(
                {
                    "print_uuid": puid,
                    "set_uuid": v2.s(row.get("set_uuid")),
                    "card_uuid": card_uuid,
                    "logical_card": logical_card,
                    "family": family,
                    "collector": collector,
                    "rarity": rarity,
                    "variant": variant_for_rarity(rarity),
                    "canonical_print_id": canonical_print_id(target, logical_card, collector, rarity),
                }
            )
            global_uuid_targets[puid].add(target)

        # A YGOJSON print UUID may be repeated as membership in multiple source
        # releases. It must not change its physical identity when reused.
        uuid_identities: dict[str, set[tuple[str, str, str, str]]] = defaultdict(set)
        uuid_releases: dict[str, set[str]] = defaultdict(set)
        uuid_membership_count = Counter()
        for row in exact_rows:
            uuid_identities[row["print_uuid"]].add(
                (row["logical_card"], row["collector"], row["rarity"], row["family"])
            )
            if row["set_uuid"]:
                uuid_releases[row["print_uuid"]].add(row["set_uuid"])
            uuid_membership_count[row["print_uuid"]] += 1

        uuid_identity_conflicts = {
            puid: values for puid, values in uuid_identities.items() if len(values) > 1
        }

        # Collapse repeated memberships and UUID aliases to Don’tRipIt’s existing
        # YGO canonical identity: logical card + full collector + canonical rarity.
        canonical_groups: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
        seen_membership = set()
        unique_memberships: list[dict[str, Any]] = []
        for row in exact_rows:
            membership_key = (row["print_uuid"], row["set_uuid"])
            if membership_key not in seen_membership:
                seen_membership.add(membership_key)
                unique_memberships.append(row)
            canonical_groups[(row["logical_card"], row["collector"], row["rarity"])].append(row)

        # Print table collision surface after Set family+region projection. A slot
        # cannot contain two different Cards with the same rarity-derived variant.
        physical_slots: dict[tuple[str, str, str], set[str]] = defaultdict(set)
        physical_slot_rows: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
        for row in exact_rows:
            key = (row["family"], row["collector"], row["variant"])
            physical_slots[key].add(row["logical_card"])
            physical_slot_rows[key].append(row)
        family_slot_conflicts = {
            key: identities for key, identities in physical_slots.items() if len(identities) > 1
        }

        canonical_uuid_counts = Counter()
        canonical_release_counts = Counter()
        canonical_alias_groups = 0
        canonical_multi_release_groups = 0
        alias_samples = []
        multi_release_samples = []
        for identity, rows in canonical_groups.items():
            uuids = {r["print_uuid"] for r in rows}
            releases = {r["set_uuid"] for r in rows if r["set_uuid"]}
            canonical_uuid_counts[len(uuids)] += 1
            canonical_release_counts[len(releases)] += 1
            if len(uuids) > 1:
                canonical_alias_groups += 1
                if len(alias_samples) < 60:
                    alias_samples.append(
                        {
                            "logical_card": identity[0],
                            "collector": identity[1],
                            "rarity": identity[2],
                            "source_print_uuids": sorted(uuids),
                            "source_set_uuids": sorted(releases),
                        }
                    )
            if len(releases) > 1:
                canonical_multi_release_groups += 1
                if len(multi_release_samples) < 60:
                    multi_release_samples.append(
                        {
                            "logical_card": identity[0],
                            "collector": identity[1],
                            "rarity": identity[2],
                            "source_print_uuids": sorted(uuids),
                            "source_set_uuids": sorted(releases),
                        }
                    )

        reused_uuid_count = sum(1 for puid, releases in uuid_releases.items() if len(releases) > 1)
        reused_uuid_memberships = sum(
            uuid_membership_count[puid]
            for puid, releases in uuid_releases.items()
            if len(releases) > 1
        )

        conflict_samples = []
        for key, identities in list(sorted(family_slot_conflicts.items()))[:80]:
            rows = physical_slot_rows[key]
            conflict_samples.append(
                {
                    "family": key[0],
                    "collector": key[1],
                    "variant": key[2],
                    "logical_cards": sorted(identities),
                    "source_set_uuids": sorted({r["set_uuid"] for r in rows}),
                    "source_print_uuids": sorted({r["print_uuid"] for r in rows}),
                }
            )

        target_gates = {
            "exact_rows_present": bool(exact_rows),
            "all_exact_rows_accounted": len(exact_rows) > 0,
            "source_uuid_identity_stable_across_releases": not uuid_identity_conflicts,
            "family_region_physical_slots_have_one_card": not family_slot_conflicts,
            "canonical_ids_unique": len({
                canonical_print_id(target, identity[0], identity[1], identity[2])
                for identity in canonical_groups
            }) == len(canonical_groups),
        }
        targets[target] = {
            "language": spec["language"],
            "region": TARGET_REGION[target],
            "source_rows_after_quarantine": len(exact_rows),
            "source_unique_print_uuids": len(uuid_identities),
            "source_unique_release_memberships": len(seen_membership),
            "source_release_uuids": len({r["set_uuid"] for r in exact_rows if r["set_uuid"]}),
            "quarantine": quarantine_details[target],
            "canonical_prints_after_card_collector_rarity_collapse": len(canonical_groups),
            "source_uuid_alias_groups_collapsed": canonical_alias_groups,
            "canonical_prints_in_multiple_releases": canonical_multi_release_groups,
            "source_print_uuids_reused_across_releases": reused_uuid_count,
            "memberships_of_reused_source_uuids": reused_uuid_memberships,
            "max_source_uuids_per_canonical_print": max(canonical_uuid_counts, default=0),
            "max_releases_per_canonical_print": max(canonical_release_counts, default=0),
            "canonical_source_uuid_count_distribution": dict(sorted(canonical_uuid_counts.items())),
            "canonical_release_count_distribution": dict(sorted(canonical_release_counts.items())),
            "source_uuid_identity_conflicts": len(uuid_identity_conflicts),
            "family_region_physical_slot_conflicts": len(family_slot_conflicts),
            "gates": target_gates,
            "alias_samples": alias_samples,
            "multi_release_samples": multi_release_samples,
            "uuid_identity_conflict_samples": [
                {"print_uuid": puid, "identities": sorted(values)}
                for puid, values in list(uuid_identity_conflicts.items())[:60]
            ],
            "family_slot_conflict_samples": conflict_samples,
        }

    cross_language_uuid_reuse = {
        puid: sorted(langs) for puid, langs in global_uuid_targets.items() if len(langs) > 1
    }
    gates = {
        "target_gates_pass": all(all(t["gates"].values()) for t in targets.values()),
        # Cross-language reuse is diagnostic: YGOJSON may intentionally use the
        # same printing object for language-overridden rows. ES/JA canonical IDs
        # still include target language, so this is not automatically a conflict.
        "canonical_projection_is_language_scoped": True,
    }
    report = {
        "mode": "source_only_ygojson_release_aware_canonical_print_projection",
        "production_writes": 0,
        "identity_policy": {
            "canonical_print": "target language + logical card (Konami preferred) + full collector + canonical source rarity",
            "variant": "deterministic rarity-derived variant, matching existing YGO v2 philosophy",
            "release": "YGOJSON set UUID -> CatalogRelease(source=ygojson)",
            "membership": "YGOJSON print UUID + set UUID -> PrintRelease; repeated memberships never force a duplicate Print",
            "source_uuid": "evidence identifier, not canonical Print identity by itself",
        },
        "targets": targets,
        "cross_language_source_uuid_reuse_count": len(cross_language_uuid_reuse),
        "cross_language_source_uuid_reuse_samples": list(cross_language_uuid_reuse.items())[:80],
        "gates": gates,
        "gate_pass": all(gates.values()),
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({
        "gate_pass": report["gate_pass"],
        "gates": gates,
        "targets": {
            k: {kk: vv for kk, vv in v.items() if kk not in {
                "alias_samples", "multi_release_samples", "uuid_identity_conflict_samples", "family_slot_conflict_samples"
            }}
            for k, v in targets.items()
        },
        "cross_language_source_uuid_reuse_count": len(cross_language_uuid_reuse),
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
