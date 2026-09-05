from __future__ import annotations

import argparse
import json
import os
from copy import deepcopy
from pathlib import Path

from sqlalchemy import select, text

from app import db
from app.models import Game
from app.scripts.audit_mtg_current_delta_v1 import _build_unbounded_delta
from app.scripts.reconcile_mtg_current_source_v1 import (
    MAX_COLLECTOR_CORRECTIONS,
    MAX_NEW_CARDS,
    MAX_NEW_SETS,
    Plan,
    _economics,
    _production_state,
    apply_plan,
    assert_plan_bounds,
    load_snapshot,
)


CONFIRM_TOKEN = "APPLY_MTG_CURRENT_SOURCE_BATCHED_V1"
MAX_BATCH_WRITES = 500
MAX_BATCHES = 4
MAX_TOTAL_WRITES = 1500


def _delta_write_count(delta: dict) -> int:
    return sum(
        len(delta.get(key) or [])
        for key in ("new_sets", "new_cards", "new_prints", "collector_corrections")
    )


def _validate_delta(delta: dict) -> None:
    forbidden = delta.get("forbidden_mismatches") or []
    if forbidden:
        raise AssertionError(f"MTG current delta contains forbidden mismatches: {forbidden[:10]!r}")

    checks = (
        ("new_sets", len(delta.get("new_sets") or []), MAX_NEW_SETS),
        ("new_cards", len(delta.get("new_cards") or []), MAX_NEW_CARDS),
        (
            "collector_corrections",
            len(delta.get("collector_corrections") or []),
            MAX_COLLECTOR_CORRECTIONS,
        ),
    )
    for label, actual, ceiling in checks:
        if actual > ceiling:
            raise AssertionError(f"MTG batched reconciler ceiling exceeded for {label}: {actual}>{ceiling}")

    total = _delta_write_count(delta)
    if total > MAX_TOTAL_WRITES:
        raise AssertionError(f"MTG batched reconciler total ceiling exceeded: {total}>{MAX_TOTAL_WRITES}")


def _next_batch(delta: dict, *, batch_limit: int = MAX_BATCH_WRITES) -> Plan:
    if batch_limit <= 0 or batch_limit > MAX_BATCH_WRITES:
        raise AssertionError(f"invalid MTG batch limit: {batch_limit}")

    _validate_delta(delta)
    remaining = batch_limit
    selected: dict[str, tuple[str, ...]] = {}
    for key in ("new_sets", "new_cards", "collector_corrections", "new_prints"):
        values = tuple(delta.get(key) or ())
        take = min(len(values), remaining)
        selected[key] = values[:take]
        remaining -= take

    plan = Plan(
        new_sets=selected["new_sets"],
        new_cards=selected["new_cards"],
        new_prints=selected["new_prints"],
        collector_corrections=selected["collector_corrections"],
        forbidden_mismatches=(),
    )
    if plan.write_count > batch_limit:
        raise AssertionError("MTG batch exceeds requested write limit")
    assert_plan_bounds(plan)
    return plan


def _subtract_plan(delta: dict, plan: Plan) -> dict:
    remaining = deepcopy(delta)
    consumed = {
        "new_sets": set(plan.new_sets),
        "new_cards": set(plan.new_cards),
        "new_prints": set(plan.new_prints),
        "collector_corrections": set(plan.collector_corrections),
    }
    for key, values in consumed.items():
        remaining[key] = [value for value in (remaining.get(key) or []) if value not in values]
    return remaining


def project_batches(delta: dict) -> list[Plan]:
    _validate_delta(delta)
    remaining = deepcopy(delta)
    batches: list[Plan] = []
    while _delta_write_count(remaining):
        if len(batches) >= MAX_BATCHES:
            raise AssertionError(
                f"MTG delta needs more than {MAX_BATCHES} batches of {MAX_BATCH_WRITES} writes"
            )
        plan = _next_batch(remaining)
        if plan.write_count <= 0:
            raise AssertionError("MTG batch planner made no progress")
        batches.append(plan)
        remaining = _subtract_plan(remaining, plan)
    return batches


def _delta_for_state(*, source_sets, source_cards, source_prints, prod_sets, prod_cards, prod_prints) -> dict:
    delta = _build_unbounded_delta(
        source_sets=source_sets,
        source_cards=source_cards,
        source_prints=source_prints,
        prod_sets=prod_sets,
        prod_cards=prod_cards,
        prod_prints=prod_prints,
    )
    _validate_delta(delta)
    return delta


def _batch_summary(index: int, plan: Plan) -> dict:
    return {
        "batch": index,
        "writes": plan.write_count,
        "new_sets": len(plan.new_sets),
        "new_cards": len(plan.new_cards),
        "new_prints": len(plan.new_prints),
        "collector_corrections": len(plan.collector_corrections),
        "samples": {
            "new_sets": list(plan.new_sets[:10]),
            "new_cards": list(plan.new_cards[:10]),
            "new_prints": list(plan.new_prints[:10]),
            "collector_corrections": list(plan.collector_corrections[:10]),
        },
    }


