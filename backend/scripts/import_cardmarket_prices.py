#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from urllib import request

from app import db
from app.jobs.cardmarket_prices import apply_import_plan, build_import_plan, load_price_guide_bytes


def _read_source(value: str) -> tuple[str, bytes]:
    if value.startswith(("https://", "http://")):
        req = request.Request(value, headers={"User-Agent": "Dontripit-Cardmarket-Price-Importer/1.0"})
        with request.urlopen(req, timeout=60) as response:
            content = response.read()
        if not content:
            raise ValueError("Cardmarket price guide download was empty")
        return value.rsplit("/", 1)[-1] or "price-guide.json", content
    path = Path(value)
    content = path.read_bytes()
    if not content:
        raise ValueError(f"Cardmarket price guide {path} is empty")
    return path.name, content


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Preflight and optionally import Cardmarket daily prices using exact PrintIdentifier mappings only."
    )
    parser.add_argument("source", help="Local JSON/CSV file or direct downloadable URL")
    parser.add_argument("--apply", action="store_true", help="Write exact mapped snapshots after preflight. Default is dry-run.")
    args = parser.parse_args()

    database_url = os.getenv("DATABASE_URL_UNPOOLED") or os.getenv("DATABASE_URL")
    if not database_url:
        raise SystemExit("DATABASE_URL_UNPOOLED or DATABASE_URL is required")
    db.init_engine(database_url)

    filename, content = _read_source(args.source)
    created_at, rows = load_price_guide_bytes(content, filename=filename)
    if not rows:
        raise SystemExit("No Cardmarket price rows were parsed; refusing to continue")

    with db.SessionLocal() as session:
        plan = build_import_plan(session, rows, as_of=created_at)
        print(json.dumps({"mode": "apply" if args.apply else "dry-run", **plan.summary()}, indent=2, sort_keys=True))

        if plan.ambiguous:
            print(f"REFUSED: {plan.ambiguous} Cardmarket product IDs map to multiple Prints.", file=sys.stderr)
            return 2
        if plan.duplicate_feed_rows:
            print(f"REFUSED: {plan.duplicate_feed_rows} duplicate Cardmarket product rows in source.", file=sys.stderr)
            return 2
        if not args.apply:
            return 0
        if not plan.snapshots:
            print("REFUSED: no exact mapped snapshots available to write.", file=sys.stderr)
            return 2

        result = apply_import_plan(session, plan)
        session.commit()
        print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
