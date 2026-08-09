#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from app import db
from app.jobs.cardmarket_catalog_audit import load_product_list_file
from app.jobs.cardmarket_set_bootstrap import bootstrap_expansion_set_families


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Infer read-only Cardmarket expansion -> Don’tRipIt set/family proposals "
            "from Cardmarket Product List content and the canonical live catalog."
        )
    )
    parser.add_argument("product_list", help="Official Cardmarket singles Product List JSON/CSV/.gz")
    parser.add_argument("--game", required=True, choices=["pokemon", "mtg", "onepiece", "yugioh"])
    parser.add_argument("--report", help="Optional full JSON report output path")
    parser.add_argument("--proposals", help="Optional JSON output containing reviewable proposals only")
    args = parser.parse_args()

    database_url = os.getenv("DATABASE_URL_UNPOOLED") or os.getenv("DATABASE_URL")
    if not database_url:
        raise SystemExit("DATABASE_URL_UNPOOLED or DATABASE_URL is required")
    db.init_engine(database_url)

    products = load_product_list_file(args.product_list)
    if not products:
        raise SystemExit("No Cardmarket Product List rows parsed; refusing to continue")

    with db.SessionLocal() as session:
        summary, decisions, proposals = bootstrap_expansion_set_families(
            session,
            products,
            game_slug=args.game,
        )
        session.rollback()

    payload = {
        "summary": summary,
        "decisions": [item.as_dict() for item in decisions],
        "proposals": proposals,
    }
    rendered = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    print(rendered)
    if args.report:
        Path(args.report).write_text(rendered + "\n", encoding="utf-8")
    if args.proposals:
        Path(args.proposals).write_text(
            json.dumps(proposals, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
