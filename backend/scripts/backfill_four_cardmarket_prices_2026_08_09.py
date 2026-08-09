#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from sqlalchemy import select

from app import db
from app.jobs.cardmarket_prices import apply_import_plan, build_import_plan, load_price_guide_file
from app.models import PriceSnapshot, PriceSource


EXPECTED_DATE = "2026-08-09"
EXPECTED_EXISTING = 6006
EXPECTED_TOTAL = 6010
EXPECTED_NEW_PRINT_IDS = {53078, 53375, 53424, 63858}


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill exactly four reviewed Pokémon mappings into the existing 2026-08-09 Cardmarket capture.")
    parser.add_argument("price_guide")
    parser.add_argument("--report", required=True)
    args = parser.parse_args()

    database_url = os.getenv("DATABASE_URL_UNPOOLED") or os.getenv("DATABASE_URL")
    if not database_url:
        raise SystemExit("DATABASE_URL_UNPOOLED or DATABASE_URL is required")
    db.init_engine(database_url)

    created_at, rows = load_price_guide_file(Path(args.price_guide))
    if created_at is None or created_at.date().isoformat() != EXPECTED_DATE:
        raise SystemExit(f"REFUSED: expected Price Guide date {EXPECTED_DATE}, got {created_at}")

    with db.SessionLocal() as session:
        plan = build_import_plan(session, rows, as_of=created_at, game_slug="pokemon")
        if plan.ambiguous or plan.cross_game_mappings or plan.duplicate_feed_rows:
            session.rollback()
            raise SystemExit(
                f"REFUSED: integrity blockers ambiguous={plan.ambiguous} cross_game={plan.cross_game_mappings} duplicates={plan.duplicate_feed_rows}"
            )
        if len(plan.snapshots) != EXPECTED_TOTAL:
            session.rollback()
            raise SystemExit(f"REFUSED: expected {EXPECTED_TOTAL} snapshots after mapping review, got {len(plan.snapshots)}")

        source = session.execute(select(PriceSource).where(PriceSource.name == "cardmarket")).scalar_one_or_none()
        if source is None:
            session.rollback()
            raise SystemExit("REFUSED: Cardmarket source missing")

        existing_ids = set(session.execute(
            select(PriceSnapshot.entity_id).where(
                PriceSnapshot.entity_type == "print",
                PriceSnapshot.source_id == source.id,
                PriceSnapshot.currency == "EUR",
                PriceSnapshot.as_of == created_at,
            )
        ).scalars().all())
        if len(existing_ids) != EXPECTED_EXISTING:
            session.rollback()
            raise SystemExit(f"REFUSED: expected existing capture {EXPECTED_EXISTING}, found {len(existing_ids)}")

        planned_ids = {payload["entity_id"] for payload in plan.snapshots}
        delta = planned_ids - existing_ids
        if delta != EXPECTED_NEW_PRINT_IDS:
            session.rollback()
            raise SystemExit(f"REFUSED: unexpected delta {sorted(delta)}")
        if existing_ids - planned_ids:
            session.rollback()
            raise SystemExit(f"REFUSED: existing capture contains ids absent from new plan: {sorted(existing_ids - planned_ids)[:20]}")

        delta_payloads = {payload["entity_id"]: payload for payload in plan.snapshots if payload["entity_id"] in delta}
        for print_id in EXPECTED_NEW_PRINT_IDS:
            payload = delta_payloads.get(print_id)
            if payload is None:
                session.rollback()
                raise SystemExit(f"REFUSED: missing payload for new Print {print_id}")
            if payload["price_mid"] is None:
                session.rollback()
                raise SystemExit(f"REFUSED: new Print {print_id} lacks conservative price_mid")

        apply_result = apply_import_plan(session, plan)
        session.commit()

    with db.SessionLocal() as verify:
        source = verify.execute(select(PriceSource).where(PriceSource.name == "cardmarket")).scalar_one()
        final_ids = set(verify.execute(
            select(PriceSnapshot.entity_id).where(
                PriceSnapshot.entity_type == "print",
                PriceSnapshot.source_id == source.id,
                PriceSnapshot.currency == "EUR",
                PriceSnapshot.as_of == created_at,
            )
        ).scalars().all())
        verify.rollback()

    if final_ids != planned_ids or len(final_ids) != EXPECTED_TOTAL:
        raise SystemExit(f"POST-COMMIT VERIFY FAILED: final={len(final_ids)} expected={EXPECTED_TOTAL}")

    report = {
        "mode": "extend_existing_capture",
        "feed_created_at": created_at.isoformat(),
        "before": EXPECTED_EXISTING,
        "after": len(final_ids),
        "new_print_ids": sorted(EXPECTED_NEW_PRINT_IDS),
        "new_conservative_prices": {
            str(pid): str(delta_payloads[pid]["price_mid"]) for pid in sorted(EXPECTED_NEW_PRINT_IDS)
        },
        "apply": apply_result,
        "verified": True,
    }
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    Path(args.report).write_text(rendered + "\n", encoding="utf-8")
    print("CARDMARKET_FOUR_PRICE_BACKFILL=" + json.dumps(report, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
