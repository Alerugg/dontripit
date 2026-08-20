from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select

from app import db
from app.models import Card, Game, PriceSnapshot, PriceSource, Print, Set
from app.routes import user_library
from app.routes.user_library import _latest_price


def _seed_print(session) -> Print:
    game = Game(slug="pokemon", name="Pokémon")
    session.add(game)
    session.flush()
    set_row = Set(game_id=game.id, code="TST", name="Test Set")
    session.add(set_row)
    session.flush()
    card = Card(game_id=game.id, name="Test Card", card_key="pokemon:test-card")
    session.add(card)
    session.flush()
    print_row = Print(
        set_id=set_row.id,
        card_id=card.id,
        collector_number="001",
        language="en",
        is_foil=False,
        variant="default",
        print_key="pokemon:test:001:en:default",
    )
    session.add(print_row)
    session.flush()
    return print_row


def _legacy_snapshot(session, *, print_id: int, source: str, as_of: datetime, market=None):
    source_row = session.execute(select(PriceSource).where(PriceSource.name == source)).scalar_one_or_none()
    if source_row is None:
        source_row = PriceSource(name=source, currency="EUR")
        session.add(source_row)
        session.flush()
    row = PriceSnapshot(
        entity_type="print",
        entity_id=print_id,
        source_id=source_row.id,
        currency="EUR",
        as_of=as_of,
        price_market=Decimal(str(market)) if market is not None else None,
        raw_json={"finish": "nonfoil"},
    )
    session.add(row)
    session.flush()
    return row


def _install_exact_projection(monkeypatch, *, print_id: int, finish: str = "nonfoil"):
    monkeypatch.setattr(user_library, "_load_print_market_rows", lambda session, print_ids: [{"print_id": print_id}])
    monkeypatch.setattr(
        user_library,
        "_build_print_market_payloads",
        lambda rows, print_ids: {
            print_id: {
                "status": "priced",
                "price": {
                    "minimum": 3.0,
                    "conservative": 7.5,
                    "trend": 10.0,
                    "average": 9.0,
                    "currency": "EUR",
                    "as_of": "2026-08-20T00:00:00+00:00",
                    "finish": finish,
                },
            }
        },
    )


def test_current_exact_cardmarket_projection_drives_conservative_portfolio(monkeypatch):
    _install_exact_projection(monkeypatch, print_id=101)

    price = _latest_price(object(), 101)

    assert price["source"] == "cardmarket"
    assert price["minimum"] == 3.0
    assert price["conservative"] == 7.5
    assert price["trend"] == 10.0
    assert price["average"] == 9.0
    assert price["value"] == 7.5
    assert price["valuation_value"] == 7.5
    assert price["portfolio_method"] == "cardmarket_low_ex_plus_or_foil_low"


def test_legacy_or_non_cardmarket_snapshot_never_falls_back_into_current_exact_portfolio(client):
    now = datetime.now(timezone.utc)
    with db.SessionLocal() as session:
        print_row = _seed_print(session)
        _legacy_snapshot(
            session,
            print_id=print_row.id,
            source="other-market",
            as_of=now,
            market="99.00",
        )
        session.commit()

        # _latest_price deliberately reads only the current exact Cardmarket
        # projection. Legacy PriceSnapshot rows and sibling markets are ignored.
        assert _latest_price(session, print_row.id) is None


def test_current_exact_cardmarket_finish_is_preserved_for_user_facing_explanation(monkeypatch):
    _install_exact_projection(monkeypatch, print_id=202, finish="foil")

    price = _latest_price(object(), 202)

    assert price["finish"] == "foil"
    assert price["conservative"] == 7.5
    assert price["source"] == "cardmarket"
