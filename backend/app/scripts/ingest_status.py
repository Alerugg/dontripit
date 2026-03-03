from __future__ import annotations

from sqlalchemy import func, select

from app import db
from app.models import Card, Game, Print, Set, Source, SourceRecord, SourceSyncState


def main() -> int:
    db.init_engine()
    with db.SessionLocal() as session:
        rows = session.execute(
            select(Source.name, SourceSyncState.last_run_at, SourceSyncState.cursor_json)
            .join(SourceSyncState, SourceSyncState.source_id == Source.id)
            .order_by(Source.name.asc())
        ).all()

        print("=== source_sync_state ===")
        if not rows:
            print("(no sync state rows)")
        for source_name, last_run_at, cursor in rows:
            print(f"source={source_name} last_run_at={last_run_at.isoformat() if last_run_at else None} cursor={cursor}")

        print("\n=== counts by game ===")
        game_rows = session.execute(select(Game.id, Game.slug).order_by(Game.slug.asc())).all()
        if not game_rows:
            print("(no games)")
        for game_id, slug in game_rows:
            cards = session.execute(select(func.count(Card.id)).where(Card.game_id == game_id)).scalar_one()
            sets = session.execute(select(func.count(Set.id)).where(Set.game_id == game_id)).scalar_one()
            prints = session.execute(
                select(func.count(Print.id)).join(Set, Set.id == Print.set_id).where(Set.game_id == game_id)
            ).scalar_one()
            print(f"game={slug} cards={cards} sets={sets} prints={prints}")

        print("\n=== source_records by source ===")
        sr_rows = session.execute(
            select(Source.name, func.count(SourceRecord.id))
            .outerjoin(SourceRecord, SourceRecord.source_id == Source.id)
            .group_by(Source.name)
            .order_by(Source.name.asc())
        ).all()
        if not sr_rows:
            print("(no sources)")
        for source_name, count in sr_rows:
            print(f"source={source_name} source_records={count}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
