#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from sqlalchemy import func, select

from app import db
from app.jobs.cardmarket_catalog_audit import load_product_list_file, normalize_name, split_product_name_hints
from app.models import Card, Game, Print, PrintIdentifier, Set


EXPECTED = {
    "277439": {"expansion_id": "1614", "name": "Mr. Briney's Compassion", "set_code": "P2", "collector": "8", "print_id": 53375, "card_id": 46796, "metacard_id": "213825"},
    "277488": {"expansion_id": "1617", "name": "Bill's Maintenance", "set_code": "P5", "collector": "6", "print_id": 53424, "card_id": 46845, "metacard_id": "264163"},
    "278574": {"expansion_id": "1563", "name": "Charon's Choice", "set_code": "RR", "collector": "RT6", "print_id": 53078, "card_id": 46499, "metacard_id": "216434"},
    "297277": {"expansion_id": "1758", "name": "Potion", "set_code": "TK10A", "collector": "15", "print_id": 63858, "card_id": 57279, "metacard_id": "225654"},
}


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply four manually reviewed exact Pokémon Cardmarket mappings after live re-validation.")
    parser.add_argument("--products", required=True)
    parser.add_argument("--report", required=True)
    args = parser.parse_args()

    products = load_product_list_file(args.products)
    by_product = {row.product_id: row for row in products}

    database_url = os.getenv("DATABASE_URL_UNPOOLED") or os.getenv("DATABASE_URL")
    if not database_url:
        raise SystemExit("DATABASE_URL_UNPOOLED or DATABASE_URL is required")
    db.init_engine(database_url)

    validated = []
    with db.SessionLocal() as session:
        for product_id, expected in EXPECTED.items():
            product = by_product.get(product_id)
            if product is None:
                session.rollback()
                raise SystemExit(f"REFUSED: product {product_id} missing from current Pokémon Product List")
            base_name, _collector_hint = split_product_name_hints(product.name)
            if product.expansion_id != expected["expansion_id"]:
                session.rollback()
                raise SystemExit(f"REFUSED: {product_id} expansion changed to {product.expansion_id}")
            if product.metacard_id != expected["metacard_id"]:
                session.rollback()
                raise SystemExit(f"REFUSED: {product_id} metacard changed to {product.metacard_id}")
            if normalize_name(base_name) != normalize_name(expected["name"]):
                session.rollback()
                raise SystemExit(f"REFUSED: {product_id} product name changed to {product.name!r}")

            same_product_identity = [
                row for row in products
                if row.expansion_id == product.expansion_id
                and normalize_name(split_product_name_hints(row.name)[0]) == normalize_name(base_name)
            ]
            if len(same_product_identity) != 1 or same_product_identity[0].product_id != product_id:
                session.rollback()
                raise SystemExit(f"REFUSED: {product_id} is not unique by expansion + normalized product name")

            internal = session.execute(
                select(
                    Print.id,
                    Card.id,
                    Card.name,
                    Game.slug,
                    Set.code,
                    Print.collector_number,
                    Print.language,
                    Print.is_foil,
                )
                .join(Card, Card.id == Print.card_id)
                .join(Game, Game.id == Card.game_id)
                .join(Set, Set.id == Print.set_id)
                .where(Print.id == expected["print_id"])
            ).one_or_none()
            if internal is None:
                session.rollback()
                raise SystemExit(f"REFUSED: expected Print {expected['print_id']} missing")

            print_id, card_id, card_name, game_slug, set_code, collector, language, is_foil = internal
            checks = {
                "print_id": int(print_id) == expected["print_id"],
                "card_id": int(card_id) == expected["card_id"],
                "name": normalize_name(card_name) == normalize_name(expected["name"]),
                "game": game_slug == "pokemon",
                "set_code": set_code == expected["set_code"],
                "collector": str(collector).casefold() == expected["collector"].casefold(),
                "language": language == "en",
            }
            failed = [key for key, ok in checks.items() if not ok]
            if failed:
                session.rollback()
                raise SystemExit(f"REFUSED: internal identity mismatch for {product_id}: {failed}")

            existing_product = session.execute(
                select(PrintIdentifier.print_id).where(
                    PrintIdentifier.source == "cardmarket",
                    PrintIdentifier.external_id == product_id,
                )
            ).scalars().all()
            existing_print = session.execute(
                select(PrintIdentifier.external_id).where(
                    PrintIdentifier.source == "cardmarket",
                    PrintIdentifier.print_id == expected["print_id"],
                )
            ).scalars().all()
            if existing_product or existing_print:
                session.rollback()
                raise SystemExit(
                    f"REFUSED: mapping no longer empty for {product_id}; product={list(existing_product)} print={list(existing_print)}"
                )

            validated.append({
                "idProduct": product_id,
                "print_id": expected["print_id"],
                "card_id": expected["card_id"],
                "card_name": card_name,
                "set_code": set_code,
                "collector_number": str(collector),
                "is_foil": bool(is_foil),
                "expansion_id": product.expansion_id,
                "metacard_id": product.metacard_id,
            })

        before = int(session.execute(
            select(func.count()).select_from(PrintIdentifier).where(PrintIdentifier.source == "cardmarket")
        ).scalar_one())
        session.add_all([
            PrintIdentifier(print_id=item["print_id"], source="cardmarket", external_id=item["idProduct"])
            for item in validated
        ])
        session.commit()

    with db.SessionLocal() as verify:
        after = int(verify.execute(
            select(func.count()).select_from(PrintIdentifier).where(PrintIdentifier.source == "cardmarket")
        ).scalar_one())
        verified = []
        for item in validated:
            mapped = verify.execute(
                select(PrintIdentifier.print_id).where(
                    PrintIdentifier.source == "cardmarket",
                    PrintIdentifier.external_id == item["idProduct"],
                )
            ).scalars().all()
            if mapped != [item["print_id"]]:
                raise SystemExit(f"POST-COMMIT VERIFY FAILED for {item['idProduct']}: {mapped}")
            verified.append(item["idProduct"])
        verify.rollback()

    if after != before + len(validated):
        raise SystemExit(f"POST-COMMIT COUNT FAILED: before={before} after={after} expected_delta={len(validated)}")

    report = {
        "mode": "apply_exact_reviewed_mappings",
        "before_cardmarket_mappings": before,
        "after_cardmarket_mappings": after,
        "inserted": len(validated),
        "validated": validated,
        "verified_product_ids": verified,
    }
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    Path(args.report).write_text(rendered + "\n", encoding="utf-8")
    print("CARDMARKET_FOUR_MAPPING_APPLY=" + json.dumps(report, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
