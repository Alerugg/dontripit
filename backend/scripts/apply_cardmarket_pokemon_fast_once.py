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
EXPECTED_DATE = "2026-08-09"
MIN_SAFE_SNAPSHOTS = 5900
MAX_SAFE_SNAPSHOTS = 6100


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
    parser = argparse.ArgumentParser(description="Fast guarded one-time Pokémon Cardmarket import using batch upsert.")
    parser.add_argument("price_guide")
    parser.add_argument("--report", required=True)
    args = parser.parse_args()

    database_url = os.getenv("DATABASE_URL_UNPOOLED") or os.getenv("DATABASE_URL")
    if not database_url:
        raise SystemExit("DATABASE_URL_UNPOOLED or DATABASE_URL is required")
    db.init_engine(database_url)

    created_at, rows = load_price_guide_file(Path(args.price_guide))
    if created_at is None:
        raise SystemExit("REFUSED: missing Cardmarket createdAt")
    if created_at.date().isoformat() != EXPECTED_DATE:
        raise SystemExit(f"REFUSED: expected {EXPECTED_DATE}, got {created_at.isoformat()}")

    with db.SessionLocal() as session:
        plan = build_import_plan(session, rows, as_of=created_at, game_slug=EXPECTED_GAME)
        blockers = []
        if plan.ambiguous:
            blockers.append(f"ambiguous={plan.ambiguous}")
        if plan.cross_game_mappings:
            blockers.append(f"cross_game={plan.cross_game_mappings}")
        if plan.duplicate_feed_rows:
            blockers.append(f"duplicate_feed_rows={plan.duplicate_feed_rows}")
        if not (MIN_SAFE_SNAPSHOTS <= len(plan.snapshots) <= MAX_SAFE_SNAPSHOTS):
            blockers.append(f"snapshot_count={len(plan.snapshots)} outside {MIN_SAFE_SNAPSHOTS}-{MAX_SAFE_SNAPSHOTS}")
        if blockers:
            session.rollback()
            raise SystemExit("REFUSED: " + ", ".join(blockers))

        source = session.execute(select(PriceSource).where(PriceSource.name == "cardmarket")).scalar_one_or_none()
        existing_capture = _capture_count(session, source.id, created_at) if source is not None else 0
        sample_print_ids = [payload["entity_id"] for payload in plan.snapshots[:10]]

        if existing_capture == len(plan.snapshots):
            session.rollback()
            report = {
                "mode": "already_applied",
                "game": EXPECTED_GAME,
                "feed_created_at": created_at.isoformat(),
                "preflight": plan.summary(),
                "existing_capture_snapshots": existing_capture,
                "sample_print_ids": sample_print_ids,
                "verified": True,
            }
        else:
            if existing_capture not in {0, len(plan.snapshots)}:
                session.rollback()
                raise SystemExit(
                    f"REFUSED: partial capture already exists ({existing_capture}/{len(plan.snapshots)}); manual audit required"
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
                "game": EXPECTED_GAME,
                "feed_created_at": created_at.isoformat(),
                "preflight": plan.summary(),
                "apply": apply_result,
                "existing_capture_snapshots_before": existing_capture,
                "after_capture_snapshots": after_capture,
                "total_cardmarket_snapshots": total,
                "sample_print_ids": sample_print_ids,
                "verified": True,
            }

    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    Path(args.report).write_text(rendered + "\n", encoding="utf-8")
    print("CARDMARKET_POKEMON_FAST_APPLY=" + json.dumps(report, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
