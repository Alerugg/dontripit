#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import os
from pathlib import Path

from app import db
from app.jobs.missing_image_report import build_missing_image_report


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate the complete canonical and current-market missing verified image worklist."
    )
    parser.add_argument("--json", dest="json_path", help="JSON report path")
    parser.add_argument("--csv", dest="csv_path", help="CSV worklist path")
    args = parser.parse_args()

    database_url = os.getenv("DATABASE_URL_UNPOOLED") or os.getenv("DATABASE_URL")
    if not database_url:
        raise SystemExit("DATABASE_URL_UNPOOLED or DATABASE_URL is required")
    db.init_engine(database_url)

    with db.SessionLocal() as session:
        report = build_missing_image_report(session)
        payload = {**report.summary(), "items": list(report.rows)}

    rendered = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str)
    print(json.dumps(report.summary(), ensure_ascii=False, indent=2, sort_keys=True))
    if args.json_path:
        Path(args.json_path).write_text(rendered + "\n", encoding="utf-8")
    if args.csv_path:
        csv_path = Path(args.csv_path)
        fieldnames = sorted({key for row in report.rows for key in row})
        with csv_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(report.rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
