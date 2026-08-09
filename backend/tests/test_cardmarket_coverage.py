from __future__ import annotations

from decimal import Decimal

from app import db
from app.jobs.cardmarket_coverage import build_cardmarket_coverage
from app.jobs.cardmarket_prices import CardmarketPriceRow
from app.models import Card, Game, Print, PrintIdentifier, Set


def _seed_set(session, *, game_slug: str, set_code: str, print_count: int, mapped: int, foil_at: set[int] | None = None):
    foil_at = foil_at or set()
    game = session.query(Game).filter(Game.slug == game_slug).one_or_none()
    if game is None:
        game = Game(slug=game_slug, name=game_slug.upper())
        session.add(game)
        session.flush()
    set_row = Set(game_id=game.id, code=set_code, name=f"Set {set_code}")
    session.add(set_row)
    session.flush()

    products = []
    for index in range(1, print_count + 1):
        card = Card(game_id=game.id, name=f"Card {set_code} {index}", card_key=f"{game_slug}:{set_code}:{index}")
        session.add(card)
        session.flush()
        print_row = Print(
            set_id=set_row.id,
            card_id=card.id,
            collector_number=str(index),
            language="en",
            is_foil=index in foil_at,
            variant="default",
            print_key=f"{game_slug}:{set_code}:{index}",
        )
        session.add(print_row)
        session.flush()
        if index <= mapped:
            product_id = f"{set_code}-{index}"
            session.add(PrintIdentifier(print_id=print_row.id, source="cardmarket", external_id=product_id))
            products.append((product_id, bool(print_row.is_foil)))
    session.flush()
    return products


def test_mapping_coverage_is_reported_by_game_and_set(client):
    with db.SessionLocal() as session:
        _seed_set(session, game_slug="pokemon", set_code="A", print_count=10, mapped=6)
        _seed_set(session, game_slug="pokemon", set_code="B", print_count=5, mapped=5)
        _seed_set(session, game_slug="onepiece", set_code="OP01", print_count=8, mapped=0)
        session.commit()

        report = build_cardmarket_coverage(session)

        assert report["summary"]["total_prints"] == 23
        assert report["summary"]["mapped_prints"] == 11
        assert report["summary"]["price_guide_supplied"] is False
        assert report["summary"]["write_mode"] == "disabled"
        pokemon = next(item for item in report["games"] if item["game"] == "pokemon")
        assert pokemon["total_prints"] == 15
        assert pokemon["mapped_prints"] == 11
        op01 = next(item for item in report["sets"] if item["set_code"] == "OP01")
        assert op01["mapping_coverage"] == 0.0
        assert report["priority_sets"][0]["set_code"] == "OP01"


def test_price_guide_counts_only_finish_compatible_candidates(client):
    with db.SessionLocal() as session:
        products = _seed_set(
            session,
            game_slug="pokemon",
            set_code="SV",
            print_count=4,
            mapped=4,
            foil_at={3, 4},
        )
        session.commit()

        rows = [
            CardmarketPriceRow(product_id=products[0][0], low=Decimal("1.00"), trend=Decimal("2.00")),
            CardmarketPriceRow(product_id=products[1][0], low_ex=Decimal("2.50")),
            CardmarketPriceRow(product_id=products[2][0], foil_low=Decimal("5.00"), foil_trend=Decimal("6.00")),
            CardmarketPriceRow(product_id=products[3][0], low=Decimal("9.00")),
        ]

        report = build_cardmarket_coverage(session, rows)

        assert report["summary"]["mapped_prints"] == 4
        assert report["summary"]["priced_candidates"] == 3
        assert report["summary"]["mapped_products_wrong_finish"] == 1
        assert report["games"][0]["price_candidate_coverage"] == 0.75


def test_mapped_product_missing_from_price_guide_is_counted(client):
    with db.SessionLocal() as session:
        products = _seed_set(session, game_slug="yugioh", set_code="Y1", print_count=2, mapped=2)
        session.commit()

        rows = [CardmarketPriceRow(product_id=products[0][0], avg=Decimal("3.00"))]
        report = build_cardmarket_coverage(session, rows)

        assert report["summary"]["mapped_products_missing_from_price_guide"] == 1
        assert report["summary"]["priced_candidates"] == 1


def test_duplicate_price_rows_do_not_double_count_candidates(client):
    with db.SessionLocal() as session:
        products = _seed_set(session, game_slug="mtg", set_code="M1", print_count=1, mapped=1)
        session.commit()

        product_id = products[0][0]
        rows = [
            CardmarketPriceRow(product_id=product_id, low=Decimal("1.00")),
            CardmarketPriceRow(product_id=product_id, low=Decimal("99.00")),
        ]
        report = build_cardmarket_coverage(session, rows)

        assert report["summary"]["duplicate_price_rows"] == 1
        assert report["summary"]["priced_candidates"] == 1
