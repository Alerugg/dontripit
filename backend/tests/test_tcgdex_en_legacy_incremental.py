from sqlalchemy import select

from app import db
from app.ingest.connectors.tcgdex_pokemon_incremental_guard import (
    LegacyAwarePhysicalMultilingualTcgdexPokemonConnector,
)
from app.models import Card, Game, Print, Set


def _raw_card(language: str) -> dict:
    return {
        "_language": language,
        "set": {
            "id": "swsh1",
            "abbreviation": "SWSH1",
            "name": "Sword & Shield" if language == "en" else "Espada y Escudo",
            "releaseDate": "2020-02-07",
        },
        "id": "swsh1-1",
        "localId": "1",
        "name": "Celebi",
        "image": f"https://assets.tcgdex.net/{language}/swsh1/1",
        "hp": 70,
        "stage": "Basic",
        "types": ["Grass"],
        "abilities": [],
        "attacks": [],
        "rules": [],
    }


def test_legacy_english_canonical_rows_are_incremental_complete_without_alias_backfill(client):
    connector = LegacyAwarePhysicalMultilingualTcgdexPokemonConnector()
    normalized = connector.normalize(_raw_card("en"), lang="en")

    with db.SessionLocal() as session:
        game = Game(slug="pokemon", name="Pokémon")
        session.add(game)
        session.flush()
        set_row = Set(
            game_id=game.id,
            code="swsh1",
            name="Sword & Shield",
            tcgdex_id="swsh1",
        )
        card = Card(
            game_id=game.id,
            name="Celebi",
            card_key="pokemon:tcgdex:swsh1-1",
            tcgdex_id="swsh1-1",
        )
        session.add_all([set_row, card])
        session.flush()
        session.add(
            Print(
                card_id=card.id,
                set_id=set_row.id,
                collector_number="1",
                language="en",
                rarity="Rare",
                is_foil=False,
                tcgdex_id="swsh1-1",
                variant="default",
            )
        )
        session.flush()

        assert connector._localized_state_complete(session, normalized) is True


def test_non_english_overlay_still_requires_regional_physical_state(client):
    connector = LegacyAwarePhysicalMultilingualTcgdexPokemonConnector()
    en = connector.normalize(_raw_card("en"), lang="en")
    es = connector.normalize(_raw_card("es"), lang="es")

    with db.SessionLocal() as session:
        game = Game(slug="pokemon", name="Pokémon")
        session.add(game)
        session.flush()
        set_row = Set(
            game_id=game.id,
            code="swsh1",
            name="Sword & Shield",
            tcgdex_id="swsh1",
        )
        card = Card(
            game_id=game.id,
            name="Celebi",
            card_key="pokemon:tcgdex:swsh1-1",
            tcgdex_id="swsh1-1",
        )
        session.add_all([set_row, card])
        session.flush()
        session.add(
            Print(
                card_id=card.id,
                set_id=set_row.id,
                collector_number="1",
                language="en",
                rarity="Rare",
                is_foil=False,
                tcgdex_id="swsh1-1",
                variant="default",
            )
        )
        session.flush()

        assert connector._localized_state_complete(session, en) is True
        assert connector._localized_state_complete(session, es) is False
        assert session.execute(select(Print).where(Print.language == "es")).scalars().all() == []
