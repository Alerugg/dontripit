#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import func, select

from app import db
from app.jobs.cardmarket_daily_sync import DailySyncPolicy, validate_daily_plan
from app.jobs.cardmarket_prices import apply_import_plan, build_import_plan, load_price_guide_file
from app.models import PriceSnapshot, PriceSource


GAME = "pokemon"


def _capture_count(session, source_id: int, as_of) -> int:
    return int(session.execute(
        select(func.count()).select_from(PriceSnapshot).where(
            PriceSnapshot.entity_type == "print",
            PriceSnapshot.source_id == source_id,
            PriceSnapshot.currency == "EUR",
            PriceSnapshot.as_of == as_of,
        )
    ).scalar_one())


def main() -> int:
    parser = argparse.ArgumentParser(description="Safely sync the current Pokémon Cardmarket Price Guide into exact Print snapshots.")
    parser.add_argument("price_guide")
    parser.add_argument("--report", required=True)
    parser.add_argument("--max-feed-age-hours", type=int, default=36)
    args = parser.parse_args()

    database_url = os.getenv("DATABASE_URL_UNPOOLED") or os.getenv("DATABASE_URL")
    if not database_url:
        raise SystemExit("DATABASE_URL_UNPOOLED or DATABASE_URL is required")
    db.init_engine(database_url)

    created_at, rows = load_price_guide_file(Path(args.price_guide))
    if created_at is None:
        raise SystemExit("REFUSED: Cardmarket Price Guide has no createdAt")

    policy = DailySyncPolicy(max_feed_age_hours=max(1, args.max_feed_age_hours))
    now = datetime.now(timezone.utc)

    with db.SessionLocal() as session:
        plan = build_import_plan(session, rows, as_of=created_at, game_slug=GAME)
        blockers = validate_daily_plan(plan, now=now, policy=policy)
        if blockers:
            session.rollback()
            raise SystemExit("REFUSED: " + ", ".join(blockers))

        source = session.execute(select(PriceSource).where(PriceSource.name == "cardmarket")).scalar_one_or_none()
        existing_capture = _capture_count(session, source.id, created_at) if source is not None else 0

        if existing_capture == len(plan.snapshots):
            session.rollback()
            report = {
                "mode": "already_applied",
                "game": GAME,
                "feed_created_at": created_at.isoformat(),
                "checked_at": now.isoformat(),
                "preflight": plan.summary(),
                "existing_capture_snapshots": existing_capture,
                "verified": True,
            }
        else:
            if existing_capture != 0:
                session.rollback()
                raise SystemExit(
                    f"REFUSED: partial capture exists ({existing_capture}/{len(plan.snapshots)}); manual audit required"
                )

            apply_result = apply_import_plan(session, plan)
            session.commit()

            with db.SessionLocal() as verify:
                source = verify.execute(select(PriceSource).where(PriceSource.name == "cardmarket")).scalar_one()
                after_capture = _capture_count(verify, source.id, created_at)
                total = int(verify.execute(
                    select(func.count()).select_from(PriceSnapshot).where(PriceSnapshot.source_id == source.id)
                ).scalar_one())
                verify.rollback()

            if after_capture != len(plan.snapshots):
                raise SystemExit(f"POST-COMMIT VERIFY FAILED: {after_capture}/{len(plan.snapshots)}")

            report = {
                "mode": "applied",
                "game": GAME,
                "feed_created_at": created_at.isoformat(),
                "checked_at": now.isoformat(),
                "preflight": plan.summary(),
                "apply": apply_result,
                "after_capture_snapshots": after_capture,
                "total_cardmarket_snapshots": total,
                "verified": True,
            }

    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    Path(args.report).write_text(rendered + "\n", encoding="utf-8")
    print("CARDMARKET_POKEMON_DAILY_SYNC=" + json.dumps(report, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
