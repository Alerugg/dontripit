import pytest
from sqlalchemy.exc import IntegrityError

from app import db
from app.models import Card, Game, Print, Set
from app.search_v2_models import CardSearchProfile, FacetDefinition, PrintSearchProfile


def _seed_card_and_print(session):
    game = Game(slug="onepiece", name="ONE PIECE Card Game")
    session.add(game)
    session.flush()
    set_row = Set(game_id=game.id, code="op-05", name="Awakening of the New Era")
    session.add(set_row)
    session.flush()
    card = Card(game_id=game.id, name="Monkey.D.Luffy", card_key="onepiece:op05-119")
    session.add(card)
    session.flush()
    print_row = Print(
        set_id=set_row.id,
        card_id=card.id,
        collector_number="OP05-119",
        language="en",
        rarity="SEC",
        variant="p1",
        print_key="onepiece:op-05:op05-119:en:p1",
    )
    session.add(print_row)
    session.flush()
    return game, card, print_row


def test_search_profiles_store_rebuildable_structured_projection(client):
    with db.SessionLocal() as session:
        game, card, print_row = _seed_card_and_print(session)
        session.add(
            CardSearchProfile(
                card_id=card.id,
                game_id=game.id,
                normalized_name="monkey d luffy",
                aliases_json=["luffy"],
                keywords_json=["one piece"],
                attributes_json={"color": ["Purple"], "power": 12000},
                search_text="monkey d luffy one piece",
            )
        )
        session.add(
            PrintSearchProfile(
                print_id=print_row.id,
                card_id=card.id,
                game_id=game.id,
                normalized_name="monkey d luffy",
                normalized_set_code="op-05",
                normalized_collector_number="op05-119",
                language="en",
                rarity="SEC",
                exact_variant="p1",
                variant_family="parallel",
                release_names_json=["Awakening of the New Era"],
                attributes_json={"is_manga": True},
                search_text="monkey d luffy op05 119 manga parallel english",
            )
        )
        session.commit()

        assert session.query(CardSearchProfile).count() == 1
        saved = session.query(PrintSearchProfile).one()
        assert saved.variant_family == "parallel"
        assert saved.attributes_json["is_manga"] is True


def test_facet_definition_identity_is_unique_per_game_scope_key(client):
    with db.SessionLocal() as session:
        game = Game(slug="onepiece", name="ONE PIECE Card Game")
        session.add(game)
        session.flush()
        kwargs = dict(
            game_id=game.id,
            scope="card",
            key="color",
            label="Color",
            value_type="enum",
            ui_type="chips",
            source_path="attributes.color",
        )
        session.add(FacetDefinition(**kwargs))
        session.commit()
        session.add(FacetDefinition(**kwargs))
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()
