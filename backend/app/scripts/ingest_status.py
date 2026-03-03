from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select

from app import db
from app.models import Card, Game, IngestRun, Print, Set, Source, SourceRecord, SourceSyncState


def get_ingest_status(session, runs_limit: int = 20) -> dict:
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

    connector_rows = session.execute(
        select(
            Source.name,
            func.count(SourceRecord.id).label("source_records_total"),
            func.max(SourceRecord.ingested_at).label("newest_source_record_at"),
            func.max(IngestRun.started_at).label("newest_ingest_started_at"),
            func.max(IngestRun.finished_at).label("newest_ingest_finished_at"),
        )
        .outerjoin(SourceRecord, SourceRecord.source_id == Source.id)
        .outerjoin(IngestRun, IngestRun.source_id == Source.id)
        .group_by(Source.id, Source.name)
        .order_by(Source.name.asc())
    ).all()

    run_rows = session.execute(
        select(IngestRun, Source.name)
        .join(Source, Source.id == IngestRun.source_id)
        .order_by(IngestRun.started_at.desc())
        .limit(max(runs_limit, 1))
    ).all()

    newest_source_record_at = session.execute(select(func.max(SourceRecord.ingested_at))).scalar_one()
    newest_ingest_started_at = session.execute(select(func.max(IngestRun.started_at))).scalar_one()
    newest_ingest_finished_at = session.execute(select(func.max(IngestRun.finished_at))).scalar_one()

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
        "connectors": [
            {
                "name": source_name,
                "source_records_total": source_records_total,
                "newest_source_record_at": newest_source_record_at.isoformat() if newest_source_record_at else None,
                "newest_ingest_started_at": newest_ingest_started_at.isoformat() if newest_ingest_started_at else None,
                "newest_ingest_finished_at": newest_ingest_finished_at.isoformat() if newest_ingest_finished_at else None,
            }
            for (
                source_name,
                source_records_total,
                newest_source_record_at,
                newest_ingest_started_at,
                newest_ingest_finished_at,
            ) in connector_rows
        ],
        "runs": [
            {
                "id": run.id,
                "source": source_name,
                "status": run.status,
                "started_at": run.started_at.isoformat() if run.started_at else None,
                "finished_at": run.finished_at.isoformat() if run.finished_at else None,
                "counts": run.counts_json,
                "error_summary": run.error_summary,
            }
            for run, source_name in run_rows
        ],
        "newest_timestamps": {
            "source_record_at": newest_source_record_at.isoformat() if newest_source_record_at else None,
            "ingest_started_at": newest_ingest_started_at.isoformat() if newest_ingest_started_at else None,
            "ingest_finished_at": newest_ingest_finished_at.isoformat() if newest_ingest_finished_at else None,
        },
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
