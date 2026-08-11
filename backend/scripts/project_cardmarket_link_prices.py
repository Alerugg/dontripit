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

    rendered = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True)
    print(rendered)
    if args.report:
        Path(args.report).write_text(rendered + "\n", encoding="utf-8")

    # Ambiguous/cross-game identity is a hard failure. Missing prices and
    # unsupported finishes are classifications, not reasons to guess values.
    return 0 if result.get("write_ready") else 2


if __name__ == "__main__":
    raise SystemExit(main())
