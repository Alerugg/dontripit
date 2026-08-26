from __future__ import annotations

from pathlib import Path

from sqlalchemy import event, func, select

from app import db
import app.ingest.base as ingest_base
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


class ProvenanceOnlyProbeConnector(SourceConnector):
    name = "provenance_only_probe"

    def load(self, path=None, **kwargs):
        return [(Path("provenance.json"), {"value": 1}, "a" * 64)]

    def upsert(self, session, payload, stats, **kwargs):
        return {self.CATALOG_UNCHANGED_RESULT_KEY: True}


class LegacyEmptyResultProbeConnector(SourceConnector):
    name = "legacy_empty_result_probe"

    def load(self, path=None, **kwargs):
        return [(Path("legacy.json"), {"value": 2}, "b" * 64)]

    def upsert(self, session, payload, stats, **kwargs):
        # Existing connectors historically return {} even after possible catalog
        # writes, so this must retain the conservative full-reindex fallback.
        return {}


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


def test_explicit_provenance_only_upsert_persists_source_record_without_reindex(client, monkeypatch):
    connector = ProvenanceOnlyProbeConnector()
    reindex_calls: list[dict] = []

    def capture_reindex(_session, **kwargs):
        reindex_calls.append(kwargs)

    monkeypatch.setattr(ingest_base, "rebuild_search_documents", capture_reindex)

    with db.SessionLocal() as session:
        stats = connector.run(session, incremental=True)
        session.commit()

    with db.SessionLocal() as session:
        source_record_count = session.execute(
            select(func.count(SourceRecord.id))
        ).scalar_one()

    assert stats.files_seen == 1
    assert stats.files_skipped == 0
    assert source_record_count == 1
    assert reindex_calls == []


def test_legacy_empty_upsert_result_keeps_full_reindex_fallback(client, monkeypatch):
    connector = LegacyEmptyResultProbeConnector()
    reindex_calls: list[dict] = []

    def capture_reindex(_session, **kwargs):
        reindex_calls.append(kwargs)

    monkeypatch.setattr(ingest_base, "rebuild_search_documents", capture_reindex)

    with db.SessionLocal() as session:
        stats = connector.run(session, incremental=True)
        session.commit()

    assert stats.files_seen == 1
    assert len(reindex_calls) == 1
    assert reindex_calls[0] == {}
