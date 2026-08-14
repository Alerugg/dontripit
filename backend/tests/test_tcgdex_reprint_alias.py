from sqlalchemy import select

from app import db
from app.ingest.base import IngestStats
from app.ingest.connectors.tcgdex_pokemon_multilingual_physical import (
    PhysicalMultilingualTcgdexPokemonConnector,
)
from app.models import Card, Game
from app.multilingual_models import CardIdentifier


def test_physical_tcgdex_reprints_can_share_one_canonical_card(client):
    connector = PhysicalMultilingualTcgdexPokemonConnector()

    with db.SessionLocal() as session:
        game = Game(slug="pokemon", name="Pokémon")
        session.add(game)
        session.flush()
        card = Card(game_id=game.id, name="Charizard", card_key="pokemon:charizard")
        session.add(card)
        session.flush()

        stats = IngestStats()
        connector._upsert_card_identifier(
            session,
            card_row=card,
            language="en",
            external_id="base1-4",
            stats=stats,
        )
        connector._upsert_card_identifier(
            session,
            card_row=card,
            language="en",
            external_id="base6-3",
            stats=stats,
        )
        session.commit()

        aliases = session.execute(
            select(CardIdentifier)
            .where(
                CardIdentifier.card_id == card.id,
                CardIdentifier.source == "tcgdex:en",
            )
            .order_by(CardIdentifier.external_id)
        ).scalars().all()

        assert [row.external_id for row in aliases] == ["base1-4", "base6-3"]
        assert {row.card_id for row in aliases} == {card.id}
        assert stats.records_inserted == 2


def test_physical_tcgdex_external_alias_cannot_move_between_cards(client):
    connector = PhysicalMultilingualTcgdexPokemonConnector()

    with db.SessionLocal() as session:
        game = Game(slug="pokemon", name="Pokémon")
        session.add(game)
        session.flush()
        first = Card(game_id=game.id, name="Charizard", card_key="pokemon:charizard")
        second = Card(game_id=game.id, name="Different Card", card_key="pokemon:different")
        session.add_all([first, second])
        session.flush()

        stats = IngestStats()
        connector._upsert_card_identifier(
            session,
            card_row=first,
            language="en",
            external_id="base1-4",
            stats=stats,
        )
        session.flush()

        try:
            connector._upsert_card_identifier(
                session,
                card_row=second,
                language="en",
                external_id="base1-4",
                stats=stats,
            )
        except RuntimeError as exc:
            assert "identifier collision" in str(exc)
        else:
            raise AssertionError("Expected a strict external-identity collision")
