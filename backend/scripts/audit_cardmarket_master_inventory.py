#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import os
from pathlib import Path

from app import db
from app.jobs.cardmarket_catalog_audit import load_expansion_crosswalk
from app.jobs.cardmarket_master_inventory import (
    PRODUCT_GROUPS,
    SUPPORTED_GAMES,
    build_master_inventory,
    load_catalog_feed_file,
)


def _parse_catalog(value: str) -> tuple[str, str, str]:
    parts = value.split(":", 2)
    if len(parts) != 3:
        raise argparse.ArgumentTypeError("catalog must be GAME:GROUP:PATH")
    game, group, path = (part.strip() for part in parts)
    if game not in SUPPORTED_GAMES:
        raise argparse.ArgumentTypeError(f"unsupported game {game!r}")
    if group not in PRODUCT_GROUPS:
        raise argparse.ArgumentTypeError(f"unsupported product group {group!r}")
    if not path:
        raise argparse.ArgumentTypeError("catalog path is required")
    return game, group, path


def _parse_crosswalk(value: str) -> tuple[str, str]:
    parts = value.split(":", 1)
    if len(parts) != 2:
        raise argparse.ArgumentTypeError("crosswalk must be GAME:PATH")
    game, path = (part.strip() for part in parts)
    if game not in SUPPORTED_GAMES:
        raise argparse.ArgumentTypeError(f"unsupported game {game!r}")
    if not path:
        raise argparse.ArgumentTypeError("crosswalk path is required")
    return game, path


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only reverse completeness audit for official Cardmarket product catalogs. "
            "Every accepted Cardmarket idProduct is classified against Don’tRipIt; no database writes occur."
        )
    )
    parser.add_argument(
        "--catalog",
        action="append",
        required=True,
        type=_parse_catalog,
        metavar="GAME:GROUP:PATH",
        help="Official Cardmarket Product List feed. Repeat for single/non_single and games.",
    )
    parser.add_argument(
        "--crosswalk",
        action="append",
        default=[],
        type=_parse_crosswalk,
        metavar="GAME:PATH",
        help="Optional Cardmarket Expansion ID -> Don’tRipIt set crosswalk. Repeat per game.",
    )
    parser.add_argument("--report", help="Optional full JSON report path")
    parser.add_argument("--decisions-csv", help="Optional CSV with one classification row per accepted Cardmarket product")
    parser.add_argument(
        "--require-ready",
        action="store_true",
        help="Exit non-zero unless every supplied game is fully mapped with zero unresolved/conflicts/rejections.",
    )
    args = parser.parse_args()

    database_url = os.getenv("DATABASE_URL_UNPOOLED") or os.getenv("DATABASE_URL")
    if not database_url:
        raise SystemExit("DATABASE_URL_UNPOOLED or DATABASE_URL is required")
    db.init_engine(database_url)

    feeds = [
        load_catalog_feed_file(path, game_slug=game, product_group=group)
        for game, group, path in args.catalog
    ]
    crosswalks: dict[str, dict] = {}
    for game, path in args.crosswalk:
        if game in crosswalks:
            raise SystemExit(f"Duplicate crosswalk supplied for {game}")
        crosswalks[game] = load_expansion_crosswalk(path)

    with db.SessionLocal() as session:
        summary, decisions = build_master_inventory(session, feeds, crosswalks=crosswalks)
        session.rollback()  # explicit guarantee: this tool is evidence-only

    payload = {
        "summary": summary,
        "feeds": [
            {
                "game": feed.game_slug,
                "product_group": feed.product_group,
                "created_at": feed.created_at.isoformat() if feed.created_at else None,
                "raw_records": feed.raw_records,
                "accepted_records": len(feed.rows),
                "rejected_records": feed.rejected_records,
            }
            for feed in feeds
        ],
        "decisions": [item.as_dict() for item in decisions],
    }
    rendered = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    print(rendered)

    if args.report:
        Path(args.report).write_text(rendered + "\n", encoding="utf-8")

    if args.decisions_csv:
        with Path(args.decisions_csv).open("w", newline="", encoding="utf-8") as handle:
            fieldnames = [
                "product_id",
                "game",
                "product_group",
                "name",
                "category",
                "expansion_id",
                "status",
                "entity_type",
                "entity_id",
                "evidence",
            ]
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            for item in decisions:
                row = item.as_dict()
                row["evidence"] = json.dumps(row["evidence"], ensure_ascii=False, sort_keys=True)
                writer.writerow(row)

    if args.require_ready and not summary["ready"]:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
