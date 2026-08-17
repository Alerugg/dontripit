import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.catalog_release_models import CatalogRelease, PrintRelease
from app.models import Base, Card, Game, Print, Set


def _session():
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return Session(engine)


def _seed_print(session):
    game = Game(slug="onepiece", name="ONE PIECE Card Game")
    session.add(game)
    session.flush()
    set_row = Set(game_id=game.id, code="OP-01", name="Romance Dawn")
    session.add(set_row)
    session.flush()
    card = Card(game_id=game.id, name="Monkey.D.Luffy", card_key="onepiece:op01-003")
    session.add(card)
    session.flush()
    print_row = Print(
        set_id=set_row.id,
        card_id=card.id,
        collector_number="OP01-003",
        language="en",
        rarity="R",
        is_foil=False,
        variant="default",
        print_key="onepiece:op-01:op01-003:en:default",
    )
    session.add(print_row)
    session.flush()
    return game, print_row


def test_release_and_print_release_persist_many_to_many_provenance():
    with _session() as session:
        game, print_row = _seed_print(session)
        release_a = CatalogRelease(
            game_id=game.id,
            source="onepiece_official",
            external_id="1",
            name="Romance Dawn",
            language="en",
            region="global-en",
        )
        release_b = CatalogRelease(
            game_id=game.id,
            source="onepiece_official",
            external_id="99",
            name="Reprint Collection",
            language="en",
            region="global-en",
        )
        session.add_all([release_a, release_b])
        session.flush()
        session.add_all(
            [
                PrintRelease(print_id=print_row.id, release_id=release_a.id, source_print_id="OP01-003"),
                PrintRelease(print_id=print_row.id, release_id=release_b.id, source_print_id="OP01-003"),
            ]
        )
        session.commit()

        links = session.execute(select(PrintRelease).where(PrintRelease.print_id == print_row.id)).scalars().all()
        assert len(links) == 2
        assert {link.release_id for link in links} == {release_a.id, release_b.id}


def test_catalog_release_source_external_identity_is_unique_per_game():
    with _session() as session:
        game, _print_row = _seed_print(session)
        session.add(
            CatalogRelease(
                game_id=game.id,
                source="onepiece_official",
                external_id="1",
                name="First",
            )
        )
        session.commit()
        session.add(
            CatalogRelease(
                game_id=game.id,
                source="onepiece_official",
                external_id="1",
                name="Duplicate",
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()


def test_print_release_is_unique_per_print_and_release():
    with _session() as session:
        game, print_row = _seed_print(session)
        release = CatalogRelease(
            game_id=game.id,
            source="onepiece_official",
            external_id="1",
            name="Romance Dawn",
        )
        session.add(release)
        session.flush()
        session.add(PrintRelease(print_id=print_row.id, release_id=release.id, source_print_id="OP01-003"))
        session.commit()
        session.add(PrintRelease(print_id=print_row.id, release_id=release.id, source_print_id="OP01-003"))
        with pytest.raises(IntegrityError):
            session.commit()
