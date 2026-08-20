from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select

from app import db
from app.external_catalog_models import (
    ExternalCatalogProduct,
    ExternalCatalogProductVariantLink,
    ExternalMarketPriceSnapshot,
)
from app.jobs.cardmarket_product_link_price_projection import (
    apply_product_link_price_projection_plan,
    build_product_link_price_projection_plan,
)
from app.models import Game, PriceSnapshot, Product, ProductVariant


def _game(session, slug="pokemon"):
    game = session.execute(select(Game).where(Game.slug == slug)).scalar_one_or_none()
    if game is None:
        game = Game(slug=slug, name=slug.upper())
        session.add(game)
        session.flush()
    return game


def _variant(session, game, *, name="Box"):
    product = Product(game_id=game.id, product_type="booster_box", name=name)
    session.add(product)
    session.flush()
    variant = ProductVariant(product_id=product.id, language="en", region="eu", packaging="sealed")
    session.add(variant)
    session.flush()
    return variant


def _external(session, game, *, product_id, captured_at, as_of, low="100.00"):
    external = ExternalCatalogProduct(
        source="cardmarket",
        external_id=str(product_id),
        game_id=game.id,
        product_group="non_single",
        name=f"CM {product_id}",
        website_path=f"/Products?idProduct={product_id}",
        last_seen_at=captured_at,
    )
    session.add(external)
    session.flush()
    session.add(
        ExternalMarketPriceSnapshot(
            external_product_id=external.id,
            currency="EUR",
            price_variant="sealed",
            price_low=Decimal(low),
            price_mid=Decimal(low),
            price_market=Decimal("110.00"),
            price_last=Decimal("105.00"),
            as_of=as_of,
        )
    )
    session.flush()
    return external


def _link(session, external, variant, *, status="accepted"):
    session.add(
        ExternalCatalogProductVariantLink(
            external_product_id=external.id,
            product_variant_id=variant.id,
            mapping_method="cardmarket_exact_product",
            confidence="exact",
            link_status=status,
            reviewed=True,
            evidence={"idProduct": external.external_id},
        )
    )
    session.flush()


def test_projects_current_accepted_sealed_link_and_is_idempotent(client):
    captured = datetime(2026, 8, 20, 4, tzinfo=timezone.utc)
    as_of = datetime(2026, 8, 17, 0, tzinfo=timezone.utc)
    with db.SessionLocal() as session:
        game = _game(session)
        variant = _variant(session, game)
        external = _external(session, game, product_id="202", captured_at=captured, as_of=as_of)
        _link(session, external, variant)
        session.commit()

        plan = build_product_link_price_projection_plan(session, game_slug="pokemon")
        assert plan.summary()["write_ready"] is True
        assert plan.catalog_capture == captured
        assert plan.as_of == as_of
        assert plan.accepted_links == 1
        assert plan.canonical_variants == 1
        assert plan.priceable_variants == 1
        snapshot = plan.snapshots[0]
        assert snapshot["entity_type"] == "product_variant"
        assert snapshot["entity_id"] == variant.id
        assert snapshot["raw_json"]["idProduct"] == "202"
        assert snapshot["raw_json"]["mapping_method"] == "cardmarket_exact_product"

        first = apply_product_link_price_projection_plan(session, plan)
        session.commit()
        second = apply_product_link_price_projection_plan(session, plan)
        session.commit()
        assert first["inserted"] == 1 and first["updated"] == 0
        assert second["inserted"] == 0 and second["updated"] == 1
        stored = session.execute(select(PriceSnapshot)).scalar_one()
        assert stored.entity_type == "product_variant"
        assert stored.entity_id == variant.id
        assert stored.raw_json["idProduct"] == "202"


def test_candidate_link_is_not_priceable_identity(client):
    captured = datetime(2026, 8, 20, 4, tzinfo=timezone.utc)
    as_of = datetime(2026, 8, 17, tzinfo=timezone.utc)
    with db.SessionLocal() as session:
        game = _game(session)
        variant = _variant(session, game)
        external = _external(session, game, product_id="303", captured_at=captured, as_of=as_of)
        _link(session, external, variant, status="candidate")
        session.commit()
        plan = build_product_link_price_projection_plan(session, game_slug="pokemon")
        assert plan.accepted_links == 0
        assert plan.canonical_variants == 0
        assert plan.snapshots == ()


def test_multiple_current_products_claiming_one_variant_is_blocked(client):
    captured = datetime(2026, 8, 20, 4, tzinfo=timezone.utc)
    as_of = datetime(2026, 8, 17, tzinfo=timezone.utc)
    with db.SessionLocal() as session:
        game = _game(session)
        variant = _variant(session, game)
        for product_id in ("401", "402"):
            external = _external(session, game, product_id=product_id, captured_at=captured, as_of=as_of)
            _link(session, external, variant)
        session.commit()
        plan = build_product_link_price_projection_plan(session, game_slug="pokemon")
        assert plan.ambiguous_variant_links == 1
        assert plan.summary()["write_ready"] is False
        with pytest.raises(ValueError, match="ambiguous"):
            apply_product_link_price_projection_plan(session, plan)


def test_stale_catalog_link_is_not_projected(client):
    captured = datetime(2026, 8, 20, 4, tzinfo=timezone.utc)
    as_of = datetime(2026, 8, 17, tzinfo=timezone.utc)
    with db.SessionLocal() as session:
        game = _game(session)
        stale_variant = _variant(session, game, name="Old")
        stale = _external(
            session,
            game,
            product_id="501",
            captured_at=captured - timedelta(days=1),
            as_of=as_of,
        )
        _link(session, stale, stale_variant)
        _external(session, game, product_id="502", captured_at=captured, as_of=as_of)
        session.commit()

        plan = build_product_link_price_projection_plan(session, game_slug="pokemon")
        assert plan.catalog_capture == captured
        assert plan.accepted_links == 0
        assert plan.canonical_variants == 0
        assert plan.snapshots == ()
