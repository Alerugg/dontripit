#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from app import db
from app.jobs.cardmarket_sealed_mapping import apply_sealed_mapping_plan, build_sealed_mapping_plan


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Map only exact and uniquely provable Cardmarket sealed products to existing canonical variants. "
            "Dry-run is the default; unresolved products remain source-owned and visible."
        )
    )
    parser.add_argument("--apply", action="store_true", help="Commit safe exact links")
    parser.add_argument("--report", help="Optional JSON report path")
    parser.add_argument("--sample-limit", type=int, default=100)
    args = parser.parse_args()

    database_url = os.getenv("DATABASE_URL_UNPOOLED") or os.getenv("DATABASE_URL")
    if not database_url:
        raise SystemExit("DATABASE_URL_UNPOOLED or DATABASE_URL is required")
    db.init_engine(database_url)

    with db.SessionLocal() as session:
        plan = build_sealed_mapping_plan(session)
        result = plan.summary()
        if args.apply:
            result = apply_sealed_mapping_plan(session, plan)
            session.commit()
            result["mode"] = "apply"
        else:
            session.rollback()
            result["mode"] = "dry_run"
        result["samples"] = [
            item.as_dict()
            for item in plan.decisions
            if item.status != "already_mapped"
        ][: max(0, args.sample_limit)]

    rendered = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True)
    print(rendered)
    if args.report:
        Path(args.report).write_text(rendered + "\n", encoding="utf-8")
    return 0 if result.get("write_ready") else 2


if __name__ == "__main__":
    raise SystemExit(main())
