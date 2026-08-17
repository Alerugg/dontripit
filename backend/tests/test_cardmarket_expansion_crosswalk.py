from __future__ import annotations

from app import db
from app.jobs.cardmarket_catalog_audit import ProductListRow
from app.jobs.cardmarket_expansion_crosswalk import derive_expansion_crosswalk
from app.models import Card, Game, Print, PrintIdentifier, Set


def _seed_set(session, *, game_slug: str, set_code: str):
    game = session.query(Game).filter(Game.slug == game_slug).one_or_none()
    if game is None:
        game = Game(slug=game_slug, name=game_slug.upper())
        session.add(game)
        session.flush()
    set_row = Set(game_id=game.id, code=set_code, name=f"Set {set_code}")
    session.add(set_row)
    session.flush()
    return game, set_row


def _mapped_print(session, *, game, set_row, product_id: str, name: str, number: str):
    card = Card(game_id=game.id, name=name, card_key=f"{game.slug}:{set_row.code}:{number}:{product_id}")
    session.add(card)
    session.flush()
    print_row = Print(
        set_id=set_row.id,
        card_id=card.id,
        collector_number=number,
        language="en",
        is_foil=False,
        variant="default",
        print_key=f"{game.slug}:{set_row.code}:{number}:{product_id}",
    )
    session.add(print_row)
    session.flush()
    session.add(PrintIdentifier(print_id=print_row.id, source="cardmarket", external_id=product_id))
    session.flush()
    return print_row


def _product(product_id: str, name: str, expansion_id: str, category="Pokemon Single"):
    return ProductListRow(product_id, name, "1", category, expansion_id)


def test_three_existing_exact_mappings_create_reviewable_consensus(client):
    with db.SessionLocal() as session:
        game, set_row = _seed_set(session, game_slug="pokemon", set_code="SV01")
        products = []
        for index, name in enumerate(["Pikachu", "Raichu", "Bulbasaur"], start=1):
            product_id = str(100 + index)
            _mapped_print(session, game=game, set_row=set_row, product_id=product_id, name=name, number=f"{index:03d}")
            products.append(_product(product_id, name, "9001"))
        session.commit()

        before_identifiers = session.query(PrintIdentifier).count()
        summary, decisions, proposals = derive_expansion_crosswalk(session, products, min_samples=3)
        after_identifiers = session.query(PrintIdentifier).count()

        assert before_identifiers == after_identifiers == 3
        assert summary["write_mode"] == "disabled"
        assert summary["reviewable_unique_consensus"] == 1
        assert decisions[0].status == "reviewable_unique_consensus"
        assert decisions[0].game == "pokemon"
        assert decisions[0].set_code == "SV01"
        assert decisions[0].mapped_products == 3
        assert proposals["9001"]["game"] == "pokemon"
        assert proposals["9001"]["set_code"] == "SV01"
        assert proposals["9001"]["evidence"]["consensus"] == 1.0


def test_one_sample_is_insufficient_even_with_perfect_consensus(client):
    with db.SessionLocal() as session:
        game, set_row = _seed_set(session, game_slug="pokemon", set_code="SV02")
        _mapped_print(session, game=game, set_row=set_row, product_id="201", name="Mew", number="001")
        session.commit()

        products = [_product("201", "Mew", "9002")]
        summary, decisions, proposals = derive_expansion_crosswalk(session, products, min_samples=3)

        assert summary["insufficient_evidence"] == 1
        assert decisions[0].status == "insufficient_evidence"
        assert decisions[0].set_code == "SV02"
        assert proposals == {}


def test_same_expansion_pointing_to_multiple_internal_sets_is_blocked(client):
    with db.SessionLocal() as session:
        game, set_a = _seed_set(session, game_slug="pokemon", set_code="A")
        _, set_b = _seed_set(session, game_slug="pokemon", set_code="B")
        _mapped_print(session, game=game, set_row=set_a, product_id="301", name="A", number="001")
        _mapped_print(session, game=game, set_row=set_b, product_id="302", name="B", number="002")
        session.commit()

        products = [
            _product("301", "A", "9003"),
            _product("302", "B", "9003"),
        ]
        summary, decisions, proposals = derive_expansion_crosswalk(session, products, min_samples=1)

        assert summary["conflicting_internal_sets"] == 1
        assert decisions[0].status == "conflicting_internal_sets"
        assert decisions[0].unique_internal_sets == 2
        assert proposals == {}


def test_category_game_disagreement_is_blocked(client):
    with db.SessionLocal() as session:
        game, set_row = _seed_set(session, game_slug="onepiece", set_code="OP05")
        _mapped_print(session, game=game, set_row=set_row, product_id="401", name="Luffy", number="OP05-001")
        _mapped_print(session, game=game, set_row=set_row, product_id="402", name="Zoro", number="OP05-002")
        session.commit()

        products = [
            _product("401", "Luffy", "9004", category="Pokemon Single"),
            _product("402", "Zoro", "9004", category="Pokemon Single"),
        ]
        summary, decisions, proposals = derive_expansion_crosswalk(session, products, min_samples=1)

        assert summary["category_game_conflict"] == 1
        assert decisions[0].status == "category_game_conflict"
        assert proposals == {}


def test_mappings_missing_from_product_list_are_counted_not_guessed(client):
    with db.SessionLocal() as session:
        game, set_row = _seed_set(session, game_slug="pokemon", set_code="SV03")
        _mapped_print(session, game=game, set_row=set_row, product_id="501", name="Eevee", number="001")
        session.commit()

        summary, decisions, proposals = derive_expansion_crosswalk(session, [], min_samples=1)

        assert summary["mapped_identifiers_missing_from_product_list"] == 1
        assert decisions == []
        assert proposals == {}
