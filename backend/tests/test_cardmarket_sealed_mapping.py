from __future__ import annotations

from sqlalchemy import func, select

from app import db
from app.external_catalog_models import ExternalCatalogProduct, ExternalCatalogProductVariantLink
from app.jobs.cardmarket_sealed_mapping import apply_sealed_mapping_plan, build_sealed_mapping_plan
from app.models import Game, Product, ProductIdentifier, ProductVariant


def _game(session, slug="pokemon"):
    game = Game(slug=slug, name=slug.upper())
    session.add(game)
    session.flush()
    return game


def _canonical(session, game, name="Test Booster Box", product_type="booster_box"):
    product = Product(game_id=game.id, name=name, product_type=product_type)
    session.add(product)
    session.flush()
    variant = ProductVariant(product_id=product.id, language="en", region="eu", packaging="sealed")
    session.add(variant)
    session.flush()
    return product, variant


def _external(session, game, external_id="100", name="Test Booster Box", category="Pokemon Display"):
    row = ExternalCatalogProduct(
        source="cardmarket",
        external_id=external_id,
        game_id=game.id,
        product_group="non_single",
        name=name,
        category=category,
    )
    session.add(row)
    session.flush()
    return row


def test_exact_unique_sealed_mapping_is_safe_and_idempotent(client):
    with db.SessionLocal() as session:
        game = _game(session)
        _, variant = _canonical(session, game)
        _external(session, game)
        session.commit()

        plan = build_sealed_mapping_plan(session)
        assert plan.summary()["status_counts"] == {"exact_candidate": 1}
        assert plan.decisions[0].product_variant_id == variant.id
        result = apply_sealed_mapping_plan(session, plan)
        session.commit()
        assert result["written"] == 1

        second = build_sealed_mapping_plan(session)
        assert second.summary()["status_counts"] == {"already_mapped": 1}
        assert apply_sealed_mapping_plan(session, second)["written"] == 0
        assert session.scalar(select(func.count()).select_from(ExternalCatalogProductVariantLink)) == 1


def test_legacy_identifier_is_verified_even_if_market_name_differs(client):
    with db.SessionLocal() as session:
        game = _game(session)
        _, variant = _canonical(session, game, name="Canonical Name")
        _external(session, game, external_id="200", name="Localized Market Name")
        session.add(ProductIdentifier(
            product_variant_id=variant.id,
            source="cardmarket",
            external_id="200",
        ))
        session.commit()

        plan = build_sealed_mapping_plan(session)
        assert plan.decisions[0].status == "identifier_verified"
        assert plan.decisions[0].product_variant_id == variant.id


def test_ambiguous_or_incompatible_identity_is_never_written(client):
    with db.SessionLocal() as session:
        game = _game(session)
        _canonical(session, game, product_type="unknown")
        _external(session, game, external_id="300")
        _external(session, game, external_id="301")
        session.commit()

        plan = build_sealed_mapping_plan(session)
        assert {item.status for item in plan.decisions} == {"ambiguous_external_name"}
        result = apply_sealed_mapping_plan(session, plan)
        assert result["written"] == 0


def test_missing_canonical_product_is_an_explicit_gap_not_not_listed(client):
    with db.SessionLocal() as session:
        game = _game(session, "onepiece")
        _external(session, game, external_id="400", name="OP-99 Booster Box", category="One Piece Display")
        session.commit()

        plan = build_sealed_mapping_plan(session)
        assert plan.decisions[0].status == "canonical_catalog_gap"
        assert plan.summary()["unresolved"] == 1
