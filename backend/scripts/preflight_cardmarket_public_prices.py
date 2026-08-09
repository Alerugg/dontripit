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
    parser = argparse.ArgumentParser(description="Read-only preflight of current public Cardmarket Price Guides against exact existing mappings.")
    parser.add_argument("data_dir")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    database_url = os.getenv("DATABASE_URL_UNPOOLED") or os.getenv("DATABASE_URL")
    if not database_url:
        raise SystemExit("DATABASE_URL_UNPOOLED or DATABASE_URL is required")
    db.init_engine(database_url)

    data_dir = Path(args.data_dir)
    output_path = Path(args.output)
    result = {"mode": "dry_run", "feeds": {}}
    totals = {
        "mapped_exact": 0,
        "snapshot_count": 0,
        "ambiguous": 0,
        "duplicate_feed_rows": 0,
        "missing_finish_prices": 0,
    }

    with db.SessionLocal() as session:
        for game, filename in GUIDES.items():
            created_at, rows = load_price_guide_file(data_dir / filename)
            plan = build_import_plan(session, rows, as_of=created_at)
            summary = plan.summary()
            result["feeds"][game] = summary
            for key in totals:
                totals[key] += int(summary.get(key, 0) or 0)
        session.rollback()

    result["totals"] = totals
    result["safe_to_apply_existing_mappings"] = (
        totals["mapped_exact"] > 0
        and totals["snapshot_count"] > 0
        and totals["ambiguous"] == 0
        and totals["duplicate_feed_rows"] == 0
    )

    rendered = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True)
    output_path.write_text(rendered + "\n", encoding="utf-8")
    print("CARDMARKET_PRICE_PREFLIGHT=" + json.dumps(result, ensure_ascii=False, separators=(",", ":")))

    if totals["ambiguous"] or totals["duplicate_feed_rows"]:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
