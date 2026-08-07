import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.ingest.normalization import normalize_collector_number
from app.models import Base, Card, Game, Print, PrintIdentifier, PrintImage, Set
from app.scripts.bootstrap_onepiece_fast import (
    _assert_onepiece_empty,
    _insert_catalog,
    _prepare_payload,
)


SAMPLE = {
    "source": "onepiece_official",
    "language": "EN-US",
    "sets": [
        {"code": "OP-01", "name": "Romance Dawn", "release_date": "2022-12-02"},
    ],
    "cards": [
        {
            "id": "onepiece:monkey.d.luffy",
            "name": "Monkey.D.Luffy",
            "prints": [
                {
                    "id": "OP01-003",
                    "set_code": "OP-01",
                    "collector_number": "OP01-003",
                    "rarity": "R",
                    "variant": "default",
                    "image_url": "https://example.test/luffy.png",
                },
                {
                    "id": "OP01-003",
                    "set_code": "OP-01",
                    "collector_number": "OP01-003",
                    "rarity": "R",
                    "variant": "default",
                    "image_url": "https://example.test/luffy-duplicate.png",
                },
                {
                    "id": "OP01-003_P1",
                    "set_code": "OP-01",
                    "collector_number": "OP01-003",
                    "rarity": "R",
                    "variant": "parallel",
                    "image_url": "https://example.test/luffy-parallel.png",
                },
            ],
        },
        {
            "id": "onepiece:nami",
            "name": "Nami",
            "prints": [
                {
                    "id": "OP01-016",
                    "set_code": "OP-01",
                    "collector_number": "OP01-016",
                    "rarity": "R",
                    "variant": "default",
                    "image_url": "https://example.test/nami.png",
                }
            ],
        },
    ],
}


def _session():
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return Session(engine)


def test_prepare_payload_deduplicates_identity_and_normalizes_language():
    prepared = _prepare_payload(SAMPLE)

    assert prepared["language"] == "en"
    assert len(prepared["sets"]) == 1
    assert len(prepared["cards"]) == 2
    assert len(prepared["prints"]) == 3

    keys = {row["print_key"] for row in prepared["prints"]}
    assert "onepiece:op-01:op01-003:en:default" in keys
    assert "onepiece:op-01:op01-003:en:parallel" in keys
    assert all(normalize_collector_number(row["collector_number"]) for row in prepared["prints"])


def test_insert_catalog_creates_all_entities_in_batches():
    prepared = _prepare_payload(SAMPLE)
    with _session() as session:
        _assert_onepiece_empty(session)
        stats = _insert_catalog(session, prepared)
        session.commit()

        game = session.execute(select(Game).where(Game.slug == "onepiece")).scalar_one()
        assert stats == {
            "game_id": game.id,
            "sets": 1,
            "cards": 2,
            "prints": 3,
            "images": 3,
            "identifiers": 3,
        }
        assert session.execute(select(func.count(Set.id)).where(Set.game_id == game.id)).scalar_one() == 1
        assert session.execute(select(func.count(Card.id)).where(Card.game_id == game.id)).scalar_one() == 2
        assert session.execute(select(func.count(Print.id))).scalar_one() == 3
        assert session.execute(select(func.count(PrintImage.id))).scalar_one() == 3
        assert session.execute(select(func.count(PrintIdentifier.id))).scalar_one() == 3


def test_fast_bootstrap_refuses_nonempty_onepiece_catalog():
    prepared = _prepare_payload(SAMPLE)
    with _session() as session:
        _insert_catalog(session, prepared)
        session.commit()

        with pytest.raises(RuntimeError, match="only allowed on an empty One Piece catalog"):
            _assert_onepiece_empty(session)


def test_insert_catalog_reuses_preexisting_empty_game_row():
    prepared = _prepare_payload(SAMPLE)
    with _session() as session:
        game = Game(slug="onepiece", name="ONE PIECE Card Game")
        session.add(game)
        session.commit()

        _assert_onepiece_empty(session)
        stats = _insert_catalog(session, prepared)
        session.commit()

        assert stats["game_id"] == game.id
        assert session.execute(select(func.count(Game.id)).where(Game.slug == "onepiece")).scalar_one() == 1
