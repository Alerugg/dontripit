import json

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.ingest.base import IngestStats
from app.ingest.connectors.ygoprodeck_yugioh_v2 import YgoProDeckYugiohV2Connector
from app.models import Card, Game


def _open_session():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Game.__table__.create(engine)
    Card.__table__.create(engine)
    session = Session(engine)
    game = Game(slug="yugioh", name="Yu-Gi-Oh!")
    session.add(game)
    session.flush()
    return engine, session, game


def _write_cards(tmp_path, rows):
    path = tmp_path / "cards.jsonl"
    path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def _source_card(source_id: str, name: str | None = None):
    return {
        "source_card_id": source_id,
        "name": name or f"Card {source_id}",
        "card_key": f"yugioh:ygoprodeck:card-id:{source_id}",
        "yugoprodeck_id": source_id,
    }


def test_current_card_reconcile_inserts_exact_zero_print_identity(tmp_path):
    connector = YgoProDeckYugiohV2Connector()
    engine, session, game = _open_session()
    try:
        source = _source_card("42026772", "The Magical King of Dimension Zeta")
        _write_cards(tmp_path, [source])
        stats = IngestStats()

        result = connector._reconcile_current_canonical_cards_from_snapshot(
            session, stats, tmp_path
        )

        row = session.execute(select(Card)).scalar_one()
        assert row.game_id == game.id
        assert row.name == source["name"]
        assert row.yugoprodeck_id == source["yugoprodeck_id"]
        assert row.card_key == source["card_key"]
        assert stats.records_inserted == 1
        assert result == {
            "card_ids": {row.id},
            "set_ids": set(),
            "print_ids": set(),
        }
    finally:
        session.close()
        engine.dispose()


def test_current_card_reconcile_exact_existing_identity_is_noop(tmp_path):
    connector = YgoProDeckYugiohV2Connector()
    engine, session, game = _open_session()
    try:
        source = _source_card("48128081", "Destined Duel")
        existing = Card(
            game_id=game.id,
            name=source["name"],
            yugoprodeck_id=source["yugoprodeck_id"],
            card_key=source["card_key"],
        )
        session.add(existing)
        session.flush()
        _write_cards(tmp_path, [source])
        stats = IngestStats()

        result = connector._reconcile_current_canonical_cards_from_snapshot(
            session, stats, tmp_path
        )

        assert session.execute(select(Card)).scalars().all() == [existing]
        assert stats.records_inserted == 0
        assert result == {"card_ids": set(), "set_ids": set(), "print_ids": set()}
    finally:
        session.close()
        engine.dispose()


def test_current_card_reconcile_never_coerces_same_name_to_existing_identity(tmp_path):
    connector = YgoProDeckYugiohV2Connector()
    engine, session, game = _open_session()
    try:
        session.add(
            Card(
                game_id=game.id,
                name="Same Display Name",
                yugoprodeck_id="111",
                card_key="yugioh:ygoprodeck:card-id:111",
            )
        )
        session.flush()
        source = _source_card("222", "Same Display Name")
        _write_cards(tmp_path, [source])
        stats = IngestStats()

        result = connector._reconcile_current_canonical_cards_from_snapshot(
            session, stats, tmp_path
        )

        rows = session.execute(select(Card).order_by(Card.yugoprodeck_id)).scalars().all()
        assert [row.yugoprodeck_id for row in rows] == ["111", "222"]
        assert [row.name for row in rows] == ["Same Display Name", "Same Display Name"]
        assert stats.records_inserted == 1
        assert len(result["card_ids"]) == 1
    finally:
        session.close()
        engine.dispose()


@pytest.mark.parametrize(
    ("existing_source_id", "existing_card_key", "source_id", "source_card_key"),
    [
        ("333", "yugioh:ygoprodeck:card-id:legacy-333", "333", "yugioh:ygoprodeck:card-id:333"),
        ("legacy-444", "yugioh:ygoprodeck:card-id:444", "444", "yugioh:ygoprodeck:card-id:444"),
    ],
)
def test_current_card_reconcile_rejects_source_id_or_card_key_collision(
    tmp_path,
    existing_source_id,
    existing_card_key,
    source_id,
    source_card_key,
):
    connector = YgoProDeckYugiohV2Connector()
    engine, session, game = _open_session()
    try:
        existing = Card(
            game_id=game.id,
            name="Existing",
            yugoprodeck_id=existing_source_id,
            card_key=existing_card_key,
        )
        session.add(existing)
        session.flush()
        source = _source_card(source_id, "Current Source")
        source["card_key"] = source_card_key
        _write_cards(tmp_path, [source])
        stats = IngestStats()

        with pytest.raises(RuntimeError, match="identity drift|identity collision"):
            connector._reconcile_current_canonical_cards_from_snapshot(
                session, stats, tmp_path
            )

        assert stats.records_inserted == 0
        assert session.execute(select(Card)).scalars().all() == [existing]
    finally:
        session.close()
        engine.dispose()


def test_current_card_reconcile_fails_before_writes_above_bounded_ceiling(tmp_path):
    connector = YgoProDeckYugiohV2Connector()
    connector.canonical_card_reconcile_max_writes = 1
    engine, session, _game = _open_session()
    try:
        _write_cards(tmp_path, [_source_card("501"), _source_card("502")])
        stats = IngestStats()

        with pytest.raises(RuntimeError, match="bounded write ceiling"):
            connector._reconcile_current_canonical_cards_from_snapshot(
                session, stats, tmp_path
            )

        assert stats.records_inserted == 0
        assert session.execute(select(Card)).scalars().all() == []
    finally:
        session.close()
        engine.dispose()
