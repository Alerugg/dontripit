#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from app import db
from app.jobs.cardmarket_external_prices import apply_external_price_plan, build_external_price_plan
from app.jobs.cardmarket_prices import load_price_guide_file


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Synchronize a Cardmarket Price Guide into source-level market snapshots. "
            "A single Cardmarket product may safely emit separate nonfoil and foil rows."
        )
    )
    parser.add_argument("price_guide", help="Official Cardmarket Price Guide JSON/CSV")
    parser.add_argument("--game", choices=["pokemon", "mtg", "onepiece", "yugioh"])
    parser.add_argument("--as-of", help="Optional ISO timestamp override; defaults to feed createdAt/current UTC")
    parser.add_argument("--apply", action="store_true", help="Commit the validated source-level price plan")
    parser.add_argument("--report", help="Optional JSON summary output path")
    args = parser.parse_args()

    database_url = os.getenv("DATABASE_URL_UNPOOLED") or os.getenv("DATABASE_URL")
    if not database_url:
        raise SystemExit("DATABASE_URL_UNPOOLED or DATABASE_URL is required")
    db.init_engine(database_url)

    feed_as_of, rows = load_price_guide_file(args.price_guide)
    if not rows:
        raise SystemExit("No Cardmarket Price Guide rows parsed; refusing to continue")

    explicit_as_of = None
    if args.as_of:
        raw = args.as_of.strip().replace("Z", "+00:00")
        explicit_as_of = datetime.fromisoformat(raw)
        if explicit_as_of.tzinfo is None:
            explicit_as_of = explicit_as_of.replace(tzinfo=timezone.utc)
    as_of = explicit_as_of or feed_as_of or datetime.now(timezone.utc)

    with db.SessionLocal() as session:
        plan = build_external_price_plan(session, rows, as_of=as_of, game_slug=args.game)
        result = plan.summary()
        if args.apply:
            result = apply_external_price_plan(session, plan)
            session.commit()
            result["mode"] = "apply"
        else:
            session.rollback()
            result["mode"] = "dry_run"

    rendered = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True)
    print(rendered)
    if args.report:
        Path(args.report).write_text(rendered + "\n", encoding="utf-8")
    return 0 if result.get("write_ready") else 2


if __name__ == "__main__":
    raise SystemExit(main())
