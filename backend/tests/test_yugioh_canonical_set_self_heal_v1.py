import json
from datetime import date

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.ingest.base import IngestStats
from app.ingest.connectors.ygoprodeck_yugioh_v2 import YgoProDeckYugiohV2Connector
from app.models import Game, Set


def _open_session():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Game.__table__.create(engine)
    Set.__table__.create(engine)
    session = Session(engine)
    game = Game(slug="yugioh", name="Yu-Gi-Oh!")
    session.add(game)
    session.flush()
    return engine, session, game


def _write_sets(tmp_path, rows):
    path = tmp_path / "sets.jsonl"
    path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def _source_set(code: str, name: str | None = None, release_date: str | None = None):
    return {
        "code": code,
        "name": name or f"Set {code}",
        "yugioh_id": f"family:{code}",
        "release_date": release_date,
        "release_names": [name or f"Set {code}"],
    }


def test_current_set_reconcile_inserts_wcs_and_crocs_exact_global_identities(tmp_path):
    connector = YgoProDeckYugiohV2Connector()
    engine, session, game = _open_session()
    try:
        _write_sets(
            tmp_path,
            [
                _source_set(
                    "26LP",
                    "Limited Pack World Championship 2026",
                    "2026-08-29",
                ),
                _source_set("CRC1", "Crocs collaboration card", None),
            ],
        )
        stats = IngestStats()

        result = connector._reconcile_current_canonical_sets_from_snapshot(
            session, stats, tmp_path
        )

        rows = session.execute(select(Set).order_by(Set.code)).scalars().all()
        assert [(row.code, row.region, row.yugioh_id) for row in rows] == [
            ("26LP", "global", "family:26LP"),
            ("CRC1", "global", "family:CRC1"),
        ]
        assert rows[0].game_id == game.id
        assert rows[0].name == "Limited Pack World Championship 2026"
        assert rows[0].release_date == date(2026, 8, 29)
        assert rows[1].name == "Crocs collaboration card"
        assert rows[1].release_date is None
        assert stats.records_inserted == 2
        assert result == {
            "card_ids": set(),
            "set_ids": {row.id for row in rows},
            "print_ids": set(),
        }
    finally:
        session.close()
        engine.dispose()


def test_current_set_reconcile_exact_existing_identity_is_noop(tmp_path):
    connector = YgoProDeckYugiohV2Connector()
    engine, session, game = _open_session()
    try:
        existing = Set(
            game_id=game.id,
            code="26LP",
            region="global",
            name="Limited Pack World Championship 2026",
            release_date=date(2026, 8, 29),
            yugioh_id="family:26LP",
        )
        session.add(existing)
        session.flush()
        _write_sets(
            tmp_path,
            [_source_set("26LP", "Limited Pack World Championship 2026", "2026-08-29")],
        )
        stats = IngestStats()

        result = connector._reconcile_current_canonical_sets_from_snapshot(
            session, stats, tmp_path
        )

        assert session.execute(select(Set)).scalars().all() == [existing]
        assert stats.records_inserted == 0
        assert result == {"card_ids": set(), "set_ids": set(), "print_ids": set()}
    finally:
        session.close()
        engine.dispose()


def test_current_set_reconcile_existing_exact_code_without_source_id_is_noop(tmp_path):
    connector = YgoProDeckYugiohV2Connector()
    engine, session, game = _open_session()
    try:
        existing = Set(
            game_id=game.id,
            code="CRC1",
            region="global",
            name="Existing reviewed CRC1 family",
            yugioh_id=None,
        )
        session.add(existing)
        session.flush()
        _write_sets(tmp_path, [_source_set("CRC1", "Crocs collaboration card")])
        stats = IngestStats()

        result = connector._reconcile_current_canonical_sets_from_snapshot(
            session, stats, tmp_path
        )

        assert session.execute(select(Set)).scalars().all() == [existing]
        assert existing.yugioh_id is None
        assert stats.records_inserted == 0
        assert result == {"card_ids": set(), "set_ids": set(), "print_ids": set()}
    finally:
        session.close()
        engine.dispose()


def test_current_set_reconcile_rejects_conflicting_source_identity_for_same_code(tmp_path):
    connector = YgoProDeckYugiohV2Connector()
    engine, session, game = _open_session()
    try:
        existing = Set(
            game_id=game.id,
            code="26LP",
            region="global",
            name="Conflicting identity",
            yugioh_id="family:OTHER",
        )
        session.add(existing)
        session.flush()
        _write_sets(tmp_path, [_source_set("26LP", "Limited Pack World Championship 2026")])
        stats = IngestStats()

        with pytest.raises(RuntimeError, match="source identity conflict"):
            connector._reconcile_current_canonical_sets_from_snapshot(
                session, stats, tmp_path
            )

        assert stats.records_inserted == 0
        assert session.execute(select(Set)).scalars().all() == [existing]
    finally:
        session.close()
        engine.dispose()


def test_current_set_reconcile_rejects_yugioh_id_owned_by_different_code(tmp_path):
    connector = YgoProDeckYugiohV2Connector()
    engine, session, game = _open_session()
    try:
        existing = Set(
            game_id=game.id,
            code="OTHER",
            region="global",
            name="Conflicting id owner",
            yugioh_id="family:26LP",
        )
        session.add(existing)
        session.flush()
        _write_sets(tmp_path, [_source_set("26LP", "Limited Pack World Championship 2026")])
        stats = IngestStats()

        with pytest.raises(RuntimeError, match="identity drift"):
            connector._reconcile_current_canonical_sets_from_snapshot(
                session, stats, tmp_path
            )

        assert stats.records_inserted == 0
        assert session.execute(select(Set)).scalars().all() == [existing]
    finally:
        session.close()
        engine.dispose()


def test_current_set_reconcile_fails_before_writes_above_bounded_ceiling(tmp_path):
    connector = YgoProDeckYugiohV2Connector()
    connector.canonical_set_reconcile_max_writes = 1
    engine, session, _game = _open_session()
    try:
        _write_sets(tmp_path, [_source_set("26LP"), _source_set("CRC1")])
        stats = IngestStats()

        with pytest.raises(RuntimeError, match="bounded write ceiling"):
            connector._reconcile_current_canonical_sets_from_snapshot(
                session, stats, tmp_path
            )

        assert stats.records_inserted == 0
        assert session.execute(select(Set)).scalars().all() == []
    finally:
        session.close()
        engine.dispose()
