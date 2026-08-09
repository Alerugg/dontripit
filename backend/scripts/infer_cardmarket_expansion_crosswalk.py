#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from app import db
from app.jobs.cardmarket_catalog_audit import load_product_list_file
from app.jobs.cardmarket_expansion_crosswalk import derive_expansion_crosswalk


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Infer reviewable Cardmarket Expansion ID -> Don’tRipIt set mappings from "
            "existing exact Cardmarket PrintIdentifier evidence. Read-only."
        )
    )
    parser.add_argument("product_list", help="Official Cardmarket Product List CSV or .gz file")
    parser.add_argument("--game", default="", choices=["", "pokemon", "mtg", "onepiece", "yugioh"], help="Optional game filter")
    parser.add_argument("--min-samples", type=int, default=3, help="Minimum exact mapped products required for a reviewable proposal")
    parser.add_argument("--report", help="Optional full JSON report path")
    parser.add_argument("--crosswalk-out", help="Optional JSON containing only reviewable consensus proposals")
    args = parser.parse_args()

    database_url = os.getenv("DATABASE_URL_UNPOOLED") or os.getenv("DATABASE_URL")
    if not database_url:
        raise SystemExit("DATABASE_URL_UNPOOLED or DATABASE_URL is required")
    db.init_engine(database_url)

    products = load_product_list_file(args.product_list)
    if not products:
        raise SystemExit("No Cardmarket Product List rows parsed; refusing to continue")

    with db.SessionLocal() as session:
        summary, decisions, proposals = derive_expansion_crosswalk(
            session,
            products,
            min_samples=args.min_samples,
            game_filter=args.game,
        )
        session.rollback()

    payload = {
        "summary": summary,
        "decisions": [item.as_dict() for item in decisions],
        "reviewable_crosswalk": proposals,
    }
    rendered = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    print(rendered)

    if args.report:
        Path(args.report).write_text(rendered + "\n", encoding="utf-8")
    if args.crosswalk_out:
        Path(args.crosswalk_out).write_text(
            json.dumps(proposals, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
