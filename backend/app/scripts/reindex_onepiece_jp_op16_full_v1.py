from __future__ import annotations

import re
from sqlalchemy import select
from app import db
from app.models import Card, Game, Print, Set
from app.scripts.reindex_search import rebuild_search_documents

EXPECTED = 154


def _norm_set(value: object) -> str:
    return re.sub(r"[^A-Z0-9]+", "", str(value or "").upper())


def main() -> int:
    db.init_engine()
    with db.SessionLocal() as session:
        game = session.execute(select(Game).where(Game.slug == "onepiece")).scalar_one()
        sets = session.execute(select(Set).where(Set.game_id == game.id)).scalars().all()
        matches = [row for row in sets if _norm_set(row.code) == "OP16"]
        if len(matches) != 1:
            raise RuntimeError({"OP16_set_matches": len(matches)})
        ids = set(session.execute(select(Print.id).join(Card, Card.id == Print.card_id).where(Print.set_id == matches[0].id, Print.language == "ja", Card.game_id == game.id)).scalars().all())
        if len(ids) != EXPECTED:
            raise RuntimeError({"OP16_JA_prints": len(ids), "expected": EXPECTED})
        stats = rebuild_search_documents(session, print_ids=ids)
        if stats["prints"] != EXPECTED:
            raise RuntimeError({"reindex": stats})
        session.commit()
    print(f"OP16-JP full reindex complete prints={EXPECTED}")
    return 0


if __name__ == "__main__": raise SystemExit(main())
