#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import os
from pathlib import Path

from app import db
from app.jobs.cardmarket_catalog_audit import (
    audit_product_list,
    load_expansion_crosswalk,
    load_product_list_file,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only Cardmarket Product List audit. Finds reviewable exact candidates "
            "but never writes PrintIdentifier rows."
        )
    )
    parser.add_argument("product_list", help="Official Cardmarket Product List CSV or .gz file")
    parser.add_argument("--expansion-map", required=True, help="JSON mapping Cardmarket Expansion ID -> Don’tRipIt set_code (and optional game)")
    parser.add_argument("--game", default="", choices=["", "pokemon", "mtg", "onepiece", "yugioh"], help="Optional supported-game filter")
    parser.add_argument("--report", help="Optional JSON report output path")
    parser.add_argument("--candidates-csv", help="Optional CSV containing review-required exact candidates only")
    args = parser.parse_args()

    database_url = os.getenv("DATABASE_URL_UNPOOLED") or os.getenv("DATABASE_URL")
    if not database_url:
        raise SystemExit("DATABASE_URL_UNPOOLED or DATABASE_URL is required")
    db.init_engine(database_url)

    rows = load_product_list_file(args.product_list)
    if not rows:
        raise SystemExit("No Cardmarket Product List rows parsed; refusing to continue")
    crosswalk = load_expansion_crosswalk(args.expansion_map)

    with db.SessionLocal() as session:
        summary, decisions = audit_product_list(session, rows, crosswalk, game_filter=args.game)
        session.rollback()  # explicit proof: audit never commits or mutates the database

    payload = {
        "summary": summary,
        "decisions": [item.as_dict() for item in decisions],
    }
    rendered = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    print(rendered)

    if args.report:
        Path(args.report).write_text(rendered + "\n", encoding="utf-8")

    if args.candidates_csv:
        candidates = [item for item in decisions if item.status == "exact_candidate_review_required"]
        with Path(args.candidates_csv).open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=[
                "product_id", "game", "expansion_id", "set_code", "name", "card_id", "print_id", "evidence",
            ])
            writer.writeheader()
            for item in candidates:
                writer.writerow({
                    "product_id": item.product_id,
                    "game": item.game,
                    "expansion_id": item.expansion_id,
                    "set_code": item.set_code,
                    "name": item.name,
                    "card_id": item.card_id,
                    "print_id": item.print_id,
                    "evidence": json.dumps(item.evidence or {}, ensure_ascii=False, sort_keys=True),
                })

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