def run(*, snapshot_dir: Path, output: Path, apply: bool, confirm: str | None) -> dict:
    source_sets, source_cards, source_prints, manifest = load_snapshot(snapshot_dir)
    db.init_engine()

    with db.SessionLocal() as session:
        game = session.execute(select(Game).where(Game.slug == "mtg")).scalar_one()
        prod_sets, prod_cards, prod_prints = _production_state(session, game.id)
        economics_baseline = _economics(session, game.id)
        initial_delta = _delta_for_state(
            source_sets=source_sets,
            source_cards=source_cards,
            source_prints=source_prints,
            prod_sets=prod_sets,
            prod_cards=prod_cards,
            prod_prints=prod_prints,
        )
        projected = project_batches(initial_delta)
        session.rollback()

    applied_batches: list[dict] = []
    total_production_writes = 0

    if apply:
        if confirm != CONFIRM_TOKEN:
            raise AssertionError("production batched apply requires exact confirmation token")

        for batch_index in range(1, MAX_BATCHES + 1):
            with db.SessionLocal() as session:
                game = session.execute(select(Game).where(Game.slug == "mtg")).scalar_one()
                read_only = str(session.execute(text("SHOW transaction_read_only")).scalar_one()).lower()
                if read_only == "on":
                    raise AssertionError("cannot apply MTG batched reconciliation in a read-only transaction")

                prod_sets, prod_cards, prod_prints = _production_state(session, game.id)
                delta = _delta_for_state(
                    source_sets=source_sets,
                    source_cards=source_cards,
                    source_prints=source_prints,
                    prod_sets=prod_sets,
                    prod_cards=prod_cards,
                    prod_prints=prod_prints,
                )
                if _delta_write_count(delta) == 0:
                    session.rollback()
                    break

                plan = _next_batch(delta)
                economics_before = _economics(session, game.id)
                touched = apply_plan(
                    session,
                    game_id=game.id,
                    plan=plan,
                    source_sets=source_sets,
                    source_cards=source_cards,
                    source_prints=source_prints,
                    prod_sets=prod_sets,
                    prod_cards=prod_cards,
                    prod_prints=prod_prints,
                )
                economics_after = _economics(session, game.id)
                if economics_after != economics_before or economics_after != economics_baseline:
                    session.rollback()
                    raise AssertionError(
                        f"MTG economics changed unexpectedly in batch {batch_index}: "
                        f"baseline={economics_baseline} before={economics_before} after={economics_after}"
                    )
                session.commit()

                total_production_writes += plan.write_count
                applied_batches.append(
                    {
                        **_batch_summary(batch_index, plan),
                        "touched_counts": {key: len(value) for key, value in touched.items()},
                    }
                )
        else:
            raise AssertionError(f"MTG reconciliation exhausted {MAX_BATCHES} batches without proving zero delta")

    with db.SessionLocal() as session:
        game = session.execute(select(Game).where(Game.slug == "mtg")).scalar_one()
        prod_sets, prod_cards, prod_prints = _production_state(session, game.id)
        final_delta = _delta_for_state(
            source_sets=source_sets,
            source_cards=source_cards,
            source_prints=source_prints,
            prod_sets=prod_sets,
            prod_cards=prod_cards,
            prod_prints=prod_prints,
        )
        economics_final = _economics(session, game.id)
        session.rollback()

    if apply and _delta_write_count(final_delta) != 0:
        raise AssertionError(f"MTG batched reconciliation is not idempotent: remaining={_delta_write_count(final_delta)}")
    if economics_final != economics_baseline:
        raise AssertionError(
            f"MTG economics changed across batched reconciliation: baseline={economics_baseline} final={economics_final}"
        )

    report = {
        "status": "pass",
        "mode": "apply" if apply else "dry-run",
        "source": manifest.get("source"),
        "snapshot_schema_version": manifest.get("snapshot_schema_version"),
        "production_writes": total_production_writes,
        "planned_writes": _delta_write_count(initial_delta),
        "planned_batches": [_batch_summary(index, plan) for index, plan in enumerate(projected, start=1)],
        "applied_batches": applied_batches,
        "initial_delta": {
            "new_sets": len(initial_delta.get("new_sets") or []),
            "new_cards": len(initial_delta.get("new_cards") or []),
            "new_prints": len(initial_delta.get("new_prints") or []),
            "collector_corrections": len(initial_delta.get("collector_corrections") or []),
            "forbidden_mismatches": len(initial_delta.get("forbidden_mismatches") or []),
        },
        "final_delta_writes": _delta_write_count(final_delta),
        "safety": {
            "batch_write_ceiling": MAX_BATCH_WRITES,
            "max_batches": MAX_BATCHES,
            "total_write_ceiling": MAX_TOTAL_WRITES,
            "deletes": 0,
            "image_writes": 0,
            "cardmarket_writes": 0,
            "price_writes": 0,
            "historical_or_localized_extra_prints_preserved": True,
            "generic_scryfall_writer_quarantine_relaxed": False,
        },
        "economics_baseline": economics_baseline,
        "economics_final": economics_final,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True, default=str))
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Deterministic bounded-batch MTG current-Scryfall reconciler")
    parser.add_argument("--snapshot-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm", default=os.getenv("MTG_RECONCILE_BATCH_CONFIRM"))
    args = parser.parse_args()
    run(snapshot_dir=args.snapshot_dir, output=args.output, apply=args.apply, confirm=args.confirm)


if __name__ == "__main__":
    main()
