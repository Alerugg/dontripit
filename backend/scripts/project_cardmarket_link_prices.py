#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from app import db
from app.jobs.cardmarket_link_price_projection import (
    apply_link_price_projection_plan,
    build_link_price_projection_plan,
)


PAUSED_GAMES = {"riftbound"}


def _write_report(result: dict, report_path: str | None) -> None:
    rendered = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True)
    print(rendered)
    if report_path:
        Path(report_path).write_text(rendered + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Project current Cardmarket prices onto canonical Prints through "
            "already-accepted exact ExternalCatalogPrintLink identities."
        )
    )
    parser.add_argument("--game", required=True, choices=["pokemon", "mtg", "onepiece", "yugioh", "riftbound"])
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--report")
    args = parser.parse_args()

    # Riftbound is intentionally excluded from canonical/price refresh until the
    # official Riot production API source is available. Existing generic callers
    # may still pass --game riftbound; treat that as a successful no-op so an old
    # loop cannot accidentally mutate or certify Riftbound data.
    if args.game in PAUSED_GAMES:
        _write_report(
            {
                "game": args.game,
                "mode": "apply" if args.apply else "dry_run",
                "paused": True,
                "skipped": True,
                "reason": "official_source_pending",
                "write_ready": True,
            },
            args.report,
        )
        return 0

    database_url = os.getenv("DATABASE_URL_UNPOOLED") or os.getenv("DATABASE_URL")
    if not database_url:
        raise SystemExit("DATABASE_URL_UNPOOLED or DATABASE_URL is required")
    db.init_engine(database_url)

    with db.SessionLocal() as session:
        plan = build_link_price_projection_plan(session, game_slug=args.game)
        result = plan.summary()
        if args.apply:
            result = apply_link_price_projection_plan(session, plan)
            session.commit()
            result["mode"] = "apply"
        else:
            session.rollback()
            result["mode"] = "dry_run"

    _write_report(result, args.report)

    # Ambiguous/cross-game identity is a hard failure. Missing prices and
    # unsupported finishes are classifications, not reasons to guess values.
    return 0 if result.get("write_ready") else 2


if __name__ == "__main__":
    raise SystemExit(main())
