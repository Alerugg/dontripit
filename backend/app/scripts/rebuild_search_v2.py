from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy import func, select

from app import db
from app.models import Game
from app.search_v2.indexer import rebuild_onepiece_search_v2
from app.search_v2.onepiece_source import load_onepiece_search_attributes
from app.search_v2_models import CardSearchProfile, FacetDefinition, PrintSearchProfile


def run(*, game_slug: str = "onepiece") -> dict:
    if game_slug != "onepiece":
        raise ValueError(f"Search V2 rebuild not implemented for game: {game_slug}")

    started_at = datetime.now(timezone.utc)
    # Network enrichment is completed before opening a database transaction.
    source_attributes = load_onepiece_search_attributes()

    db.init_engine()
    with db.SessionLocal() as session:
        stats = rebuild_onepiece_search_v2(session, source_attributes=source_attributes)
        session.commit()

    with db.SessionLocal() as session:
        game = session.execute(select(Game).where(Game.slug == game_slug)).scalar_one()
        verified = {
            "card_profiles": int(
                session.execute(
                    select(func.count(CardSearchProfile.id)).where(CardSearchProfile.game_id == game.id)
                ).scalar_one()
                or 0
            ),
            "print_profiles": int(
                session.execute(
                    select(func.count(PrintSearchProfile.id)).where(PrintSearchProfile.game_id == game.id)
                ).scalar_one()
                or 0
            ),
            "facets": int(
                session.execute(
                    select(func.count(FacetDefinition.id)).where(
                        FacetDefinition.game_id == game.id, FacetDefinition.active.is_(True)
                    )
                ).scalar_one()
                or 0
            ),
        }

    if verified["card_profiles"] != 2665 or verified["print_profiles"] != 4672:
        raise RuntimeError(f"Search V2 verification failed: {verified}")
    if verified["facets"] < 20:
        raise RuntimeError(f"Search V2 facet coverage unexpectedly low: {verified}")

    return {
        "game": game_slug,
        "started_at": started_at.isoformat(),
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "stats": stats,
        "verified": verified,
    }


def main() -> int:
    payload = run()
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
