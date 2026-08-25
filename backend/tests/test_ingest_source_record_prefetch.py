from __future__ import annotations

from pathlib import Path

from sqlalchemy import event

from app import db
from app.ingest.base import SourceConnector
from app.models import SourceRecord


class BulkChecksumProbeConnector(SourceConnector):
    name = "bulk_checksum_probe"

    def __init__(self, count: int):
        self.count = count

    def load(self, path=None, **kwargs):
        return [
            (
                Path(f"probe-{index}.json"),
                {"index": index},
                f"{index:064x}",
            )
            for index in range(self.count)
        ]

    def upsert(self, session, payload, stats, **kwargs):
        raise AssertionError("prefetched existing payloads must be skipped")


def test_large_incremental_noop_prefetches_source_records_in_bounded_batches(client):
    connector = BulkChecksumProbeConnector(count=1201)
    payloads = connector.load()

    with db.SessionLocal() as session:
        source = connector.ensure_source(session)
        session.add_all(
            SourceRecord(
                source_id=source.id,
                checksum=checksum,
                raw_json=payload,
            )
            for _path, payload, checksum in payloads
        )
        session.commit()

    source_record_prefetch_queries: list[str] = []

    def capture_source_record_prefetch(_conn, _cursor, statement, _parameters, _context, _executemany):
        normalized = statement.lower()
        if "from source_records" in normalized and "checksum in" in normalized:
            source_record_prefetch_queries.append(statement)

    event.listen(db.engine, "before_cursor_execute", capture_source_record_prefetch)
    try:
        with db.SessionLocal() as session:
            stats = connector.run(session, incremental=True)
            session.commit()
    finally:
        event.remove(db.engine, "before_cursor_execute", capture_source_record_prefetch)

    assert stats.files_seen == 1201
    assert stats.files_skipped == 1201
    assert stats.records_inserted == 0
    assert stats.records_updated == 0
    # 1201 checksums at the default 500-row batch size => exactly 3 reads,
    # rather than the historical 1201 SourceRecord round-trips.
    assert len(source_record_prefetch_queries) == 3
