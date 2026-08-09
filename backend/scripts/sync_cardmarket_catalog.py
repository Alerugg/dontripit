#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os

from app import db
from app.jobs.cardmarket_catalog_ingest import apply_catalog_ingest_plan, build_catalog_ingest_plan
from app.jobs.cardmarket_master_inventory import PRODUCT_GROUPS, SUPPORTED_GAMES, load_catalog_feed_file


def _catalog(value: str) -> tuple[str, str, str]:
    parts = value.split(":", 2)
    if len(parts) != 3:
        raise argparse.ArgumentTypeError("catalog must be GAME:GROUP:PATH")
    game, group, path = (part.strip() for part in parts)
    if game not in SUPPORTED_GAMES:
        raise argparse.ArgumentTypeError(f"unsupported game {game!r}")
    if group not in PRODUCT_GROUPS:
        raise argparse.ArgumentTypeError(f"unsupported group {group!r}")
    if not path:
        raise argparse.ArgumentTypeError("catalog path is required")
    return game, group, path


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Synchronize official Cardmarket Product List feeds into the source-owned external catalog. "
            "Dry-run is the default; --apply is explicit and refuses rejected rows or identity conflicts."
        )
    )
    parser.add_argument(
        "--catalog",
        action="append",
        required=True,
        type=_catalog,
        metavar="GAME:GROUP:PATH",
        help="Repeat for every official Cardmarket singles/non-singles feed to sync.",
    )
    parser.add_argument("--apply", action="store_true", help="Commit the validated upsert plan")
    parser.add_argument("--report", help="Optional JSON summary output path")
    args = parser.parse_args()

    database_url = os.getenv("DATABASE_URL_UNPOOLED") or os.getenv("DATABASE_URL")
    if not database_url:
        raise SystemExit("DATABASE_URL_UNPOOLED or DATABASE_URL is required")
    db.init_engine(database_url)

    feeds = [load_catalog_feed_file(path, game_slug=game, product_group=group) for game, group, path in args.catalog]

    with db.SessionLocal() as session:
        plan = build_catalog_ingest_plan(session, feeds)
        result = plan.summary()
        if args.apply:
            result = apply_catalog_ingest_plan(session, plan)
            session.commit()
            result["mode"] = "apply"
        else:
            session.rollback()
            result["mode"] = "dry_run"
        result["conflict_samples"] = list(plan.conflicts[:20])

    rendered = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True)
    print(rendered)
    if args.report:
        from pathlib import Path
        Path(args.report).write_text(rendered + "\n", encoding="utf-8")
    return 0 if result.get("write_ready") else 2


if __name__ == "__main__":
    raise SystemExit(main())
