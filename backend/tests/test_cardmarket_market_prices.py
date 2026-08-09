from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app import db
from app.jobs.cardmarket_market_prices import (
    apply_market_import_plan,
    build_market_import_plan,
    validate_market_plan,
)
from app.jobs.cardmarket_prices import load_price_guide_bytes
from app.models import (
    Card,
    Game,
    PriceSnapshot,
    Print,
    PrintIdentifier,
    Product,
    ProductIdentifier,
    ProductVariant,
    Set,
)


def _game(session, slug: str):
    row = session.execute(select(Game).where(Game.slug == slug)).scalar_one_or_none()
    if row is None:
        row = Game(slug=slug, name=slug.upper())
        session.add(row)
        session.flush()
    return row


def _seed_print(session, *, game_slug: str, product_id: str, foil: bool = False):
    game = _game(session, game_slug)
    set_row = Set(game_id=game.id, code=f"T-{game_slug}", name="Test Set")
    session.add(set_row)
    session.flush()
    card = Card(game_id=game.id, name="Test Card", card_key=f"{game_slug}:card:{product_id}")
    session.add(card)
    session.flush()
    print_row = Print(
        set_id=set_row.id,
        card_id=card.id,
        collector_number=str(product_id),
        language="en",
        is_foil=foil,
        variant="foil" if foil else "default",
        print_key=f"{game_slug}:print:{product_id}",
    )
    session.add(print_row)
    session.flush()
    session.add(PrintIdentifier(print_id=print_row.id, source="cardmarket", external_id=product_id))
    session.flush()
    return print_row


def _seed_product(session, *, game_slug: str, product_id: str):
    game = _game(session, game_slug)
    product = Product(game_id=game.id, product_type="booster_box", name=f"Box {product_id}")
    session.add(product)
    session.flush()
    variant = ProductVariant(product_id=product.id, language="en", region="eu", packaging="sealed")
    session.add(variant)
    session.flush()
    session.add(ProductIdentifier(product_variant_id=variant.id, source="cardmarket", external_id=product_id))
    session.flush()
    return variant


def test_single_plan_maps_exact_print_and_preserves_cardmarket_fields(client):
    payload = b'''{"createdAt":"2026-08-09T03:00:00Z","priceGuides":[
      {"idProduct": 101, "avg": 9, "low": 4, "lowex": 6, "trend": 8, "avg7": 8.5}
    ]}'''
    created_at, rows = load_price_guide_bytes(payload)
    with db.SessionLocal() as session:
        print_row = _seed_print(session, game_slug="onepiece", product_id="101")
        session.commit()
        plan = build_market_import_plan(
            session,
            rows,
            game_slug="onepiece",
            product_group="single",
            as_of=created_at,
        )
        assert validate_market_plan(plan) == []
        assert plan.mapped_exact == 1
        snapshot = plan.snapshots[0]
        assert snapshot["entity_type"] == "print"
        assert snapshot["entity_id"] == print_row.id
        assert snapshot["price_low"] == Decimal("4.00")
        assert snapshot["price_mid"] == Decimal("6.00")
        assert snapshot["price_market"] == Decimal("8.00")
        assert snapshot["raw_json"]["product_group"] == "single"


def test_non_single_plan_maps_product_variant_and_can_be_applied(client):
    payload = b'''{"createdAt":"2026-08-09T03:00:00Z","priceGuides":[
      {"idProduct": 202, "avg": 130, "low": 115, "lowex": 120, "trend": 128, "avg30": 125}
    ]}'''
    created_at, rows = load_price_guide_bytes(payload)
    with db.SessionLocal() as session:
        variant = _seed_product(session, game_slug="pokemon", product_id="202")
        session.commit()
        plan = build_market_import_plan(
            session,
            rows,
            game_slug="pokemon",
            product_group="non_single",
            as_of=created_at,
        )
        assert plan.mapped_exact == 1
        snapshot = plan.snapshots[0]
        assert snapshot["entity_type"] == "product_variant"
        assert snapshot["entity_id"] == variant.id
        assert snapshot["price_mid"] == Decimal("120.00")
        assert snapshot["raw_json"]["product_group"] == "non_single"
        first = apply_market_import_plan(session, plan)
        session.commit()
        second = apply_market_import_plan(session, plan)
        session.commit()
        assert first["inserted"] == 1 and first["updated"] == 0
        assert second["inserted"] == 0 and second["updated"] == 1
        stored = session.execute(select(PriceSnapshot)).scalar_one()
        assert stored.entity_type == "product_variant"
        assert stored.entity_id == variant.id
        assert stored.price_market == Decimal("128.00")


def test_wrong_entity_and_cross_game_are_hard_blockers(client):
    payload = b'''{"createdAt":"2026-08-09T03:00:00Z","priceGuides":[
      {"idProduct": 303, "avg": 10, "low": 8, "trend": 9},
      {"idProduct": 404, "avg": 20, "low": 18, "trend": 19}
    ]}'''
    created_at, rows = load_price_guide_bytes(payload)
    with db.SessionLocal() as session:
        _seed_product(session, game_slug="onepiece", product_id="303")
        _seed_print(session, game_slug="mtg", product_id="404")
        session.commit()
        plan = build_market_import_plan(
            session,
            rows,
            game_slug="onepiece",
            product_group="single",
            as_of=created_at,
        )
        assert plan.wrong_entity_mappings == 1
        assert plan.cross_game_mappings == 1
        blockers = validate_market_plan(plan)
        assert "wrong_entity_mappings=1" in blockers
        assert "cross_game_mappings=1" in blockers
        with pytest.raises(ValueError, match="Refusing"):
            apply_market_import_plan(session, plan)


def test_entity_type_is_part_of_idempotency_key(client):
    payload_single = b'''{"priceGuides":[{"idProduct": 505, "avg": 5, "low": 4, "trend": 4.5}]}'''
    payload_product = b'''{"priceGuides":[{"idProduct": 606, "avg": 50, "low": 40, "trend": 45}]}'''
    _, rows_single = load_price_guide_bytes(payload_single)
    _, rows_product = load_price_guide_bytes(payload_product)
    as_of = datetime(2026, 8, 9, tzinfo=timezone.utc)
    with db.SessionLocal() as session:
        _seed_print(session, game_slug="yugioh", product_id="505")
        _seed_product(session, game_slug="yugioh", product_id="606")
        session.commit()
        single_plan = build_market_import_plan(session, rows_single, game_slug="yugioh", product_group="single", as_of=as_of)
        product_plan = build_market_import_plan(session, rows_product, game_slug="yugioh", product_group="non_single", as_of=as_of)
        apply_market_import_plan(session, single_plan)
        apply_market_import_plan(session, product_plan)
        session.commit()
        assert session.execute(select(func.count()).select_from(PriceSnapshot)).scalar_one() == 2
        kinds = set(session.execute(select(PriceSnapshot.entity_type)).scalars().all())
        assert kinds == {"print", "product_variant"}
