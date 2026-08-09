from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select

from app import db
from app.models import Card, Game, PriceSnapshot, PriceSource, Print, Set
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


def _snapshot(session, *, print_id: int, source: str, as_of: datetime, low=None, mid=None, market=None, last=None, finish="nonfoil"):
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
        price_low=Decimal(str(low)) if low is not None else None,
        price_mid=Decimal(str(mid)) if mid is not None else None,
        price_market=Decimal(str(market)) if market is not None else None,
        price_last=Decimal(str(last)) if last is not None else None,
        raw_json={"finish": finish},
    )
    session.add(row)
    session.flush()
    return row


def test_cardmarket_conservative_value_beats_newer_non_cardmarket_trend(client):
    now = datetime.now(timezone.utc)
    with db.SessionLocal() as session:
        print_row = _seed_print(session)
        _snapshot(
            session,
            print_id=print_row.id,
            source="cardmarket",
            as_of=now - timedelta(hours=2),
            low="3.00",
            mid="7.50",
            market="10.00",
            last="9.00",
        )
        _snapshot(
            session,
            print_id=print_row.id,
            source="other-market",
            as_of=now,
            market="99.00",
        )
        session.commit()

        price = _latest_price(session, print_row.id)
        assert price["source"] == "cardmarket"
        assert price["minimum"] == 3.0
        assert price["conservative"] == 7.5
        assert price["trend"] == 10.0
        assert price["average"] == 9.0
        assert price["value"] == 7.5
        assert price["valuation_value"] == 7.5
        assert price["portfolio_method"] == "cardmarket_low_ex_plus_or_foil_low"


def test_non_cardmarket_snapshot_can_display_but_never_enters_conservative_portfolio(client):
    now = datetime.now(timezone.utc)
    with db.SessionLocal() as session:
        print_row = _seed_print(session)
        _snapshot(
            session,
            print_id=print_row.id,
            source="other-market",
            as_of=now,
            low="5.00",
            mid="6.00",
            market="8.00",
            last="7.00",
        )
        session.commit()

        price = _latest_price(session, print_row.id)
        assert price["source"] == "other-market"
        assert price["value"] == 8.0
        assert price["valuation_value"] is None
        assert price["conservative"] is None
        assert price["portfolio_method"] is None


def test_cardmarket_finish_is_preserved_for_user_facing_explanation(client):
    now = datetime.now(timezone.utc)
    with db.SessionLocal() as session:
        print_row = _seed_print(session)
        _snapshot(
            session,
            print_id=print_row.id,
            source="cardmarket",
            as_of=now,
            low="15.00",
            mid="15.00",
            market="19.00",
            last="20.00",
            finish="foil",
        )
        session.commit()

        price = _latest_price(session, print_row.id)
        assert price["finish"] == "foil"
        assert price["conservative"] == 15.0
