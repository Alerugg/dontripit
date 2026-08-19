from __future__ import annotations

import re

from sqlalchemy import select

from app import db
from app.models import Card, Game, Print, Set
from app.scripts.reindex_search import rebuild_search_documents

TOKENS = {"EB01", "EB02", "EB03", "EB04", "PRB01", "PRB02"}
EXPECTED = 434


def _norm_set(value: object) -> str:
    return re.sub(r"[^A-Z0-9]+", "", str(value or "").upper())


def main() -> int:
    db.init_engine()
    with db.SessionLocal() as session:
        game = session.execute(select(Game).where(Game.slug == "onepiece")).scalar_one()
        sets = session.execute(select(Set).where(Set.game_id == game.id)).scalars().all()
        selected = [row for row in sets if _norm_set(row.code) in TOKENS]
        if len(selected) != len(TOKENS):
            raise RuntimeError({"selected_set_count": len(selected)})
        set_ids = {row.id for row in selected}
        print_ids = set(
            session.execute(
                select(Print.id)
                .join(Card, Card.id == Print.card_id)
                .where(Print.set_id.in_(set_ids), Print.language == "ja", Card.game_id == game.id)
            ).scalars().all()
        )
        if len(print_ids) != EXPECTED:
            raise RuntimeError({"batch_JA_print_count": len(print_ids), "expected": EXPECTED})
        stats = rebuild_search_documents(session, print_ids=print_ids)
        if stats["prints"] != EXPECTED:
            raise RuntimeError({"search_reindex": stats})
        session.commit()
    print(f"One Piece JP EB/PRB targeted reindex complete prints={EXPECTED}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
