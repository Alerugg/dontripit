from datetime import datetime, timezone

from app import db
from app.models import IngestRun, Source, SourceRecord
from app.scripts.ingest_status import get_ingest_status


def test_ingest_status_does_not_multiply_source_records_by_run_count(client):
    with db.SessionLocal() as session:
        source = Source(name="health-source")
        session.add(source)
        session.flush()

        session.add_all(
            [
                SourceRecord(source_id=source.id, checksum="a" * 64, raw_json={"id": 1}),
                SourceRecord(source_id=source.id, checksum="b" * 64, raw_json={"id": 2}),
            ]
        )
        now = datetime.now(timezone.utc)
        session.add_all(
            [
                IngestRun(source_id=source.id, status="success", started_at=now, finished_at=now, counts_json={}),
                IngestRun(source_id=source.id, status="success", started_at=now, finished_at=now, counts_json={}),
                IngestRun(source_id=source.id, status="failed", started_at=now, finished_at=now, counts_json={}),
            ]
        )
        session.commit()

        payload = get_ingest_status(session)

    connector = next(item for item in payload["connectors"] if item["name"] == "health-source")
    assert connector["source_records_total"] == 2
    assert len([run for run in payload["runs"] if run["source"] == "health-source"]) == 3
