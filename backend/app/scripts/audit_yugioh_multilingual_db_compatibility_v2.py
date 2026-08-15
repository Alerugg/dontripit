#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, text

from app.scripts.audit_yugioh_multilingual_db_compatibility import (
    TARGETS,
    build_source,
    find_file,
    s,
    sl,
)

TARGET_REGION = {"es": "global", "ja": "jp"}
CARD_ALIAS_TO_CANONICAL = {
    "300302053": "300302018",  # Spell of Mask Skill Card alias already certified by YGO v2.
}
FRESHNESS_MAX_DAYS = 7


def canonical_ygo_id(value: object) -> str:
    raw = s(value)
    return CARD_ALIAS_TO_CANONICAL.get(raw, raw)


def logical_card_identity(card: dict[str, str], fallback: str) -> str:
    konami = s(card.get("konami"))
    ygo = canonical_ygo_id(card.get("ygoprodeck"))
    if konami:
        return f"konami:{konami}"
    if ygo:
        return f"ygoprodeck:{ygo}"
    return f"source:{fallback}"


def source_quarantine(
    cards: dict[str, dict[str, str]], source_rows: dict[str, list[dict[str, str]]]
) -> tuple[dict[str, set[str]], dict[str, dict[str, int]]]:
    all_rows = [row for rows in source_rows.values() for row in rows]
    logical_by_uuid: dict[str, set[tuple[str, str, str]]] = defaultdict(set)
    for row in all_rows:
        puid = s(row.get("print_uuid"))
        if not puid:
            continue
        card = cards.get(s(row.get("card_uuid"))) or {}
        logical_by_uuid[puid].add(
            (
                logical_card_identity(card, s(row.get("card_uuid"))),
                s(row.get("set_uuid")),
                sl(row.get("rarity")) or "unknown",
            )
        )
    logical_conflicts = {puid for puid, values in logical_by_uuid.items() if len(values) > 1}

    quarantines: dict[str, set[str]] = {}
    details: dict[str, dict[str, int]] = {}
    for target, rows in source_rows.items():
        target_ids = {s(r.get("print_uuid")) for r in rows if s(r.get("print_uuid"))}
        missing = {
            s(r.get("print_uuid"))
            for r in rows
            if s(r.get("print_uuid")) and r.get("quality") == "missing"
        }
        placeholder = {
            s(r.get("print_uuid"))
            for r in rows
            if s(r.get("print_uuid")) and r.get("quality") == "placeholder"
        }
        slot_groups: dict[tuple[str, str, str], set[str]] = defaultdict(set)
        slot_pids: dict[tuple[str, str, str], set[str]] = defaultdict(set)
        for row in rows:
            if row.get("quality") != "exact":
                continue
            collector = s(row.get("collector")).upper()
            if not collector:
                continue
            card = cards.get(s(row.get("card_uuid"))) or {}
            key = (
                s(row.get("set_uuid")),
                collector,
                sl(row.get("rarity")) or "unknown",
            )
            slot_groups[key].add(logical_card_identity(card, s(row.get("card_uuid"))))
            if s(row.get("print_uuid")):
                slot_pids[key].add(s(row.get("print_uuid")))
        slot_conflicts = set().union(
            *(slot_pids[key] for key, identities in slot_groups.items() if len(identities) > 1),
            set(),
        )
        logical = logical_conflicts & target_ids
        union = missing | placeholder | logical | slot_conflicts
        quarantines[target] = union
        details[target] = {
            "missing_collector": len(missing),
            "placeholder_collector": len(placeholder),
            "logical_uuid_conflict": len(logical),
            "physical_slot_conflict": len(slot_conflicts),
            "quarantine_union": len(union),
        }
    return quarantines, details


