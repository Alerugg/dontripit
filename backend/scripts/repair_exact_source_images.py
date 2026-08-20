#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from app import db
from app.jobs.repair_source_images import repair_exact_source_images


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Repair missing canonical Print images only through exact source identifiers: "
            "TCGdex for Pokemon and Scryfall for MTG. Dry-run is the default."
        )
    )
    parser.add_argument("--apply", action="store_true", help="Commit validated exact-source image rows")
    parser.add_argument("--report", help="Optional JSON report path")
    args = parser.parse_args()

    database_url = os.getenv("DATABASE_URL_UNPOOLED") or os.getenv("DATABASE_URL")
    if not database_url:
        raise SystemExit("DATABASE_URL_UNPOOLED or DATABASE_URL is required")
    db.init_engine(database_url)

    with db.SessionLocal() as session:
        report = repair_exact_source_images(session)
        if args.apply:
            session.commit()
            mode = "apply"
        else:
            session.rollback()
            mode = "dry_run"

    result = {"mode": mode, "games": report}
    rendered = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True)
    print(rendered)
    if args.report:
        Path(args.report).write_text(rendered + "\n", encoding="utf-8")

    failures = sum(int(row.get("request_failures", 0)) for row in report.values())
    if failures:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
