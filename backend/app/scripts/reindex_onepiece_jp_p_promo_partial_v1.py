from __future__ import annotations

import re

from sqlalchemy import select

from app import db
from app.models import Card, Game, Print, Set
from app.scripts.reindex_search import rebuild_search_documents

EXPECTED_PRINTS = 225
EXPECTED_CARDS = 121


def norm_set(value: object) -> str:
    return re.sub(r"[^A-Z0-9]+", "", str(value or "").upper())


def main() -> int:
    db.init_engine()
    with db.SessionLocal() as session:
        game = session.execute(select(Game).where(Game.slug == "onepiece")).scalar_one()
        sets = session.execute(select(Set).where(Set.game_id == game.id)).scalars().all()
        matches = [row for row in sets if norm_set(row.code) == "P"]
        if len(matches) != 1:
            raise RuntimeError({"P_set_matches": len(matches)})
        set_row = matches[0]
        rows = session.execute(
            select(Print.id, Print.card_id)
            .join(Card, Card.id == Print.card_id)
            .where(Print.set_id == set_row.id, Print.language == "ja", Card.game_id == game.id)
        ).all()
        print_ids = {int(row.id) for row in rows}
        card_ids = {int(row.card_id) for row in rows}
        if len(print_ids) != EXPECTED_PRINTS or len(card_ids) != EXPECTED_CARDS:
            raise RuntimeError(
                {
                    "P_JA_reindex_surface_drift": {
                        "prints": len(print_ids),
                        "cards": len(card_ids),
                    }
                }
            )
        stats = rebuild_search_documents(session, card_ids=card_ids, print_ids=print_ids)
        if stats["prints"] != EXPECTED_PRINTS or stats["cards"] != EXPECTED_CARDS:
            raise RuntimeError({"P_JA_search_reindex_drift": stats})
        session.commit()
    print(f"P promo JP reindex complete cards={EXPECTED_CARDS} prints={EXPECTED_PRINTS}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

# READ ONLY post-production retrigger; no data or guard changes.
