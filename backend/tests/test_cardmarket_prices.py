from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import func, select

from app import db
from app.jobs.cardmarket_prices import (
    apply_import_plan,
    build_import_plan,
    load_price_guide_bytes,
)
from app.models import Card, Game, PriceSnapshot, PriceSource, Print, PrintIdentifier, Set


def _seed_print(session, *, product_id: str, foil: bool, number: str, variant: str):
    game = session.execute(select(Game).where(Game.slug == "pokemon")).scalar_one_or_none()
    if game is None:
        game = Game(slug="pokemon", name="Pokémon")
        session.add(game)
        session.flush()
        set_row = Set(game_id=game.id, code="TST", name="Test Set")
        session.add(set_row)
        session.flush()
    else:
        set_row = session.execute(select(Set).where(Set.game_id == game.id)).scalar_one()

    card = Card(game_id=game.id, name=f"Card {number} {variant}", card_key=f"test:{number}:{variant}")
    session.add(card)
    session.flush()
    print_row = Print(
        set_id=set_row.id,
        card_id=card.id,
        collector_number=number,
        language="en",
        is_foil=foil,
        variant=variant,
        print_key=f"pokemon:test:{number}:{variant}",
    )
    session.add(print_row)
    session.flush()
    session.add(PrintIdentifier(print_id=print_row.id, source="cardmarket", external_id=product_id))
    session.flush()
    return print_row


def test_current_json_shape_selects_finish_specific_prices(client):
    payload = b'''{
      "version": 1,
      "createdAt": "2026-08-09T02:55:18+0200",
      "priceGuides": [
        {"idProduct": 111, "avg": 9.0, "low": 4.0, "trend": 8.0, "avg1": 8.5,
         "avg-holo": 20.0, "low-holo": 15.0, "trend-holo": 19.0, "avg1-holo": 18.0},
        {"idProduct": 222, "avg": 3.0, "low": 1.0, "trend": 2.5,
         "avg-holo": 7.0, "low-holo": 5.0, "trend-holo": 6.0}
      ]
    }'''
    created_at, rows = load_price_guide_bytes(payload, filename="price_guide_6.json")
    assert created_at == datetime(2026, 8, 9, 0, 55, 18, tzinfo=timezone.utc)

    with db.SessionLocal() as session:
        normal = _seed_print(session, product_id="111", foil=False, number="1", variant="normal")
        foil = _seed_print(session, product_id="222", foil=True, number="2", variant="holo")
        session.commit()

        plan = build_import_plan(session, rows, as_of=created_at)
        assert plan.summary()["mapped_exact"] == 2
        by_print = {item["entity_id"]: item for item in plan.snapshots}
        assert by_print[normal.id]["price_low"] == Decimal("4.00")
        assert by_print[normal.id]["price_market"] == Decimal("8.00")
        assert by_print[normal.id]["price_last"] == Decimal("9.00")
        assert by_print[normal.id]["raw_json"]["finish"] == "nonfoil"
        assert by_print[foil.id]["price_low"] == Decimal("5.00")
        assert by_print[foil.id]["price_market"] == Decimal("6.00")
        assert by_print[foil.id]["price_last"] == Decimal("7.00")
        assert by_print[foil.id]["raw_json"]["finish"] == "foil"


def test_legacy_csv_preserves_low_ex_plus_as_portfolio_safe_value(client):
    csv_bytes = b'''idProduct,Avg. Sell Price,Low Price,Trend Price,Foil Sell,Foil Low,Foil Trend,Low Price Ex+,AVG1,AVG7,AVG30,Foil AVG1,Foil AVG7,Foil AVG30\n333,12.00,3.00,10.00,22.00,15.00,20.00,7.50,11.00,10.50,10.25,21.00,20.50,20.25\n'''
    _, rows = load_price_guide_bytes(csv_bytes, filename="price_guide.csv")

    with db.SessionLocal() as session:
        normal = _seed_print(session, product_id="333", foil=False, number="3", variant="normal")
        session.commit()
        plan = build_import_plan(session, rows, as_of=datetime(2026, 8, 9, tzinfo=timezone.utc))
        snapshot = plan.snapshots[0]
        assert snapshot["entity_id"] == normal.id
        assert snapshot["price_low"] == Decimal("3.00")
        assert snapshot["price_mid"] == Decimal("7.50")
        assert snapshot["raw_json"]["low_ex_plus"] == "7.50"


def test_unmapped_ambiguous_duplicate_and_wrong_finish_are_never_priced(client):
    payload = b'''{"priceGuides":[
      {"idProduct": 444, "avg": 1, "low": 0.5, "trend": 0.8},
      {"idProduct": 444, "avg": 99, "low": 99, "trend": 99},
      {"idProduct": 555, "avg": 2, "low": 1, "trend": 1.5},
      {"idProduct": 666, "avg": 3, "low": 2, "trend": 2.5},
      {"idProduct": 777, "avg": 4, "low": 3, "trend": 3.5}
    ]}'''
    _, rows = load_price_guide_bytes(payload)

    with db.SessionLocal() as session:
        _seed_print(session, product_id="555", foil=False, number="5", variant="a")
        _seed_print(session, product_id="555", foil=True, number="6", variant="b")
        _seed_print(session, product_id="666", foil=True, number="7", variant="foil-needs-holo-data")
        _seed_print(session, product_id="444", foil=False, number="4", variant="normal")
        session.commit()

        plan = build_import_plan(session, rows, as_of=datetime(2026, 8, 9, tzinfo=timezone.utc))
        assert plan.duplicate_feed_rows == 1
        assert plan.ambiguous == 1
        assert plan.missing_finish_prices == 1
        assert plan.unmapped == 1
        assert plan.mapped_exact == 1
        assert [item["raw_json"]["idProduct"] for item in plan.snapshots] == ["444"]


def test_apply_is_idempotent_for_same_cardmarket_capture(client):
    payload = b'''{"createdAt":"2026-08-09T03:00:00Z","priceGuides":[
      {"idProduct": 888, "avg": 9, "low": 5, "trend": 8, "avg7": 8.5}
    ]}'''
    created_at, rows = load_price_guide_bytes(payload)

    with db.SessionLocal() as session:
        _seed_print(session, product_id="888", foil=False, number="8", variant="normal")
        session.commit()
        plan = build_import_plan(session, rows, as_of=created_at)
        first = apply_import_plan(session, plan)
        session.commit()
        second = apply_import_plan(session, plan)
        session.commit()

        assert first["inserted"] == 1 and first["updated"] == 0
        assert second["inserted"] == 0 and second["updated"] == 1
        assert session.execute(select(func.count()).select_from(PriceSnapshot)).scalar_one() == 1
        source = session.execute(select(PriceSource).where(PriceSource.name == "cardmarket")).scalar_one()
        assert source.currency == "EUR"
        snapshot = session.execute(select(PriceSnapshot)).scalar_one()
        assert snapshot.price_mid == Decimal("5.00")
        assert snapshot.price_market == Decimal("8.00")
