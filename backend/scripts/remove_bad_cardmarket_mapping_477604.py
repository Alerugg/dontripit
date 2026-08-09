#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from sqlalchemy import func, select

from app import db
from app.jobs.cardmarket_catalog_audit import load_product_list_file
from app.models import Card, Game, PriceSnapshot, PriceSource, Print, PrintIdentifier, Set


EXPECTED_ID = "477604"
EXPECTED_PRINT_ID = 95468
EXPECTED_CARD = "Copycat"
EXPECTED_INTERNAL_GAME = "pokemon"
EXPECTED_MAGIC_PRODUCT = "Garruk, Unleashed Emblem"


def main() -> int:
    parser = argparse.ArgumentParser(description="Remove one proven cross-game Cardmarket mapping after re-validating live Product Lists.")
    parser.add_argument("--magic-products", required=True)
    parser.add_argument("--pokemon-products", required=True)
    parser.add_argument("--report", required=True)
    args = parser.parse_args()

    magic = {row.product_id: row for row in load_product_list_file(args.magic_products)}
    pokemon = {row.product_id: row for row in load_product_list_file(args.pokemon_products)}

    magic_product = magic.get(EXPECTED_ID)
    pokemon_product = pokemon.get(EXPECTED_ID)
    if magic_product is None:
        raise SystemExit("REFUSED: idProduct 477604 is no longer present in the Magic Product List")
    if magic_product.name != EXPECTED_MAGIC_PRODUCT:
        raise SystemExit(f"REFUSED: Magic idProduct 477604 changed identity to {magic_product.name!r}")
    if pokemon_product is not None:
        raise SystemExit(f"REFUSED: idProduct 477604 unexpectedly appears in Pokémon as {pokemon_product.name!r}")

    database_url = os.getenv("DATABASE_URL_UNPOOLED") or os.getenv("DATABASE_URL")
    if not database_url:
        raise SystemExit("DATABASE_URL_UNPOOLED or DATABASE_URL is required")
    db.init_engine(database_url)

    with db.SessionLocal() as session:
        rows = session.execute(
            select(
                PrintIdentifier,
                Print.id,
                Card.name,
                Game.slug,
                Set.code,
                Print.collector_number,
            )
            .join(Print, Print.id == PrintIdentifier.print_id)
            .join(Card, Card.id == Print.card_id)
            .join(Game, Game.id == Card.game_id)
            .join(Set, Set.id == Print.set_id)
            .where(
                PrintIdentifier.source == "cardmarket",
                PrintIdentifier.external_id == EXPECTED_ID,
            )
        ).all()

        if len(rows) != 1:
            session.rollback()
            raise SystemExit(f"REFUSED: expected exactly one internal mapping for 477604, found {len(rows)}")

        identifier, print_id, card_name, internal_game, set_code, collector_number = rows[0]
        if int(print_id) != EXPECTED_PRINT_ID or card_name != EXPECTED_CARD or internal_game != EXPECTED_INTERNAL_GAME:
            session.rollback()
            raise SystemExit(
                "REFUSED: internal mapping changed; "
                f"print={print_id} card={card_name!r} game={internal_game!r}"
            )

        source = session.execute(select(PriceSource).where(PriceSource.name == "cardmarket")).scalar_one_or_none()
        snapshot_count = 0
        if source is not None:
            snapshot_count = session.execute(
                select(func.count()).select_from(PriceSnapshot).where(
                    PriceSnapshot.entity_type == "print",
                    PriceSnapshot.entity_id == EXPECTED_PRINT_ID,
                    PriceSnapshot.source_id == source.id,
                )
            ).scalar_one()
        if snapshot_count:
            session.rollback()
            raise SystemExit(f"REFUSED: print {EXPECTED_PRINT_ID} already has {snapshot_count} Cardmarket snapshots")

        report = {
            "idProduct": EXPECTED_ID,
            "removed_mapping": {
                "print_id": int(print_id),
                "card_name": card_name,
                "internal_game": internal_game,
                "set_code": set_code,
                "collector_number": collector_number,
            },
            "cardmarket_current_product": {
                "feed_game": "mtg",
                "name": magic_product.name,
                "expansion_id": magic_product.expansion_id,
                "metacard_id": magic_product.metacard_id,
                "category": magic_product.category,
            },
            "preexisting_cardmarket_snapshots": int(snapshot_count),
        }

        session.delete(identifier)
        session.commit()

    with db.SessionLocal() as verify:
        remaining = verify.execute(
            select(func.count()).select_from(PrintIdentifier).where(
                PrintIdentifier.source == "cardmarket",
                PrintIdentifier.external_id == EXPECTED_ID,
            )
        ).scalar_one()
        verify.rollback()
    if remaining != 0:
        raise SystemExit(f"POST-COMMIT VERIFY FAILED: {remaining} mappings remain for 477604")

    report["verified_removed"] = True
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    Path(args.report).write_text(rendered + "\n", encoding="utf-8")
    print("CARDMARKET_MAPPING_FIX=" + json.dumps(report, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
