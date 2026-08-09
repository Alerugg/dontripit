#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import os
from pathlib import Path

from app import db
from app.jobs.cardmarket_catalog_audit import audit_product_list, load_product_list_file
from app.jobs.cardmarket_coverage import build_cardmarket_coverage
from app.jobs.cardmarket_expansion_crosswalk import derive_expansion_crosswalk
from app.jobs.cardmarket_prices import load_price_guide_file


GAME_FILES = {
    "mtg": ("products_magic.json", "prices_magic.json"),
    "pokemon": ("products_pokemon.json", "prices_pokemon.json"),
    "yugioh": ("products_yugioh.json", "prices_yugioh.json"),
    "onepiece": ("products_onepiece.json", "prices_onepiece.json"),
}

REVIEW_STATUSES = {
    "exact_candidate_review_required",
    "physical_ambiguity",
    "external_id_conflict",
    "print_identifier_conflict",
    "collector_no_match",
    "game_conflict",
}


def _write_json(path: Path, payload) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_candidates(path: Path, decisions) -> None:
    fields = [
        "status", "product_id", "game", "expansion_id", "set_code", "name",
        "card_id", "print_id", "evidence",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for item in decisions:
            if item.status not in REVIEW_STATUSES:
                continue
            writer.writerow({
                "status": item.status,
                "product_id": item.product_id,
                "game": item.game,
                "expansion_id": item.expansion_id,
                "set_code": item.set_code,
                "name": item.name,
                "card_id": item.card_id,
                "print_id": item.print_id,
                "evidence": json.dumps(item.evidence or {}, ensure_ascii=False, sort_keys=True),
            })


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the public Cardmarket datasets against Neon in read-only audit mode.")
    parser.add_argument("data_dir", help="Directory containing the four Product Lists and four Price Guides")
    parser.add_argument("--output", required=True, help="Directory for audit reports")
    parser.add_argument("--min-samples", type=int, default=3)
    args = parser.parse_args()

    database_url = os.getenv("DATABASE_URL_UNPOOLED") or os.getenv("DATABASE_URL")
    if not database_url:
        raise SystemExit("DATABASE_URL_UNPOOLED or DATABASE_URL is required")
    db.init_engine(database_url)

    data_dir = Path(args.data_dir)
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    all_price_rows = []
    summary = {
        "mode": "read_only",
        "minimum_crosswalk_samples": max(1, args.min_samples),
        "games": {},
    }

    with db.SessionLocal() as session:
        for game, (products_name, prices_name) in GAME_FILES.items():
            products_path = data_dir / products_name
            prices_path = data_dir / prices_name
            products = load_product_list_file(products_path)
            price_created_at, price_rows = load_price_guide_file(prices_path)
            all_price_rows.extend(price_rows)

            crosswalk_summary, crosswalk_decisions, proposals = derive_expansion_crosswalk(
                session,
                products,
                min_samples=args.min_samples,
                game_filter=game,
            )
            mapping_summary, mapping_decisions = audit_product_list(
                session,
                products,
                proposals,
                game_filter=game,
            )

            review_counts = {}
            for item in mapping_decisions:
                if item.status in REVIEW_STATUSES:
                    review_counts[item.status] = review_counts.get(item.status, 0) + 1

            game_summary = {
                "product_rows": len(products),
                "price_rows": len(price_rows),
                "price_created_at": price_created_at.isoformat() if price_created_at else None,
                "crosswalk": crosswalk_summary,
                "mapping_audit": mapping_summary,
                "review_status_counts": review_counts,
                "reviewable_crosswalk_proposals": len(proposals),
            }
            summary["games"][game] = game_summary

            _write_json(output_dir / f"crosswalk_{game}.json", {
                "summary": crosswalk_summary,
                "proposals": proposals,
                "decisions": [item.as_dict() for item in crosswalk_decisions],
            })
            _write_json(output_dir / f"mapping_{game}_summary.json", {
                "summary": mapping_summary,
                "review_status_counts": review_counts,
            })
            _write_candidates(output_dir / f"mapping_{game}_review.csv", mapping_decisions)

        coverage = build_cardmarket_coverage(session, all_price_rows)
        session.rollback()  # mandatory: this audit never mutates Neon

    summary["coverage"] = {
        "summary": coverage["summary"],
        "games": coverage["games"],
        "priority_sets": coverage["priority_sets"][:25],
    }
    _write_json(output_dir / "coverage.json", coverage)
    _write_json(output_dir / "summary.json", summary)

    # Keep CI logs compact while still exposing the key live result without the artifact.
    print("CARDMARKET_LIVE_SUMMARY=" + json.dumps(summary, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
