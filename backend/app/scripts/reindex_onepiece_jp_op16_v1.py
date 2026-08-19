from __future__ import annotations

import re

from sqlalchemy import select

from app import db
from app.models import Card, Game, Print, Set
from app.scripts.reindex_search import rebuild_search_documents

EXPECTED_PHYSICAL = 149
SET_TOKEN = "OP16"


def _norm_set(value: object) -> str:
    return re.sub(r"[^A-Z0-9]+", "", str(value or "").upper())


def main() -> int:
    db.init_engine()
    with db.SessionLocal() as session:
        game = session.execute(select(Game).where(Game.slug == "onepiece")).scalar_one()
        sets = session.execute(select(Set).where(Set.game_id == game.id)).scalars().all()
        matches = [row for row in sets if _norm_set(row.code) == SET_TOKEN]
        if len(matches) != 1:
            raise RuntimeError({"OP16_set_identity_not_unique": [row.id for row in matches]})
        set_row = matches[0]
        print_ids = set(
            session.execute(
                select(Print.id)
                .join(Card, Card.id == Print.card_id)
                .where(
                    Print.set_id == set_row.id,
                    Print.language == "ja",
                    Card.game_id == game.id,
                )
            ).scalars().all()
        )
        if len(print_ids) != EXPECTED_PHYSICAL:
            raise RuntimeError({"OP16_JA_print_count_drift": len(print_ids)})
        stats = rebuild_search_documents(session, print_ids=print_ids)
        if stats["prints"] != EXPECTED_PHYSICAL:
            raise RuntimeError({"OP16_JA_search_doc_reindex_drift": stats})
        session.commit()
    print(f"OP16-JP targeted reindex complete prints={EXPECTED_PHYSICAL}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
