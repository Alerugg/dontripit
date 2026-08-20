from datetime import datetime, timezone

from sqlalchemy import select

from app import db
from app.external_catalog_models import ExternalCatalogProduct, ExternalCatalogProductVariantLink
from app.jobs.mtg_sealed_gap_closure import (
    CERTIFIED_GAPS,
    apply_mtg_sealed_gap_closure_plan,
    build_mtg_sealed_gap_closure_plan,
)
from app.models import Game, Product, ProductIdentifier, ProductVariant


def _game(session):
    row = session.execute(select(Game).where(Game.slug == "mtg")).scalar_one_or_none()
    if row is None:
        row = Game(slug="mtg", name="Magic: The Gathering")
        session.add(row)
        session.flush()
    return row


def _seed_current_targets(session, *, mutate_category: bool = False):
    game = _game(session)
    capture = datetime(2026, 8, 20, 4, 59, 19, tzinfo=timezone.utc)
    for index, spec in enumerate(CERTIFIED_GAPS):
        session.add(
            ExternalCatalogProduct(
                source="cardmarket",
                external_id=spec.external_id,
                game_id=game.id,
                product_group="non_single",
                name=spec.name,
                category_id=str(8 + index),
                category="Changed Category" if mutate_category and index == 0 else spec.category,
                expansion_external_id=spec.expansion_external_id,
                last_seen_at=capture,
            )
        )
    session.flush()


def test_four_current_gaps_create_exact_canonical_shapes_idempotently(client):
    with db.SessionLocal() as session:
        _seed_current_targets(session)
        plan = build_mtg_sealed_gap_closure_plan(session)
        assert plan.summary()["write_ready"] is True
        assert plan.summary()["safe_to_apply"] == 4
        assert {item.status for item in plan.decisions} == {"safe_create_product"}

        result = apply_mtg_sealed_gap_closure_plan(session, plan)
        assert result["created_products"] == 4
        assert result["created_variants"] == 4
        assert result["created_identifiers"] == 4
        assert result["created_links"] == 4
        session.flush()

        post = build_mtg_sealed_gap_closure_plan(session)
        assert post.summary()["already_closed"] == 4
        assert post.summary()["safe_to_apply"] == 0
        assert post.summary()["conflicts"] == 0

        for spec in CERTIFIED_GAPS:
            identifier = session.execute(
                select(ProductIdentifier).where(
                    ProductIdentifier.source == "cardmarket",
                    ProductIdentifier.external_id == spec.external_id,
                )
            ).scalar_one()
            variant = session.get(ProductVariant, identifier.product_variant_id)
            product = session.get(Product, variant.product_id)
            assert product.name == spec.name
            assert product.product_type == spec.product_type
            assert product.set_id is None
            assert variant.language == "und"
            assert variant.region == "global"
            assert variant.packaging == spec.expected_packaging()
            link = session.execute(
                select(ExternalCatalogProductVariantLink).where(
                    ExternalCatalogProductVariantLink.product_variant_id == variant.id
                )
            ).scalar_one()
            assert link.link_status == "accepted"
            assert link.confidence == "exact"


def test_changed_current_source_evidence_blocks_writes(client):
    with db.SessionLocal() as session:
        _seed_current_targets(session, mutate_category=True)
        plan = build_mtg_sealed_gap_closure_plan(session)
        assert plan.summary()["write_ready"] is False
        assert plan.summary()["conflicts"] == 1
        assert plan.decisions[0].status == "conflict_source_evidence_changed"
