from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timezone

from sqlalchemy import select

from app import db
from app.ingest.normalization import normalize_collector_number
from app.models import Card, Game, Print, Set


def run_audit() -> dict:
    db.init_engine()
    with db.SessionLocal() as session:
        game = session.execute(select(Game).where(Game.slug == "onepiece")).scalar_one_or_none()
        if game is None:
            raise RuntimeError("One Piece game is not present in the canonical database")

        rows = session.execute(
            select(
                Card.id,
                Card.name,
                Card.card_key,
                Print.id,
                Print.collector_number,
                Print.variant,
                Set.code,
            )
            .join(Print, Print.card_id == Card.id)
            .join(Set, Set.id == Print.set_id)
            .where(Card.game_id == game.id)
            .order_by(Card.id.asc(), Print.id.asc())
        ).all()

    by_card: dict[int, dict] = {}
    collector_to_cards: defaultdict[str, set[int]] = defaultdict(set)
    collector_to_names: defaultdict[str, set[str]] = defaultdict(set)

    for card_id, name, card_key, print_id, collector_number, variant, set_code in rows:
        collector_norm = normalize_collector_number(collector_number)
        record = by_card.setdefault(
            int(card_id),
            {
                "card_id": int(card_id),
                "name": name,
                "card_key": card_key,
                "collector_numbers": set(),
                "print_ids": set(),
                "variants": set(),
                "set_codes": set(),
            },
        )
        if collector_norm:
            record["collector_numbers"].add(collector_norm)
            collector_to_cards[collector_norm].add(int(card_id))
            collector_to_names[collector_norm].add(str(name))
        record["print_ids"].add(int(print_id))
        record["variants"].add(str(variant or "default"))
        record["set_codes"].add(str(set_code or ""))

    multi_number_cards = []
    for record in by_card.values():
        if len(record["collector_numbers"]) <= 1:
            continue
        multi_number_cards.append(
            {
                "card_id": record["card_id"],
                "name": record["name"],
                "card_key": record["card_key"],
                "collector_number_count": len(record["collector_numbers"]),
                "collector_numbers": sorted(record["collector_numbers"]),
                "print_count": len(record["print_ids"]),
                "variants": sorted(record["variants"]),
                "set_codes": sorted(record["set_codes"]),
            }
        )
    multi_number_cards.sort(
        key=lambda item: (-item["collector_number_count"], -item["print_count"], item["name"])
    )

    collector_numbers_shared_between_cards = [
        {
            "collector_number": collector_number,
            "card_ids": sorted(card_ids),
            "names": sorted(collector_to_names[collector_number]),
        }
        for collector_number, card_ids in collector_to_cards.items()
        if len(card_ids) > 1
    ]
    collector_numbers_shared_between_cards.sort(
        key=lambda item: (-len(item["card_ids"]), item["collector_number"])
    )

    unique_collector_numbers = len(collector_to_cards)
    canonical_cards = len(by_card)
    cards_with_multiple_numbers = len(multi_number_cards)
    prints_on_collapsed_cards = sum(item["print_count"] for item in multi_number_cards)
    extra_collector_definitions_inside_name_cards = sum(
        max(item["collector_number_count"] - 1, 0) for item in multi_number_cards
    )

    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "read_only": True,
        "game": "onepiece",
        "counts": {
            "canonical_cards": canonical_cards,
            "canonical_prints": len(rows),
            "unique_collector_numbers": unique_collector_numbers,
            "cards_with_multiple_collector_numbers": cards_with_multiple_numbers,
            "prints_attached_to_multi_number_cards": prints_on_collapsed_cards,
            "extra_collector_definitions_collapsed_inside_name_cards": extra_collector_definitions_inside_name_cards,
            "collector_numbers_attached_to_multiple_card_rows": len(collector_numbers_shared_between_cards),
            "max_collector_numbers_on_one_card": (
                multi_number_cards[0]["collector_number_count"] if multi_number_cards else 1
            ),
        },
        "top_cards_with_multiple_collector_numbers": multi_number_cards[:50],
        "collector_numbers_attached_to_multiple_card_rows": collector_numbers_shared_between_cards[:50],
        "interpretation": [
            "For One Piece, a base collector number identifies a gameplay card definition more reliably than the visible card name.",
            "A Card row with multiple distinct collector numbers proves that name-based logical card identity is collapsing mechanically different cards.",
            "Parallel/reprint/treatment variants should remain Print-level identities linked to the base collector-number Card identity.",
        ],
    }


def main() -> int:
    payload = run_audit()
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
