from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select

from app import db
from app.ingest.registry import get_connector
from app.models import IngestRun, Source, SourceRecord, SourceSyncState


def _ensure_source(session, name: str) -> Source:
    source = session.execute(select(Source).where(Source.name == name)).scalar_one_or_none()
    if source is None:
        source = Source(name=name, description=f"Connector {name}")
        session.add(source)
        session.flush()
    return source


def _get_sync_state(session, source_id: int) -> SourceSyncState:
    state = session.execute(select(SourceSyncState).where(SourceSyncState.source_id == source_id)).scalar_one_or_none()
    if state is None:
        state = SourceSyncState(source_id=source_id, cursor_json={})
        session.add(state)
        session.flush()
    return state


def run_ingest(
    connector_name: str,
    path: str | None = None,
    *,
    set_code: str | None = None,
    limit: int | None = None,
    fixture: bool = False,
    incremental: bool = True,
) -> dict:
    connector = get_connector(connector_name)
    db.init_engine()

    aggregate = {
        "files_total": 0,
        "files_skipped": 0,
        "games": 0,
        "sets": 0,
        "cards": 0,
        "prints": 0,
        "images": 0,
        "identifiers": 0,
        "updates": 0,
        "conflicts": 0,
    }

    with db.SessionLocal() as session:
        source = _ensure_source(session, connector.name)
        state = _get_sync_state(session, source.id)

        ingest_run = IngestRun(source_id=source.id, status="running", counts_json={})
        session.add(ingest_run)
        session.flush()

        try:
            last_run_at = state.last_run_at if (incremental and connector.supports_incremental()) else None
            items = connector.load(
                Path(path) if path else None,
                set_code=set_code,
                limit=limit,
                fixture=fixture,
                last_run_at=last_run_at,
            )
            for file_path, payload in items:
                aggregate["files_total"] += 1
                raw_bytes = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
                checksum = hashlib.sha256(raw_bytes).hexdigest()

                existing_record = session.execute(
                    select(SourceRecord).where(SourceRecord.source_id == source.id, SourceRecord.checksum == checksum)
                ).scalar_one_or_none()
                if existing_record:
                    aggregate["files_skipped"] += 1
                    continue

                session.add(SourceRecord(source_id=source.id, checksum=checksum, raw_json=payload))
                normalized = connector.normalize(payload, set_code=set_code, limit=limit, fixture=fixture)
                stats = connector.upsert(session, normalized, source_name=source.name)
                for key in ("games", "sets", "cards", "prints", "images", "identifiers", "updates", "conflicts"):
                    aggregate[key] += stats.get(key, 0)

            now = datetime.now(timezone.utc)
            state.last_run_at = now
            state.cursor_json = {
                "set_code": set_code,
                "limit": limit,
                "fixture": fixture,
            }
            ingest_run.status = "success"
            ingest_run.finished_at = now
            ingest_run.counts_json = aggregate
            ingest_run.error_summary = None
            session.commit()
        except Exception as exc:
            ingest_run.status = "fail"
            ingest_run.finished_at = datetime.now(timezone.utc)
            ingest_run.counts_json = aggregate
            ingest_run.error_summary = str(exc)
            session.commit()
            raise

    print(f"[ingest] done: {aggregate}")
    return aggregate


def main() -> None:
    parser = argparse.ArgumentParser(description="Run ingest connector")
    parser.add_argument("connector", help="connector name")
    parser.add_argument("--path", help="Path to fixture file/directory")
    parser.add_argument("--set", dest="set_code", help="Set code filter, e.g. woe")
    parser.add_argument("--limit", type=int, help="Max cards per set in this run")
    parser.add_argument("--fixture", action="store_true", help="Use local fixture files (no internet)")
    parser.add_argument("--no-incremental", action="store_true", help="Disable incremental mode")
    args = parser.parse_args()

    run_ingest(
        args.connector,
        args.path,
        set_code=args.set_code,
        limit=args.limit,
        fixture=args.fixture,
        incremental=not args.no_incremental,
    )


if __name__ == "__main__":
    main()
