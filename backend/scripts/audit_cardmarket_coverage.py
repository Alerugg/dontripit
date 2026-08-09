#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from app import db
from app.jobs.cardmarket_coverage import build_cardmarket_coverage
from app.jobs.cardmarket_prices import load_price_guide_file


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Read-only Cardmarket mapping/price coverage report by game and set."
    )
    parser.add_argument("--price-guide", help="Optional official Cardmarket Price Guide JSON/CSV to measure price-ready coverage")
    parser.add_argument("--report", help="Optional JSON output path")
    args = parser.parse_args()

    database_url = os.getenv("DATABASE_URL_UNPOOLED") or os.getenv("DATABASE_URL")
    if not database_url:
        raise SystemExit("DATABASE_URL_UNPOOLED or DATABASE_URL is required")
    db.init_engine(database_url)

    price_rows = None
    if args.price_guide:
        _created_at, price_rows = load_price_guide_file(args.price_guide)
        if not price_rows:
            raise SystemExit("No Cardmarket Price Guide rows parsed; refusing to continue")

    with db.SessionLocal() as session:
        report = build_cardmarket_coverage(session, price_rows)
        session.rollback()

    rendered = json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False)
    print(rendered)
    if args.report:
        Path(args.report).write_text(rendered + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
