#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from collections import defaultdict
from pathlib import Path

from sqlalchemy import select

from app import db
from app.jobs.cardmarket_catalog_audit import load_product_list_file, normalize_name, split_product_name_hints
from app.jobs.cardmarket_prices import load_price_guide_file
from app.models import Card, Game, Print, PrintIdentifier, Set


CANDIDATES = {
    "277439": {"expected_set": "P2", "expected_name": "Mr. Briney's Compassion", "expected_collector": "8", "expected_print_id": 53375},
    "277488": {"expected_set": "P5", "expected_name": "Bill's Maintenance", "expected_collector": "6", "expected_print_id": 53424},
    "278574": {"expected_set": "RR", "expected_name": "Charon's Choice", "expected_collector": "RT6", "expected_print_id": 53078},
    "297277": {"expected_set": "TK10A", "expected_name": "Potion", "expected_collector": "15", "expected_print_id": 63858},
}


def compact_price(row):
    if row is None:
        return None
    fields = [
        "avg", "low", "low_ex", "trend", "avg1", "avg7", "avg30",
        "foil_avg", "foil_low", "foil_trend", "foil_avg1", "foil_avg7", "foil_avg30",
    ]
    return {field: str(getattr(row, field)) if getattr(row, field) is not None else None for field in fields}


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only deep audit of selected Pokémon Cardmarket candidate mappings.")
    parser.add_argument("--products", required=True)
    parser.add_argument("--prices", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    products = load_product_list_file(args.products)
    created_at, prices = load_price_guide_file(args.prices)
    by_product = {row.product_id: row for row in products}
    price_by_product = {row.product_id: row for row in prices}

    by_expansion_name = defaultdict(list)
    by_metacard = defaultdict(list)
    for row in products:
        base_name, collector_hint = split_product_name_hints(row.name)
        by_expansion_name[(row.expansion_id, normalize_name(base_name))].append({
            "product_id": row.product_id,
            "name": row.name,
            "collector_hint": collector_hint,
            "metacard_id": row.metacard_id,
            "date_added": row.date_added,
        })
        if row.metacard_id:
            by_metacard[row.metacard_id].append({
                "product_id": row.product_id,
                "name": row.name,
                "expansion_id": row.expansion_id,
                "date_added": row.date_added,
            })

    database_url = os.getenv("DATABASE_URL_UNPOOLED") or os.getenv("DATABASE_URL")
    if not database_url:
        raise SystemExit("DATABASE_URL_UNPOOLED or DATABASE_URL is required")
    db.init_engine(database_url)

    report = {
        "mode": "read_only",
        "price_created_at": created_at.isoformat() if created_at else None,
        "candidates": {},
    }

    with db.SessionLocal() as session:
        for product_id, expected in CANDIDATES.items():
            product = by_product.get(product_id)
            if product is None:
                report["candidates"][product_id] = {"status": "missing_from_product_list"}
                continue

            base_name, collector_hint = split_product_name_hints(product.name)
            internal_rows = session.execute(
                select(
                    Print.id,
                    Card.id,
                    Card.name,
                    Set.code,
                    Set.name,
                    Print.collector_number,
                    Print.language,
                    Print.variant,
                    Print.is_foil,
                )
                .join(Card, Card.id == Print.card_id)
                .join(Set, Set.id == Print.set_id)
                .join(Game, Game.id == Card.game_id)
                .where(
                    Game.slug == "pokemon",
                    Set.code == expected["expected_set"],
                    Card.name == expected["expected_name"],
                )
                .order_by(Print.id)
            ).all()

            exact_internal = [
                row for row in internal_rows
                if str(row.collector_number).casefold() == str(expected["expected_collector"]).casefold()
            ]
            existing_external = session.execute(
                select(PrintIdentifier.external_id, PrintIdentifier.print_id)
                .where(
                    PrintIdentifier.source == "cardmarket",
                    PrintIdentifier.external_id == product_id,
                )
            ).all()
            print_external = session.execute(
                select(PrintIdentifier.external_id)
                .where(
                    PrintIdentifier.source == "cardmarket",
                    PrintIdentifier.print_id == expected["expected_print_id"],
                )
            ).scalars().all()

            siblings = by_expansion_name[(product.expansion_id, normalize_name(base_name))]
            metacard_siblings = by_metacard.get(product.metacard_id, []) if product.metacard_id else []

            expected_print_present = any(int(row.id) == expected["expected_print_id"] for row in exact_internal)
            unique_exact_internal = len({int(row.id) for row in exact_internal}) == 1
            same_name_collector_products = [
                item for item in siblings
                if (item.get("collector_hint") or "").casefold() == (collector_hint or "").casefold()
            ]

            blockers = []
            if normalize_name(base_name) != normalize_name(expected["expected_name"]):
                blockers.append("product_name_mismatch")
            if not expected_print_present:
                blockers.append("expected_print_not_found")
            if not unique_exact_internal:
                blockers.append(f"internal_physical_candidates={len(exact_internal)}")
            if existing_external:
                blockers.append("product_already_mapped")
            if print_external:
                blockers.append("target_print_already_has_cardmarket_id")
            if len(same_name_collector_products) != 1:
                blockers.append(f"same_name_collector_products={len(same_name_collector_products)}")

            report["candidates"][product_id] = {
                "status": "reviewable_exact" if not blockers else "blocked",
                "blockers": blockers,
                "product": {
                    "idProduct": product.product_id,
                    "name": product.name,
                    "base_name": base_name,
                    "collector_hint": collector_hint,
                    "expansion_id": product.expansion_id,
                    "metacard_id": product.metacard_id,
                    "date_added": product.date_added,
                    "category": product.category,
                },
                "price": compact_price(price_by_product.get(product_id)),
                "expected": expected,
                "internal_same_name_in_set": [
                    {
                        "print_id": int(row.id),
                        "card_id": int(row[1]),
                        "name": row[2],
                        "set_code": row[3],
                        "set_name": row[4],
                        "collector_number": row[5],
                        "language": row[6],
                        "variant": row[7],
                        "is_foil": bool(row[8]),
                    }
                    for row in internal_rows
                ],
                "same_expansion_name_products": siblings,
                "same_name_collector_products": same_name_collector_products,
                "same_metacard_products": metacard_siblings,
                "existing_product_mapping": [
                    {"external_id": ext, "print_id": int(pid)} for ext, pid in existing_external
                ],
                "existing_target_print_external_ids": list(print_external),
            }
        session.rollback()

    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True, default=str)
    Path(args.output).write_text(rendered + "\n", encoding="utf-8")
    print("CARDMARKET_POKEMON_CANDIDATES=" + json.dumps(report, ensure_ascii=False, separators=(",", ":"), default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
