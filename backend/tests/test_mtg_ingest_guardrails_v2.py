from pathlib import Path

from sqlalchemy import select

from app import db
from app.ingest.base import SourceConnector
from app.ingest.registry import get_connector
from app.models import Source, SourceRecord


def test_source_connector_persists_raw_payload_by_default():
    connector = SourceConnector()
    payload = {"name": "Keep me", "nested": {"value": 7}}

    assert connector.persist_raw_source_payload is True
    assert connector.source_record_payload(payload) == payload


def test_scryfall_source_record_keeps_checksum_but_omits_raw_payload(client):
    connector = get_connector("scryfall_mtg")
    assert connector.persist_raw_source_payload is False

    with db.SessionLocal() as session:
        stats = connector.run(
            session,
            "data/fixtures/scryfall_mtg_sample.json",
            fixture=True,
            incremental=True,
            limit=1,
        )
        session.commit()

    with db.SessionLocal() as session:
        source = session.execute(
            select(Source).where(Source.name == "scryfall_mtg")
        ).scalar_one()
        records = session.execute(
            select(SourceRecord).where(SourceRecord.source_id == source.id)
        ).scalars().all()

    assert stats.files_seen == 1
    assert len(records) == 1
    record = records[0]
    assert len(record.checksum) == 64
    assert record.raw_json == {
        "_payload_omitted": True,
        "_connector": "scryfall_mtg",
    }
    assert "name" not in record.raw_json
    assert "image_uris" not in record.raw_json


def test_scryfall_checksum_still_makes_incremental_reingest_idempotent(client):
    connector = get_connector("scryfall_mtg")

    with db.SessionLocal() as session:
        first = connector.run(
            session,
            "data/fixtures/scryfall_mtg_sample.json",
            fixture=True,
            incremental=True,
            limit=1,
        )
        session.commit()

    with db.SessionLocal() as session:
        second = connector.run(
            session,
            "data/fixtures/scryfall_mtg_sample.json",
            fixture=True,
            incremental=True,
            limit=1,
        )
        session.commit()
        source = session.execute(
            select(Source).where(Source.name == "scryfall_mtg")
        ).scalar_one()
        source_record_count = len(
            session.execute(
                select(SourceRecord).where(SourceRecord.source_id == source.id)
            ).scalars().all()
        )

    assert first.files_seen == 1
    assert second.files_seen == 1
    assert second.files_skipped == 1
    assert second.records_inserted == 0
    assert second.records_updated == 0
    assert source_record_count == 1


def test_scryfall_jsonl_parser_contract_is_still_supported():
    connector = get_connector("scryfall_mtg")
    rows = connector._parse_jsonl_lines(
        [
            '{"id":"a","games":["paper"]}\n',
            b'{"id":"b","games":["paper"]}\n',
        ]
    )

    assert [row["id"] for row in rows] == ["a", "b"]


def test_general_ingest_workflow_forces_mtg_quarantine():
    repo_root = Path(__file__).resolve().parents[2]
    workflow = (repo_root / ".github" / "workflows" / "ingest.yml").read_text(encoding="utf-8")

    assert 'MTG_LIMIT="0"' in workflow
    assert '--mtg-limit "${MTG_LIMIT}"' in workflow
    assert "MTG general ingest is quarantined" in workflow
    assert "MTG_LIMIT_INPUT=" not in workflow
