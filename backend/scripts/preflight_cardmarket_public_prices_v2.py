#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from app import db
from app.jobs.cardmarket_prices import build_import_plan, load_price_guide_file


GUIDES = {
    "mtg": "prices_magic.json",
    "pokemon": "prices_pokemon.json",
    "yugioh": "prices_yugioh.json",
    "onepiece": "prices_onepiece.json",
}


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only game-scoped preflight of public Cardmarket Price Guides.")
    parser.add_argument("data_dir")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    database_url = os.getenv("DATABASE_URL_UNPOOLED") or os.getenv("DATABASE_URL")
    if not database_url:
        raise SystemExit("DATABASE_URL_UNPOOLED or DATABASE_URL is required")
    db.init_engine(database_url)

    root = Path(args.data_dir)
    result = {"mode": "dry_run_game_scoped", "feeds": {}, "safe_feeds": []}

    with db.SessionLocal() as session:
        for game, filename in GUIDES.items():
            created_at, rows = load_price_guide_file(root / filename)
            plan = build_import_plan(session, rows, as_of=created_at, game_slug=game)
            summary = plan.summary()
            summary["safe_to_apply"] = (
                summary["mapped_exact"] > 0
                and summary["snapshot_count"] > 0
                and summary["ambiguous"] == 0
                and summary["cross_game_mappings"] == 0
                and summary["duplicate_feed_rows"] == 0
            )
            result["feeds"][game] = summary
            if summary["safe_to_apply"]:
                result["safe_feeds"].append(game)
        session.rollback()

    result["safe_snapshot_count"] = sum(
        result["feeds"][game]["snapshot_count"] for game in result["safe_feeds"]
    )
    rendered = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True)
    Path(args.output).write_text(rendered + "\n", encoding="utf-8")
    print("CARDMARKET_PRICE_PREFLIGHT_V2=" + json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
