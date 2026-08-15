#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, text

from app.scripts import audit_yugioh_multilingual_db_compatibility_v2 as v2
from app.scripts.audit_yugioh_multilingual_db_compatibility_v3 import (
    PRINT_COLLECTOR_OVERRIDES,
    build_source_with_exact_collector_recovery,
)
from app.scripts.audit_yugioh_ygojson_canonical_print_projection import (
    canonical_rarity,
    variant_for_rarity,
)

TARGET_REGION = {"es": "global", "ja": "jp"}


def _table_exists(conn, table_name: str) -> bool:
    return bool(
        conn.execute(
            text(
                """
                SELECT EXISTS (
                  SELECT 1 FROM information_schema.tables
                  WHERE table_schema=current_schema() AND table_name=:name
                )
                """
            ),
            {"name": table_name},
        ).scalar_one()
    )


def _column_exists(conn, table_name: str, column_name: str) -> bool:
    return bool(
        conn.execute(
            text(
                """
                SELECT EXISTS (
                  SELECT 1 FROM information_schema.columns
                  WHERE table_schema=current_schema()
                    AND table_name=:table_name AND column_name=:column_name
                )
                """
            ),
            {"table_name": table_name, "column_name": column_name},
        ).scalar_one()
    )


def _print_identity_constraint(conn) -> str | None:
    return conn.execute(
        text(
            """
            SELECT pg_get_constraintdef(c.oid)
            FROM pg_constraint c
            JOIN pg_class t ON t.oid=c.conrelid
            JOIN pg_namespace n ON n.oid=t.relnamespace
            WHERE n.nspname=current_schema()
              AND t.relname='prints'
              AND c.conname='uq_prints_set_number_language_is_foil_variant'
            LIMIT 1
            """
        )
    ).scalar_one_or_none()


def _resolve_card(
    card: dict[str, str],
    db_cards: dict[str, int],
    konami_to_db_cards: dict[str, set[int]],
) -> tuple[int | None, str, bool]:
    raw_ygo = v2.s(card.get("ygoprodeck"))
    canonical_ygo = v2.canonical_ygo_id(raw_ygo)
    if canonical_ygo and canonical_ygo in db_cards:
        return db_cards[canonical_ygo], "ygoprodeck_exact", False
    konami_id = v2.s(card.get("konami"))
    if not konami_id:
        return None, "unresolved", False
    candidates = konami_to_db_cards.get(konami_id, set())
    if len(candidates) == 1:
        return next(iter(candidates)), "konami_exact_alias", False
    if len(candidates) > 1:
        return None, "konami_ambiguous", True
    return None, "unresolved", False


