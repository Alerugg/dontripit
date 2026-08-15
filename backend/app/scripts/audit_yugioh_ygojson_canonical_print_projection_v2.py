#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path
from typing import Any

from app.scripts import audit_yugioh_ygojson_canonical_print_projection as v1


def run(root: Path, report_path: Path) -> dict[str, Any]:
    # Reuse the fully audited source parsing/quarantine/canonicalization from v1.
    # v1 deliberately fails under the former cross-card uniqueness rule; v2
    # reclassifies only that one condition after migration 36 establishes that
    # a physical Print is scoped to its Card.
    with tempfile.TemporaryDirectory(prefix="ygo-projection-v1-") as tmp:
        base = v1.run(root, Path(tmp) / "base.json")

    targets: dict[str, Any] = {}
    for target, source in base["targets"].items():
        target_report = dict(source)
        old_gates = dict(source["gates"])
        visible_reuse_count = int(source["family_region_physical_slot_conflicts"])
        samples = list(source.get("family_slot_conflict_samples") or [])

        # The immutable historical snapshot currently has only 11 such JA
        # groups, so every group is represented in the <=80 v1 evidence sample.
        # Refuse to certify if that stops being true rather than silently accept
        # an unseen new collision class.
        all_reuse_groups_sampled = len(samples) == visible_reuse_count
        all_reuse_groups_cross_card = all(
            len(set(sample.get("logical_cards") or [])) >= 2
            for sample in samples
        )
        all_reuse_groups_cross_release = all(
            len(set(sample.get("source_set_uuids") or [])) >= 2
            for sample in samples
        )

        old_gates.pop("family_region_physical_slots_have_one_card", None)
        old_gates.update(
            {
                "all_visible_slot_reuse_groups_evidenced": all_reuse_groups_sampled,
                "visible_slot_reuse_is_cross_card": all_reuse_groups_cross_card,
                "visible_slot_reuse_is_cross_release": all_reuse_groups_cross_release,
                "card_scoped_unique_key_handles_visible_reuse": True,
            }
        )
        target_report["gates"] = old_gates
        target_report["visible_collector_slot_reuse_groups"] = visible_reuse_count
        target_report["visible_collector_slot_reuse_policy"] = (
            "Allowed only because migration 20260815_36 scopes physical uniqueness by card_id. "
            "The exact release remains disambiguated through CatalogRelease/PrintRelease."
        )
        target_report["card_scoped_projection_pass"] = all(old_gates.values())
        targets[target] = target_report

    gates = {
        "target_gates_pass": all(t["card_scoped_projection_pass"] for t in targets.values()),
        "canonical_projection_is_language_scoped": bool(
            base["gates"].get("canonical_projection_is_language_scoped")
        ),
        "requires_migration_20260815_36": True,
        "release_identity_preserved": (
            base["identity_policy"].get("release")
            == "YGOJSON set UUID -> CatalogRelease(source=ygojson)"
        ),
    }

    report = dict(base)
    report.update(
        {
            "mode": "source_only_ygojson_release_aware_card_scoped_canonical_print_projection_v2",
            "schema_requirement": {
                "migration": "20260815_36",
                "constraint": "uq_prints_set_number_language_is_foil_variant",
                "columns": [
                    "card_id",
                    "set_id",
                    "collector_number",
                    "language",
                    "is_foil",
                    "variant",
                ],
            },
            "targets": targets,
            "gates": gates,
            "gate_pass": all(gates.values()),
        }
    )
    report["identity_policy"] = dict(base["identity_policy"])
    report["identity_policy"]["physical_db_unique"] = (
        "card_id + Set family/region + collector + language + foil + rarity-derived variant"
    )
    report["identity_policy"]["visible_collector_reuse"] = (
        "same visible collector/rarity may belong to different Cards in different releases; "
        "Card scopes Print identity and CatalogRelease scopes product identity"
    )

    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "mode": report["mode"],
                "gate_pass": report["gate_pass"],
                "gates": gates,
                "targets": {
                    target: {
                        "source_unique_print_uuids": data["source_unique_print_uuids"],
                        "canonical_prints": data["canonical_prints_after_card_collector_rarity_collapse"],
                        "visible_collector_slot_reuse_groups": data["visible_collector_slot_reuse_groups"],
                        "source_uuid_identity_conflicts": data["source_uuid_identity_conflicts"],
                        "card_scoped_projection_pass": data["card_scoped_projection_pass"],
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
