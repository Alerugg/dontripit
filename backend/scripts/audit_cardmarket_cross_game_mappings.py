#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from sqlalchemy import select

from app import db
from app.jobs.cardmarket_catalog_audit import load_product_list_file
from app.models import Card, Game, Print, PrintIdentifier, Set


PRODUCT_FILES = {
    "mtg": "products_magic.json",
    "pokemon": "products_pokemon.json",
    "yugioh": "products_yugioh.json",
    "onepiece": "products_onepiece.json",
}


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only audit for Cardmarket idProduct mappings that cross TCG boundaries.")
    parser.add_argument("data_dir")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    database_url = os.getenv("DATABASE_URL_UNPOOLED") or os.getenv("DATABASE_URL")
    if not database_url:
        raise SystemExit("DATABASE_URL_UNPOOLED or DATABASE_URL is required")
    db.init_engine(database_url)

    root = Path(args.data_dir)
    feed_membership: dict[str, list[dict]] = {}
    for feed_game, filename in PRODUCT_FILES.items():
        for product in load_product_list_file(root / filename):
            feed_membership.setdefault(product.product_id, []).append({
                "feed_game": feed_game,
                "product_name": product.name,
                "expansion_id": product.expansion_id,
                "metacard_id": product.metacard_id,
                "category": product.category,
            })

    with db.SessionLocal() as session:
        rows = session.execute(
            select(
                PrintIdentifier.external_id,
                Print.id,
                Card.id,
                Card.name,
                Game.slug,
                Set.code,
                Set.name,
                Print.collector_number,
                Print.variant,
                Print.language,
                Print.is_foil,
            )
            .join(Print, Print.id == PrintIdentifier.print_id)
            .join(Card, Card.id == Print.card_id)
            .join(Game, Game.id == Card.game_id)
            .join(Set, Set.id == Print.set_id)
            .where(PrintIdentifier.source == "cardmarket")
        ).all()
        session.rollback()

    conflicts = []
    missing_from_all_feeds = []
    multi_feed_ids = []
    for external_id, print_id, card_id, card_name, internal_game, set_code, set_name, collector_number, variant, language, is_foil in rows:
        external_id = str(external_id)
        memberships = feed_membership.get(external_id, [])
        feed_games = sorted({item["feed_game"] for item in memberships})
        base = {
            "idProduct": external_id,
            "internal_game": str(internal_game),
            "print_id": int(print_id),
            "card_id": int(card_id),
            "card_name": str(card_name),
            "set_code": str(set_code),
            "set_name": str(set_name),
            "collector_number": str(collector_number),
            "variant": str(variant),
            "language": language,
            "is_foil": bool(is_foil),
            "product_memberships": memberships,
        }
        if not memberships:
            missing_from_all_feeds.append(base)
            continue
        if len(feed_games) > 1:
            multi_feed_ids.append({**base, "feed_games": feed_games})
        if str(internal_game) not in feed_games:
            conflicts.append({**base, "feed_games": feed_games})

    report = {
        "mode": "read_only",
        "mapped_identifiers": len(rows),
        "cross_game_conflicts": len(conflicts),
        "multi_feed_product_ids": len(multi_feed_ids),
        "missing_from_all_current_product_lists": len(missing_from_all_feeds),
        "conflicts": conflicts,
        "multi_feed_ids": multi_feed_ids,
        "missing_samples": missing_from_all_feeds[:50],
    }
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    Path(args.output).write_text(rendered + "\n", encoding="utf-8")
    print("CARDMARKET_CROSS_GAME_AUDIT=" + json.dumps(report, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