def ygojson_freshness(root: Path) -> dict[str, Any]:
    try:
        meta = json.loads(find_file(root, "meta.json").read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {"snapshot_cutoff": None, "age_days": None, "status": "unknown"}

    candidates: list[datetime] = []
    for key in ("lastYGOProDeckRead", "lastYugipediaRead", "lastYamlyugiRead"):
        raw = s(meta.get(key))
        if not raw:
            continue
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        candidates.append(dt.astimezone(timezone.utc))
    cutoff = max(candidates, default=None)
    if cutoff is None:
        return {"snapshot_cutoff": None, "age_days": None, "status": "unknown"}
    age = max(0, (datetime.now(timezone.utc) - cutoff).days)
    return {
        "snapshot_cutoff": cutoff.isoformat(),
        "age_days": age,
        "status": "current_enough" if age <= FRESHNESS_MAX_DAYS else "historical_snapshot",
        "max_rollout_age_days": FRESHNESS_MAX_DAYS,
    }


def run(root: Path, report_path: Path) -> dict[str, Any]:
    cards, source_rows, _legacy_quarantine = build_source(root)
    quarantines, quarantine_details = source_quarantine(cards, source_rows)
    freshness = ygojson_freshness(root)

    url = os.getenv("DATABASE_URL_UNPOOLED") or os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL_UNPOOLED or DATABASE_URL is required")

    engine = create_engine(url, pool_pre_ping=True)
    with engine.connect() as conn:
        tx = conn.begin()
        conn.execute(text("SET TRANSACTION READ ONLY"))
        ro = sl(conn.execute(text("SHOW transaction_read_only")).scalar_one())
        if ro not in {"on", "true", "1"}:
            raise AssertionError(f"transaction_read_only={ro!r}")

        game_id = conn.execute(text("SELECT id FROM games WHERE slug='yugioh' LIMIT 1")).scalar_one()
        has_region = bool(conn.execute(text("""
            SELECT EXISTS (
              SELECT 1 FROM information_schema.columns
              WHERE table_schema=current_schema() AND table_name='sets' AND column_name='region'
            )
        """)).scalar_one())

        db_cards = {
            str(ext): int(card_id)
            for card_id, ext in conn.execute(text(
                "SELECT id, yugoprodeck_id FROM cards WHERE game_id=:g AND yugoprodeck_id IS NOT NULL"
            ), {"g": game_id})
            if ext is not None
        }

        if has_region:
            set_rows = list(conn.execute(text(
                "SELECT id, code, coalesce(region,'global') FROM sets WHERE game_id=:g"
            ), {"g": game_id}))
        else:
            set_rows = [(sid, code, "global") for sid, code in conn.execute(text(
                "SELECT id, code FROM sets WHERE game_id=:g"
            ), {"g": game_id})]
        db_sets = {(s(code).upper(), sl(region) or "global"): int(sid) for sid, code, region in set_rows if s(code)}

        existing_ygo_ids = {
            str(value)
            for value in conn.execute(text("""
                SELECT p.yugioh_id
                FROM prints p JOIN cards c ON c.id=p.card_id
                WHERE c.game_id=:g AND p.yugioh_id IS NOT NULL
            """), {"g": game_id}).scalars()
        }
        db_print_rows = list(conn.execute(text("""
            SELECT p.id, p.set_id, p.card_id, upper(p.collector_number),
                   coalesce(lower(p.language),''), coalesce(lower(p.rarity),''),
                   p.is_foil, p.variant, p.yugioh_id
            FROM prints p JOIN cards c ON c.id=p.card_id
            WHERE c.game_id=:g
        """), {"g": game_id}))
        tx.rollback()

    # Source-derived exact alias bridge: if several YGOProDeck IDs share one
    # official Konami identity, reuse it only when all DB-backed source aliases
    # converge on exactly one canonical Card row. No names are consulted.
    konami_to_db_cards: dict[str, set[int]] = defaultdict(set)
    for card in cards.values():
        konami_id = s(card.get("konami"))
        ygo_id = canonical_ygo_id(card.get("ygoprodeck"))
        db_card_id = db_cards.get(ygo_id) if ygo_id else None
        if konami_id and db_card_id is not None:
            konami_to_db_cards[konami_id].add(db_card_id)

    db_physical: dict[tuple[int, str, str], set[int]] = defaultdict(set)
    for _pid, set_id, card_id, collector, language, _rarity, _foil, _variant, _yid in db_print_rows:
        db_physical[(int(set_id), s(collector).upper(), sl(language))].add(int(card_id))

    targets: dict[str, Any] = {}
    for target, spec in TARGETS.items():
        region = TARGET_REGION[target]
        quarantine = quarantines[target]
        unique: dict[str, dict[str, str]] = {}
        missing_print_uuid_rows = 0
        for row in source_rows[target]:
            puid = s(row.get("print_uuid"))
            if not puid:
                missing_print_uuid_rows += 1
                continue
            if puid in quarantine:
                continue
            unique.setdefault(puid, row)
        certifiable = list(unique.values())

        counters = Counter()
        missing_cards: list[dict[str, Any]] = []
        collision_samples: list[dict[str, Any]] = []
        uuid_samples: list[str] = []
        missing_set_identity_samples: list[dict[str, Any]] = []
        ambiguous_konami_samples: list[dict[str, Any]] = []
        create_families: set[str] = set()
        existing_families: set[str] = set()
        matched_cards: set[int] = set()
        alias_source_rows = alias_resolved_rows = 0

        for row in certifiable:
            card = cards.get(s(row.get("card_uuid"))) or {}
            raw_ygo = s(card.get("ygoprodeck"))
            canonical_ygo = canonical_ygo_id(raw_ygo)
            konami_id = s(card.get("konami"))
            if raw_ygo in CARD_ALIAS_TO_CANONICAL:
                alias_source_rows += 1

            db_card_id = db_cards.get(canonical_ygo) if canonical_ygo else None
            bridge_mode = "ygoprodeck_exact" if db_card_id is not None else None
            if raw_ygo in CARD_ALIAS_TO_CANONICAL and db_card_id is not None:
                alias_resolved_rows += 1

            if db_card_id is None and konami_id:
                candidates = konami_to_db_cards.get(konami_id, set())
                if len(candidates) == 1:
                    db_card_id = next(iter(candidates))
                    bridge_mode = "konami_exact_alias"
                    counters["konami_alias_bridge_rows"] += 1
                elif len(candidates) > 1:
                    counters["konami_alias_ambiguous_rows"] += 1
                    if len(ambiguous_konami_samples) < 40:
                        ambiguous_konami_samples.append({
                            "print_uuid": row["print_uuid"],
                            "collector": row["collector"],
                            "konami_id": konami_id,
                            "candidate_db_card_ids": sorted(candidates),
                        })

            family = s(row.get("family")).upper()
            set_id = db_sets.get((family, region)) if family else None
            if set_id is None:
                if family:
                    create_families.add(family)
                    counters["set_create_rows"] += 1
                else:
                    counters["set_identity_missing_rows"] += 1
                    if len(missing_set_identity_samples) < 40:
                        missing_set_identity_samples.append({
                            "print_uuid": row["print_uuid"],
                            "collector": row["collector"],
                            "set_uuid": row["set_uuid"],
                            "rarity": row["rarity"],
                            "card_uuid": row["card_uuid"],
                        })
            else:
                existing_families.add(family)
                counters["set_existing_rows"] += 1

            reason = None
            if db_card_id is None:
                reason = "card_not_in_db"
                counters[reason] += 1
                if len(missing_cards) < 40:
                    missing_cards.append({
                        "print_uuid": row["print_uuid"],
                        "collector": row["collector"],
                        "raw_ygo_id": raw_ygo or None,
                        "canonical_ygo_id": canonical_ygo or None,
                        "konami_id": konami_id or None,
                        "konami_db_candidates": sorted(konami_to_db_cards.get(konami_id, set())) if konami_id else [],
                    })
            else:
                counters["card_exact_match"] += 1
                counters[f"card_bridge_{bridge_mode}"] += 1
                matched_cards.add(db_card_id)

            if reason is None and row["print_uuid"] in existing_ygo_ids:
                reason = "uuid_already_used"
                counters[reason] += 1
                if len(uuid_samples) < 40:
                    uuid_samples.append(row["print_uuid"])

            if reason is None and db_card_id is not None and set_id is not None:
                existing_cards = db_physical.get(
                    (set_id, s(row.get("collector")).upper(), spec["language"]), set()
                )
                if existing_cards and existing_cards != {db_card_id}:
                    reason = "physical_tuple_conflict"
                    counters[reason] += 1
                    if len(collision_samples) < 40:
                        collision_samples.append({
                            "print_uuid": row["print_uuid"],
                            "collector": row["collector"],
                            "source_card_id": db_card_id,
                            "existing_card_ids": sorted(existing_cards),
                            "set_id": set_id,
                            "region": region,
                        })

            if reason is None and not family:
                reason = "set_identity_missing"
            if reason is None:
                counters["writer_ready_after_set_projection"] += 1

        total = len(certifiable)
        accounted = (
            counters["writer_ready_after_set_projection"]
            + counters["card_not_in_db"]
            + counters["uuid_already_used"]
            + counters["physical_tuple_conflict"]
            + counters["set_identity_missing_rows"]
        )
        targets[target] = {
            "language": spec["language"],
            "target_region": region,
            "source_memberships": len(source_rows[target]),
            "source_rows_missing_print_uuid": missing_print_uuid_rows,
            "quarantine": quarantine_details[target],
            "certifiable_unique_print_ids": total,
            "card_exact_match_rows": counters["card_exact_match"],
            "card_bridge_ygoprodeck_exact_rows": counters["card_bridge_ygoprodeck_exact"],
            "card_bridge_konami_exact_alias_rows": counters["card_bridge_konami_exact_alias"],
            "card_not_in_db_rows": counters["card_not_in_db"],
            "konami_alias_ambiguous_rows": counters["konami_alias_ambiguous_rows"],
            "unique_db_cards_matched": len(matched_cards),
            "known_alias_source_rows": alias_source_rows,
            "known_alias_resolved_rows": alias_resolved_rows,
            "set_existing_region_rows": counters["set_existing_rows"],
            "set_create_region_rows": counters["set_create_rows"],
            "set_existing_region_families": len(existing_families),
            "set_create_region_families": len(create_families),
            "set_create_region_family_samples": sorted(create_families)[:80],
            "uuid_already_used_rows": counters["uuid_already_used"],
            "physical_tuple_conflict_rows": counters["physical_tuple_conflict"],
            "set_identity_missing_rows": counters["set_identity_missing_rows"],
            "writer_ready_after_set_projection": counters["writer_ready_after_set_projection"],
            "writer_ready_pct": round(100.0 * counters["writer_ready_after_set_projection"] / total, 4) if total else 0.0,
            "accounted_rows": accounted,
            "samples": {
                "missing_cards": missing_cards,
                "ambiguous_konami_aliases": ambiguous_konami_samples,
                "missing_set_identity": missing_set_identity_samples,
                "physical_tuple_conflicts": collision_samples,
                "uuid_collisions": uuid_samples,
            },
        }

    gates = {
        "read_only_enforced": True,
        "all_certifiable_rows_accounted": all(
            t["accounted_rows"] == t["certifiable_unique_print_ids"] for t in targets.values()
        ),
        "no_uuid_reuse": all(t["uuid_already_used_rows"] == 0 for t in targets.values()),
        "no_existing_region_physical_conflicts": all(
            t["physical_tuple_conflict_rows"] == 0 for t in targets.values()
        ),
        "no_ambiguous_konami_aliases": all(
            t["konami_alias_ambiguous_rows"] == 0 for t in targets.values()
        ),
        "all_sets_projectable": all(t["set_identity_missing_rows"] == 0 for t in targets.values()),
        "known_aliases_resolve": all(
            t["known_alias_source_rows"] == t["known_alias_resolved_rows"] for t in targets.values()
        ),
        "writer_ready_nonzero": all(t["writer_ready_after_set_projection"] > 0 for t in targets.values()),
    }
    structural_pass = all(gates.values())
    rollout_freshness_pass = freshness.get("status") == "current_enough"

    report = {
        "mode": "read_only_ygojson_regional_writer_compatibility_v2",
        "production_writes": 0,
        "database_transaction_read_only": True,
        "database_schema": {
            "sets_region_column_present": has_region,
            "regional_schema_migration_required_before_writer": not has_region,
        },
        "database_inventory": {
            "cards_with_yugoprodeck_id": len(db_cards),
            "sets": len(db_sets),
            "prints": len(db_print_rows),
            "prints_with_yugioh_id": len(existing_ygo_ids),
        },
        "identity_policy": {
            "card": "exact YGOJSON YGOPRODeck id -> certified canonical alias -> Card.yugoprodeck_id; if absent, exact official Konami ID may bridge only when source-backed aliases converge on one DB Card; no names/fuzzy match",
            "set": "ES projects to region=global; JA projects to region=jp; missing regional set is a deterministic create, not a fallback to another region",
            "print": "YGOJSON UUID is the historical physical identity; missing/placeholder collectors remain quarantined",
            "prices": "not read, inherited or written by this audit",
        },
        "source_freshness": freshness,
        "targets": targets,
        "gates": gates,
        "structural_pass": structural_pass,
        "rollout_freshness_pass": rollout_freshness_pass,
        "production_rollout_ready": structural_pass and rollout_freshness_pass and has_region,
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return report


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input-dir", type=Path, required=True)
    ap.add_argument("--report", type=Path, required=True)
    args = ap.parse_args()
    run(args.input_dir, args.report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
