#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from sqlalchemy import func, select

from app import db
from app.jobs.cardmarket_prices import apply_import_plan, build_import_plan, load_price_guide_file
from app.models import PriceSnapshot, PriceSource


EXPECTED_GAME = "pokemon"
MIN_SAFE_SNAPSHOTS = 5000
MAX_SAFE_SNAPSHOTS = 7000


def main() -> int:
    parser = argparse.ArgumentParser(description="Guarded one-time apply of the current Pokémon Cardmarket Price Guide.")
    parser.add_argument("price_guide")
    parser.add_argument("--report", required=True)
    args = parser.parse_args()

    database_url = os.getenv("DATABASE_URL_UNPOOLED") or os.getenv("DATABASE_URL")
    if not database_url:
        raise SystemExit("DATABASE_URL_UNPOOLED or DATABASE_URL is required")
    db.init_engine(database_url)

    created_at, rows = load_price_guide_file(Path(args.price_guide))
    if created_at is None:
        raise SystemExit("REFUSED: Cardmarket Price Guide has no createdAt timestamp")
    if created_at.date().isoformat() != "2026-08-09":
        raise SystemExit(f"REFUSED: expected 2026-08-09 Price Guide, got {created_at.isoformat()}")

    with db.SessionLocal() as session:
        plan = build_import_plan(session, rows, as_of=created_at, game_slug=EXPECTED_GAME)
        preflight = plan.summary()
        blockers = []
        if plan.ambiguous:
            blockers.append(f"ambiguous={plan.ambiguous}")
        if plan.cross_game_mappings:
            blockers.append(f"cross_game_mappings={plan.cross_game_mappings}")
        if plan.duplicate_feed_rows:
            blockers.append(f"duplicate_feed_rows={plan.duplicate_feed_rows}")
        if not (MIN_SAFE_SNAPSHOTS <= len(plan.snapshots) <= MAX_SAFE_SNAPSHOTS):
            blockers.append(f"snapshot_count={len(plan.snapshots)} outside {MIN_SAFE_SNAPSHOTS}-{MAX_SAFE_SNAPSHOTS}")
        if blockers:
            session.rollback()
            raise SystemExit("REFUSED: " + ", ".join(blockers))

        source = session.execute(select(PriceSource).where(PriceSource.name == "cardmarket")).scalar_one_or_none()
        before_total = 0
        before_capture = 0
        if source is not None:
            before_total = session.execute(
                select(func.count()).select_from(PriceSnapshot).where(PriceSnapshot.source_id == source.id)
            ).scalar_one()
            before_capture = session.execute(
                select(func.count()).select_from(PriceSnapshot).where(
                    PriceSnapshot.source_id == source.id,
                    PriceSnapshot.as_of == plan.as_of,
                )
            ).scalar_one()

        sample_print_ids = [payload["entity_id"] for payload in plan.snapshots[:10]]
        result = apply_import_plan(session, plan)
        session.commit()

    with db.SessionLocal() as verify:
        source = verify.execute(select(PriceSource).where(PriceSource.name == "cardmarket")).scalar_one()
        after_total = verify.execute(
            select(func.count()).select_from(PriceSnapshot).where(PriceSnapshot.source_id == source.id)
        ).scalar_one()
        after_capture = verify.execute(
            select(func.count()).select_from(PriceSnapshot).where(
                PriceSnapshot.source_id == source.id,
                PriceSnapshot.as_of == created_at,
            )
        ).scalar_one()
        verify.rollback()

    if after_capture != len(plan.snapshots):
        raise SystemExit(f"POST-COMMIT VERIFY FAILED: expected {len(plan.snapshots)} snapshots for capture, found {after_capture}")

    report = {
        "mode": "apply",
        "game": EXPECTED_GAME,
        "feed_created_at": created_at.isoformat(),
        "preflight": preflight,
        "apply": result,
        "before_total_cardmarket_snapshots": int(before_total),
        "before_capture_snapshots": int(before_capture),
        "after_total_cardmarket_snapshots": int(after_total),
        "after_capture_snapshots": int(after_capture),
        "sample_print_ids": sample_print_ids,
        "verified": True,
    }
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    Path(args.report).write_text(rendered + "\n", encoding="utf-8")
    print("CARDMARKET_POKEMON_APPLY=" + json.dumps(report, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
