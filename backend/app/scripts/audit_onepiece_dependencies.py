from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy import func, select

from app import db
from app.models import (
    Card,
    FieldProvenance,
    Game,
    Price,
    PriceDailyOHLC,
    PriceSnapshot,
    Print,
    Product,
    SearchDocument,
    Set,
)


def _count(session, statement) -> int:
    return int(session.execute(statement).scalar_one() or 0)


def run_audit() -> dict:
    db.init_engine()
    with db.SessionLocal() as session:
        game = session.execute(select(Game).where(Game.slug == "onepiece")).scalar_one_or_none()
        if game is None:
            return {"game_present": False, "safe_for_rebuild": True}

        print_ids = select(Print.id).join(Set, Set.id == Print.set_id).where(Set.game_id == game.id)
        card_ids = select(Card.id).where(Card.game_id == game.id)
        set_ids = select(Set.id).where(Set.game_id == game.id)

        counts = {
            "cards": _count(session, select(func.count(Card.id)).where(Card.game_id == game.id)),
            "sets": _count(session, select(func.count(Set.id)).where(Set.game_id == game.id)),
            "prints": _count(
                session,
                select(func.count(Print.id)).join(Set, Set.id == Print.set_id).where(Set.game_id == game.id),
            ),
            "prices_by_game": _count(session, select(func.count(Price.id)).where(Price.game_id == game.id)),
            "prices_by_print": _count(session, select(func.count(Price.id)).where(Price.print_id.in_(print_ids))),
            "prices_by_card": _count(session, select(func.count(Price.id)).where(Price.card_id.in_(card_ids))),
            "price_snapshots_print": _count(
                session,
                select(func.count(PriceSnapshot.id)).where(
                    PriceSnapshot.entity_type == "print",
                    PriceSnapshot.entity_id.in_(print_ids),
                ),
            ),
            "price_snapshots_card": _count(
                session,
                select(func.count(PriceSnapshot.id)).where(
                    PriceSnapshot.entity_type == "card",
                    PriceSnapshot.entity_id.in_(card_ids),
                ),
            ),
            "price_daily_ohlc_print": _count(
                session,
                select(func.count(PriceDailyOHLC.id)).where(
                    PriceDailyOHLC.entity_type == "print",
                    PriceDailyOHLC.entity_id.in_(print_ids),
                ),
            ),
            "price_daily_ohlc_card": _count(
                session,
                select(func.count(PriceDailyOHLC.id)).where(
                    PriceDailyOHLC.entity_type == "card",
                    PriceDailyOHLC.entity_id.in_(card_ids),
                ),
            ),
            "products": _count(session, select(func.count(Product.id)).where(Product.game_id == game.id)),
            "products_linked_to_sets": _count(session, select(func.count(Product.id)).where(Product.set_id.in_(set_ids))),
            "field_provenance_cards": _count(
                session,
                select(func.count(FieldProvenance.id)).where(
                    FieldProvenance.entity_type == "card",
                    FieldProvenance.entity_id.in_(card_ids),
                ),
            ),
            "field_provenance_prints": _count(
                session,
                select(func.count(FieldProvenance.id)).where(
                    FieldProvenance.entity_type == "print",
                    FieldProvenance.entity_id.in_(print_ids),
                ),
            ),
            "search_documents": _count(
                session,
                select(func.count(SearchDocument.id)).where(SearchDocument.game_id == game.id),
            ),
        }

    valuable_keys = [
        "prices_by_game",
        "prices_by_print",
        "prices_by_card",
        "price_snapshots_print",
        "price_snapshots_card",
        "price_daily_ohlc_print",
        "price_daily_ohlc_card",
        "products",
        "products_linked_to_sets",
    ]
    valuable_dependencies = {key: counts[key] for key in valuable_keys if counts[key] > 0}

    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "read_only": True,
        "game_present": True,
        "counts": counts,
        "valuable_dependencies": valuable_dependencies,
        "safe_for_rebuild": not valuable_dependencies,
        "derived_or_rebuildable": {
            "search_documents": counts["search_documents"],
            "field_provenance_cards": counts["field_provenance_cards"],
            "field_provenance_prints": counts["field_provenance_prints"],
        },
    }


def main() -> int:
    payload = run_audit()
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
