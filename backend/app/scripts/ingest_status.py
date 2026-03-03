from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select

from app import db
from app.models import Card, Game, Print, Set, Source, SourceRecord, SourceSyncState


def get_ingest_status(session) -> dict:
    source_rows = session.execute(
        select(Source.name, SourceSyncState.last_run_at, func.count(SourceRecord.id).label("records"))
        .outerjoin(SourceSyncState, SourceSyncState.source_id == Source.id)
        .outerjoin(SourceRecord, SourceRecord.source_id == Source.id)
        .group_by(Source.id, Source.name, SourceSyncState.last_run_at)
        .order_by(Source.name.asc())
    ).all()

    game_rows = session.execute(select(Game.id, Game.slug).order_by(Game.slug.asc())).all()
    games = []
    for game_id, slug in game_rows:
        cards = session.execute(select(func.count(Card.id)).where(Card.game_id == game_id)).scalar_one()
        sets = session.execute(select(func.count(Set.id)).where(Set.game_id == game_id)).scalar_one()
        prints = session.execute(select(func.count(Print.id)).join(Set, Set.id == Print.set_id).where(Set.game_id == game_id)).scalar_one()
        games.append({"slug": slug, "cards": cards, "sets": sets, "prints": prints})

    return {
        "sources": [
            {
                "name": source_name,
                "last_run_at": last_run_at.isoformat() if last_run_at else None,
                "records": records,
            }
            for source_name, last_run_at, records in source_rows
        ],
        "games": games,
        "now": datetime.now(timezone.utc).isoformat(),
    }


def main() -> int:
    db.init_engine()
    with db.SessionLocal() as session:
        payload = get_ingest_status(session)

        print("=== source_sync_state ===")
        if not payload["sources"]:
            print("(no sync state rows)")
        for source in payload["sources"]:
            print(
                f"source={source['name']} "
                f"last_run_at={source['last_run_at']} "
                f"source_records={source['records']}"
            )

        print("\n=== counts by game ===")
        if not payload["games"]:
            print("(no games)")
        for game in payload["games"]:
            print(f"game={game['slug']} cards={game['cards']} sets={game['sets']} prints={game['prints']}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