def run(root: Path, report_path: Path) -> dict[str, Any]:
    cards, source_rows, _legacy = build_source_with_exact_collector_recovery(root)
    quarantines, quarantine_details = v2.source_quarantine(cards, source_rows)
    freshness = v2.ygojson_freshness(root)

    url = os.getenv("DATABASE_URL_UNPOOLED") or os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL_UNPOOLED or DATABASE_URL is required")

    engine = create_engine(url, pool_pre_ping=True)
    with engine.connect() as conn:
        tx = conn.begin()
        conn.execute(text("SET TRANSACTION READ ONLY"))
        ro = v2.sl(conn.execute(text("SHOW transaction_read_only")).scalar_one())
        if ro not in {"on", "true", "1"}:
            raise AssertionError(f"transaction_read_only={ro!r}")

        game_id = int(
            conn.execute(text("SELECT id FROM games WHERE slug='yugioh' LIMIT 1")).scalar_one()
        )
        has_region = _column_exists(conn, "sets", "region")
        has_catalog_releases = _table_exists(conn, "catalog_releases")
        has_print_releases = _table_exists(conn, "print_releases")
        has_source_print_id = _column_exists(conn, "print_releases", "source_print_id")
        print_constraint = _print_identity_constraint(conn)
        card_scoped_print_identity = bool(
            print_constraint and "card_id" in print_constraint.casefold()
        )
        alembic_revision = conn.execute(
            text("SELECT version_num FROM alembic_version LIMIT 1")
        ).scalar_one_or_none()

        db_cards = {
            str(ext): int(card_id)
            for card_id, ext in conn.execute(
                text(
                    "SELECT id, yugoprodeck_id FROM cards "
                    "WHERE game_id=:g AND yugoprodeck_id IS NOT NULL"
                ),
                {"g": game_id},
            )
            if ext is not None
        }
        if has_region:
            set_rows = list(
                conn.execute(
                    text(
                        "SELECT id, code, coalesce(region,'global') "
                        "FROM sets WHERE game_id=:g"
                    ),
                    {"g": game_id},
                )
            )
        else:
            set_rows = [
                (sid, code, "global")
                for sid, code in conn.execute(
                    text("SELECT id, code FROM sets WHERE game_id=:g"), {"g": game_id}
                )
            ]
        db_sets = {
            (v2.s(code).upper(), v2.sl(region) or "global"): int(sid)
            for sid, code, region in set_rows
            if v2.s(code)
        }

        db_print_rows = list(
            conn.execute(
                text(
                    """
                    SELECT p.id, p.set_id, p.card_id, upper(p.collector_number),
                           coalesce(lower(p.language),''), p.is_foil,
                           coalesce(p.variant,''), p.yugioh_id
                    FROM prints p
                    JOIN cards c ON c.id=p.card_id
                    WHERE c.game_id=:g
                    """
                ),
                {"g": game_id},
            )
        )
        tx.rollback()

    # Exact source-backed bridge only. Names are never consulted.
    konami_to_db_cards: dict[str, set[int]] = defaultdict(set)
    for card in cards.values():
        konami_id = v2.s(card.get("konami"))
        ygo_id = v2.canonical_ygo_id(card.get("ygoprodeck"))
        db_card_id = db_cards.get(ygo_id) if ygo_id else None
        if konami_id and db_card_id is not None:
            konami_to_db_cards[konami_id].add(db_card_id)

    db_exact = Counter()
    db_legacy_slots: dict[tuple[int, str, str, bool, str], set[int]] = defaultdict(set)
    existing_ygo_ids: set[str] = set()
    for _pid, set_id, card_id, collector, language, foil, variant, ygo_id in db_print_rows:
        exact_key = (
            int(card_id),
            int(set_id),
            v2.s(collector).upper(),
            v2.sl(language),
            bool(foil),
            v2.s(variant),
        )
        db_exact[exact_key] += 1
        db_legacy_slots[
            (int(set_id), v2.s(collector).upper(), v2.sl(language), bool(foil), v2.s(variant))
        ].add(int(card_id))
        if ygo_id:
            existing_ygo_ids.add(str(ygo_id))

    targets: dict[str, Any] = {}
    all_source_print_uuids: set[str] = set()

    for target, spec in v2.TARGETS.items():
        region = TARGET_REGION[target]
        quarantine = quarantines[target]
        exact_rows: list[dict[str, Any]] = []
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
            card = cards.get(card_uuid) or {}
            logical = v2.logical_card_identity(card, card_uuid)
            rarity = canonical_rarity(row.get("rarity"))
            exact_rows.append(
                {
                    "print_uuid": puid,
                    "set_uuid": set_uuid,
                    "card_uuid": card_uuid,
                    "logical_card": logical,
                    "collector": collector,
                    "family": family,
                    "rarity": rarity,
                    "variant": variant_for_rarity(rarity),
                }
            )
            all_source_print_uuids.add(puid)

        groups: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
        membership_uuids: dict[tuple[tuple[str, str, str], str], set[str]] = defaultdict(set)
        for row in exact_rows:
            identity = (row["logical_card"], row["collector"], row["rarity"])
            groups[identity].append(row)
            membership_uuids[(identity, row["set_uuid"])].add(row["print_uuid"])

        membership_alias_conflicts = {
            key: uuids for key, uuids in membership_uuids.items() if len(uuids) > 1
        }
        counters = Counter()
        resolution_modes = Counter()
        matched_cards: set[int] = set()
        create_families: set[str] = set()
        existing_families: set[str] = set()
        missing_card_samples: list[dict[str, Any]] = []
        ambiguous_card_samples: list[dict[str, Any]] = []
        family_conflict_samples: list[dict[str, Any]] = []
        existing_duplicate_samples: list[dict[str, Any]] = []
        legacy_cross_card_samples: list[dict[str, Any]] = []
        ready_identities: set[tuple[str, str, str]] = set()

        for identity, rows in groups.items():
            families = {row["family"] for row in rows if row["family"]}
            card_uuids = {row["card_uuid"] for row in rows}
            resolved_ids: set[int] = set()
            modes: set[str] = set()
            ambiguous = False
            for card_uuid in card_uuids:
                db_card_id, mode, is_ambiguous = _resolve_card(
                    cards.get(card_uuid) or {}, db_cards, konami_to_db_cards
                )
                modes.add(mode)
                ambiguous = ambiguous or is_ambiguous
                if db_card_id is not None:
                    resolved_ids.add(db_card_id)

            if ambiguous or len(resolved_ids) > 1:
                counters["card_resolution_ambiguous"] += 1
                if len(ambiguous_card_samples) < 40:
                    ambiguous_card_samples.append(
                        {
                            "logical_card": identity[0],
                            "collector": identity[1],
                            "rarity": identity[2],
                            "resolved_db_card_ids": sorted(resolved_ids),
                            "source_card_uuids": sorted(card_uuids),
                        }
                    )
                continue

            if not resolved_ids:
                counters["retained_card_not_in_db"] += 1
                if len(missing_card_samples) < 40:
                    first = rows[0]
                    card = cards.get(first["card_uuid"]) or {}
                    missing_card_samples.append(
                        {
                            "logical_card": identity[0],
                            "collector": identity[1],
                            "rarity": identity[2],
                            "ygoprodeck_id": v2.canonical_ygo_id(card.get("ygoprodeck")) or None,
                            "konami_id": v2.s(card.get("konami")) or None,
                        }
                    )
                continue

            db_card_id = next(iter(resolved_ids))
            matched_cards.add(db_card_id)
            if "ygoprodeck_exact" in modes:
                resolution_modes["ygoprodeck_exact"] += 1
            elif "konami_exact_alias" in modes:
                resolution_modes["konami_exact_alias"] += 1
            else:
                resolution_modes["exact_source_alias_group"] += 1

            if len(families) != 1:
                counters["canonical_family_conflict"] += 1
                if len(family_conflict_samples) < 40:
                    family_conflict_samples.append(
                        {
                            "logical_card": identity[0],
                            "collector": identity[1],
                            "rarity": identity[2],
                            "families": sorted(families),
                        }
                    )
                continue
            family = next(iter(families))
            set_id = db_sets.get((family, region))
            if set_id is None:
                counters["set_create_canonical_prints"] += 1
                create_families.add(family)
                counters["materializable_canonical_prints"] += 1
                counters["new_candidate_canonical_prints"] += 1
                ready_identities.add(identity)
                continue

            existing_families.add(family)
            counters["set_existing_canonical_prints"] += 1
            variant = rows[0]["variant"]
            exact_key = (
                db_card_id,
                set_id,
                identity[1],
                spec["language"],
                False,
                variant,
            )
            exact_count = db_exact.get(exact_key, 0)
            if exact_count > 1:
                counters["existing_exact_duplicate_groups"] += 1
                if len(existing_duplicate_samples) < 40:
                    existing_duplicate_samples.append(
                        {
                            "db_card_id": db_card_id,
                            "set_id": set_id,
                            "collector": identity[1],
                            "language": spec["language"],
                            "variant": variant,
                            "row_count": exact_count,
                        }
                    )
                continue

            counters["materializable_canonical_prints"] += 1
            ready_identities.add(identity)
            if exact_count == 1:
                counters["existing_idempotent_canonical_prints"] += 1
            else:
                counters["new_candidate_canonical_prints"] += 1
                legacy_cards = db_legacy_slots.get(
                    (set_id, identity[1], spec["language"], False, variant), set()
                )
                other_cards = legacy_cards - {db_card_id}
                if other_cards:
                    counters["legacy_cross_card_slot_reuse_requires_m36"] += 1
                    if len(legacy_cross_card_samples) < 40:
                        legacy_cross_card_samples.append(
                            {
                                "db_card_id": db_card_id,
                                "existing_other_card_ids": sorted(other_cards),
                                "set_id": set_id,
                                "collector": identity[1],
                                "language": spec["language"],
                                "variant": variant,
                            }
                        )

        ready_memberships = sum(
            1
            for (identity, _release_uuid), uuids in membership_uuids.items()
            if identity in ready_identities and len(uuids) == 1
        )
        canonical_accounted = (
            counters["materializable_canonical_prints"]
            + counters["retained_card_not_in_db"]
            + counters["card_resolution_ambiguous"]
            + counters["canonical_family_conflict"]
            + counters["existing_exact_duplicate_groups"]
        )

        targets[target] = {
            "language": spec["language"],
            "region": region,
            "source_rows_after_quarantine": len(exact_rows),
            "source_unique_print_uuids": len({r["print_uuid"] for r in exact_rows}),
            "source_print_release_memberships": len(membership_uuids),
            "canonical_prints": len(groups),
            "canonical_prints_accounted": canonical_accounted,
            "materializable_canonical_prints": counters["materializable_canonical_prints"],
            "new_candidate_canonical_prints": counters["new_candidate_canonical_prints"],
            "existing_idempotent_canonical_prints": counters["existing_idempotent_canonical_prints"],
            "retained_card_not_in_db_canonical_prints": counters["retained_card_not_in_db"],
            "card_resolution_ambiguous_canonical_prints": counters["card_resolution_ambiguous"],
            "canonical_family_conflicts": counters["canonical_family_conflict"],
            "existing_exact_duplicate_groups": counters["existing_exact_duplicate_groups"],
            "legacy_cross_card_slot_reuse_requires_m36": counters[
                "legacy_cross_card_slot_reuse_requires_m36"
            ],
            "ready_print_release_memberships": ready_memberships,
            "membership_alias_conflict_groups": len(membership_alias_conflicts),
            "unique_db_cards_matched": len(matched_cards),
            "card_resolution_modes": dict(sorted(resolution_modes.items())),
            "set_existing_region_families": len(existing_families),
            "set_create_region_families": len(create_families),
            "set_create_region_family_samples": sorted(create_families)[:80],
            "quarantine": quarantine_details[target],
            "samples": {
                "missing_cards": missing_card_samples,
                "ambiguous_card_resolution": ambiguous_card_samples,
                "canonical_family_conflicts": family_conflict_samples,
                "existing_exact_duplicates": existing_duplicate_samples,
                "legacy_cross_card_slot_reuse": legacy_cross_card_samples,
            },
        }

    provenance_schema_present = bool(
        has_catalog_releases and has_print_releases and has_source_print_id
    )
    source_uuid_overlap_with_print_yugioh_id = len(all_source_print_uuids & existing_ygo_ids)

    structural_gates = {
        "read_only_enforced": True,
        "all_exact_collector_overrides_present": len(PRINT_COLLECTOR_OVERRIDES) == 8,
        "all_canonical_prints_accounted": all(
            t["canonical_prints_accounted"] == t["canonical_prints"]
            for t in targets.values()
        ),
        "no_membership_alias_conflicts": all(
            t["membership_alias_conflict_groups"] == 0 for t in targets.values()
        ),
        "no_card_resolution_ambiguities": all(
            t["card_resolution_ambiguous_canonical_prints"] == 0
            for t in targets.values()
        ),
        "one_family_per_canonical_print": all(
            t["canonical_family_conflicts"] == 0 for t in targets.values()
        ),
        "no_existing_exact_duplicate_prints": all(
            t["existing_exact_duplicate_groups"] == 0 for t in targets.values()
        ),
        "materializable_nonzero": all(
            t["materializable_canonical_prints"] > 0 for t in targets.values()
        ),
        "provenance_schema_available": provenance_schema_present,
        "localized_source_uuid_not_used_as_print_identity": True,
        "prices_not_read_or_written": True,
    }
    structural_pass = all(structural_gates.values())
    rollout_freshness_pass = freshness.get("status") == "current_enough"
    schema_rollout_gates = {
        "set_region_identity_present": has_region,
        "card_scoped_print_identity_present": card_scoped_print_identity,
        "provenance_schema_present": provenance_schema_present,
    }
    schema_rollout_pass = all(schema_rollout_gates.values())

    report = {
        "mode": "read_only_ygojson_card_scoped_regional_db_compatibility_v4",
        "production_writes": 0,
        "database_transaction_read_only": True,
        "database_schema": {
            "alembic_revision": alembic_revision,
            "sets_region_column_present": has_region,
            "print_identity_constraint": print_constraint,
            "card_scoped_print_identity_present": card_scoped_print_identity,
            "catalog_releases_present": has_catalog_releases,
            "print_releases_present": has_print_releases,
            "print_releases_source_print_id_present": has_source_print_id,
        },
        "database_inventory": {
            "cards_with_yugoprodeck_id": len(db_cards),
            "sets": len(db_sets),
            "prints": len(db_print_rows),
            "prints_with_yugioh_id": len(existing_ygo_ids),
        },
        "identity_policy": {
            "card": "exact YGOProDeck ID or exact unambiguous official Konami-ID source alias only; never name/fuzzy matching",
            "set": "family code + explicit region; ES=global and JA=jp",
            "print": "Card + Set + full collector + language + is_foil + deterministic rarity-derived variant",
            "source_print_uuid": "provenance only in PrintRelease.source_print_id; never localized Print.yugioh_id identity",
            "release": "YGOJSON set UUID -> CatalogRelease(source=ygojson)",
            "prices": "not read, inherited or written by this audit",
        },
        "source_freshness": freshness,
        "rollout_freshness_pass": rollout_freshness_pass,
        "source_uuid_overlap_with_existing_print_yugioh_id": source_uuid_overlap_with_print_yugioh_id,
        "exact_collector_recovery_count": len(PRINT_COLLECTOR_OVERRIDES),
        "targets": targets,
        "structural_gates": structural_gates,
        "structural_pass": structural_pass,
        "schema_rollout_gates": schema_rollout_gates,
        "schema_rollout_pass": schema_rollout_pass,
        "production_rollout_ready": bool(
            structural_pass and rollout_freshness_pass and schema_rollout_pass
        ),
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "mode": report["mode"],
                "production_writes": 0,
                "database_transaction_read_only": True,
                "database_schema": report["database_schema"],
                "source_freshness": freshness,
                "targets": {
                    key: {
                        "canonical_prints": value["canonical_prints"],
                        "materializable_canonical_prints": value[
                            "materializable_canonical_prints"
                        ],
                        "retained_card_not_in_db": value[
                            "retained_card_not_in_db_canonical_prints"
                        ],
                        "ready_print_release_memberships": value[
                            "ready_print_release_memberships"
                        ],
                        "membership_alias_conflicts": value[
                            "membership_alias_conflict_groups"
                        ],
                        "card_resolution_ambiguous": value[
                            "card_resolution_ambiguous_canonical_prints"
                        ],
                        "legacy_cross_card_slot_reuse_requires_m36": value[
                            "legacy_cross_card_slot_reuse_requires_m36"
                        ],
                    }
                    for key, value in targets.items()
                },
                "structural_gates": structural_gates,
                "structural_pass": structural_pass,
                "schema_rollout_gates": schema_rollout_gates,
                "schema_rollout_pass": schema_rollout_pass,
                "production_rollout_ready": report["production_rollout_ready"],
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
    return 0 if report["structural_pass"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
