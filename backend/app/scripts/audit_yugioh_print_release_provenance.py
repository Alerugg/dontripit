#!/usr/bin/env python3
from __future__ import annotations

# Source-only gate: this module never opens the production database.
import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from app.scripts import audit_yugioh_multilingual_db_compatibility_v2 as v2
from app.scripts.audit_yugioh_multilingual_db_compatibility_v3 import (
    build_source_with_exact_collector_recovery,
)
from app.scripts.audit_yugioh_ygojson_canonical_print_projection import canonical_rarity


def run(root: Path, report_path: Path) -> dict[str, Any]:
    cards, source_rows, _legacy = build_source_with_exact_collector_recovery(root)
    quarantines, quarantine_details = v2.source_quarantine(cards, source_rows)

    targets: dict[str, Any] = {}
    global_uuid_targets: dict[str, set[str]] = defaultdict(set)

    for target, spec in v2.TARGETS.items():
        quarantine = quarantines[target]
        exact_rows: list[dict[str, str]] = []
        for row in source_rows[target]:
            puid = v2.s(row.get("print_uuid"))
            if not puid or puid in quarantine or row.get("quality") != "exact":
                continue
            collector = v2.s(row.get("collector")).upper()
            family = v2.s(row.get("family")).upper()
            set_uuid = v2.s(row.get("set_uuid"))
            card_uuid = v2.s(row.get("card_uuid"))
            if not collector or not family or not set_uuid or not card_uuid:
                continue
            logical_card = v2.logical_card_identity(cards.get(card_uuid) or {}, card_uuid)
            rarity = canonical_rarity(row.get("rarity"))
            exact_rows.append(
                {
                    "print_uuid": puid,
                    "set_uuid": set_uuid,
                    "card_uuid": card_uuid,
                    "logical_card": logical_card,
                    "collector": collector,
                    "family": family,
                    "rarity": rarity,
                }
            )
            global_uuid_targets[puid].add(target)

        # Canonical physical identity deliberately excludes source release UUID.
        # Product/release provenance is represented separately by PrintRelease.
        membership_uuids: dict[tuple[str, str, str, str], set[str]] = defaultdict(set)
        uuid_identities: dict[str, set[tuple[str, str, str, str]]] = defaultdict(set)
        uuid_releases: dict[str, set[str]] = defaultdict(set)
        canonical_releases: dict[tuple[str, str, str], set[str]] = defaultdict(set)
        canonical_uuids: dict[tuple[str, str, str], set[str]] = defaultdict(set)

        for row in exact_rows:
            canonical = (row["logical_card"], row["collector"], row["rarity"])
            membership = canonical + (row["set_uuid"],)
            membership_uuids[membership].add(row["print_uuid"])
            uuid_identities[row["print_uuid"]].add(
                (row["logical_card"], row["collector"], row["rarity"], row["family"])
            )
            uuid_releases[row["print_uuid"]].add(row["set_uuid"])
            canonical_releases[canonical].add(row["set_uuid"])
            canonical_uuids[canonical].add(row["print_uuid"])

        membership_alias_conflicts = {
            membership: uuids
            for membership, uuids in membership_uuids.items()
            if len(uuids) > 1
        }
        uuid_identity_conflicts = {
            puid: identities
            for puid, identities in uuid_identities.items()
            if len(identities) > 1
        }
        repeated_uuid_across_releases = {
            puid: releases
            for puid, releases in uuid_releases.items()
            if len(releases) > 1
        }
        canonical_alias_groups = {
            canonical: uuids
            for canonical, uuids in canonical_uuids.items()
            if len(uuids) > 1
        }

        membership_uuid_distribution = Counter(len(v) for v in membership_uuids.values())
        release_count_distribution = Counter(len(v) for v in canonical_releases.values())

        gates = {
            "exact_rows_present": bool(exact_rows),
            "every_membership_has_source_print_uuid": all(bool(v) for v in membership_uuids.values()),
            "one_source_print_uuid_per_print_release_membership": not membership_alias_conflicts,
            "source_print_uuid_has_stable_physical_identity": not uuid_identity_conflicts,
        }
        targets[target] = {
            "language": spec["language"],
            "source_rows_after_quarantine": len(exact_rows),
            "source_unique_print_uuids": len(uuid_identities),
            "canonical_prints": len(canonical_releases),
            "print_release_memberships": len(membership_uuids),
            "canonical_prints_with_multiple_source_uuid_aliases": len(canonical_alias_groups),
            "source_print_uuids_reused_across_releases": len(repeated_uuid_across_releases),
            "membership_alias_conflict_groups": len(membership_alias_conflicts),
            "source_uuid_identity_conflicts": len(uuid_identity_conflicts),
            "max_source_uuids_per_print_release_membership": max(membership_uuid_distribution, default=0),
            "max_releases_per_canonical_print": max(release_count_distribution, default=0),
            "membership_source_uuid_count_distribution": dict(sorted(membership_uuid_distribution.items())),
            "canonical_release_count_distribution": dict(sorted(release_count_distribution.items())),
            "quarantine": quarantine_details[target],
            "gates": gates,
            "membership_alias_conflict_samples": [
                {
                    "logical_card": membership[0],
                    "collector": membership[1],
                    "rarity": membership[2],
                    "set_uuid": membership[3],
                    "source_print_uuids": sorted(uuids),
                }
                for membership, uuids in list(sorted(membership_alias_conflicts.items()))[:80]
            ],
            "source_uuid_identity_conflict_samples": [
                {"print_uuid": puid, "identities": sorted(values)}
                for puid, values in list(sorted(uuid_identity_conflicts.items()))[:80]
            ],
        }

    cross_language_reuse = {
        puid: sorted(targets_)
        for puid, targets_ in global_uuid_targets.items()
        if len(targets_) > 1
    }
    gates = {
        "target_gates_pass": all(all(t["gates"].values()) for t in targets.values()),
        "print_release_schema_can_preserve_source_uuid": all(
            t["membership_alias_conflict_groups"] == 0 for t in targets.values()
        ),
        "cross_language_source_uuid_reuse_is_not_print_identity": True,
    }
    report = {
        "mode": "source_only_ygojson_print_release_provenance",
        "production_writes": 0,
        "identity_policy": {
            "canonical_print": "language + logical card + collector + source rarity",
            "release": "YGOJSON set UUID -> CatalogRelease(source=ygojson)",
            "membership": "canonical Print + CatalogRelease -> one PrintRelease row",
            "source_print_uuid": "PrintRelease.source_print_id evidence; never Print.yugioh_id for localized identity",
            "cross_language_uuid_reuse": "allowed because source UUID is provenance, not canonical localized Print identity",
        },
        "targets": targets,
        "cross_language_source_uuid_reuse_count": len(cross_language_reuse),
        "cross_language_source_uuid_reuse_samples": list(sorted(cross_language_reuse.items()))[:80],
        "gates": gates,
        "gate_pass": all(gates.values()),
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "gate_pass": report["gate_pass"],
                "gates": gates,
                "cross_language_source_uuid_reuse_count": len(cross_language_reuse),
                "targets": {
                    target: {
                        "source_unique_print_uuids": data["source_unique_print_uuids"],
                        "canonical_prints": data["canonical_prints"],
                        "print_release_memberships": data["print_release_memberships"],
                        "canonical_alias_groups": data["canonical_prints_with_multiple_source_uuid_aliases"],
                        "membership_alias_conflicts": data["membership_alias_conflict_groups"],
                        "source_uuid_identity_conflicts": data["source_uuid_identity_conflicts"],
                        "max_source_uuids_per_membership": data["max_source_uuids_per_print_release_membership"],
                    }
                    for target, data in targets.items()
                },
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
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
