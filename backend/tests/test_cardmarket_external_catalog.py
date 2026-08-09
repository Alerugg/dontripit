from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app import db
from app.external_catalog_models import (
    ExternalCatalogPrintLink,
    ExternalCatalogProduct,
    ExternalMarketPriceSnapshot,
)
from app.jobs.cardmarket_catalog_audit import ProductListRow
from app.jobs.cardmarket_catalog_ingest import apply_catalog_ingest_plan, build_catalog_ingest_plan
from app.jobs.cardmarket_external_prices import apply_external_price_plan, build_external_price_plan
from app.jobs.cardmarket_master_inventory import CatalogFeed
from app.jobs.cardmarket_prices import CardmarketPriceRow
from app.models import Card, Game, Print, Set


NOW = datetime(2026, 8, 9, 20, 0, tzinfo=timezone.utc)
SOURCE_TIME = datetime(2026, 8, 9, 18, 0, tzinfo=timezone.utc)


def _game(session, slug: str):
    row = session.execute(select(Game).where(Game.slug == slug)).scalar_one_or_none()
    if row is None:
        row = Game(slug=slug, name=slug.upper())
        session.add(row)
        session.flush()
    return row


def _feed(game: str, group: str, *rows: ProductListRow, rejected: int = 0):
    return CatalogFeed(
        game_slug=game,
        product_group=group,
        rows=tuple(rows),
        created_at=SOURCE_TIME,
        raw_records=len(rows) + rejected,
        rejected_records=rejected,
    )


def _single(session, game, set_code="TEST", name="Pikachu", number="001", foil=False):
    set_row = session.execute(
        select(Set).where(Set.game_id == game.id, Set.code == set_code)
    ).scalar_one_or_none()
    if set_row is None:
        set_row = Set(game_id=game.id, code=set_code, name=set_code)
        session.add(set_row)
        session.flush()
    card = Card(game_id=game.id, name=name, card_key=f"{game.slug}:{set_code}:{number}:{foil}:{name}")
    session.add(card)
    session.flush()
    print_row = Print(
        set_id=set_row.id,
        card_id=card.id,
        collector_number=number,
        language="en",
        is_foil=foil,
        variant="default",
        print_key=f"{game.slug}:{set_code}:{number}:en:{foil}:default",
    )
    session.add(print_row)
    session.flush()
    return print_row


def test_catalog_ingest_persists_every_accepted_product_and_is_idempotent(client):
    rows = [
        ProductListRow("100", "Pikachu", "51", "Pokemon Single", "10", "2026-01-02", "900"),
        ProductListRow("101", "Elite Trainer Box", "53", "Pokemon Display", "", "2026-01-03", None),
    ]
    with db.SessionLocal() as session:
        _game(session, "pokemon")
        session.commit()

        feeds = [_feed("pokemon", "single", rows[0]), _feed("pokemon", "non_single", rows[1])]
        plan = build_catalog_ingest_plan(session, feeds, seen_at=NOW)
        assert plan.summary()["write_ready"] is True
        assert plan.inserts == 2
        assert plan.accepted_records == 2
        apply_catalog_ingest_plan(session, plan)
        session.commit()

        stored = session.execute(
            select(ExternalCatalogProduct).order_by(ExternalCatalogProduct.external_id)
        ).scalars().all()
        assert [row.external_id for row in stored] == ["100", "101"]
        assert stored[0].metacard_external_id == "900"
        assert stored[1].expansion_external_id is None
        assert stored[1].product_group == "non_single"

        second = build_catalog_ingest_plan(session, feeds, seen_at=NOW)
        assert second.inserts == 0
        assert second.updates == 0
        assert second.unchanged == 2
        apply_catalog_ingest_plan(session, second)
        session.commit()
        assert session.scalar(select(func.count()).select_from(ExternalCatalogProduct)) == 2


def test_catalog_ingest_refuses_rejected_source_rows(client):
    with db.SessionLocal() as session:
        _game(session, "pokemon")
        session.commit()
        plan = build_catalog_ingest_plan(
            session,
            [_feed("pokemon", "single", ProductListRow("200", "A", "51", "Pokemon Single", "10"), rejected=1)],
            seen_at=NOW,
        )
        assert plan.summary()["write_ready"] is False
        with pytest.raises(ValueError, match="rejected source rows"):
            apply_catalog_ingest_plan(session, plan)


def test_catalog_ingest_refuses_external_id_game_or_group_reassignment(client):
    with db.SessionLocal() as session:
        pokemon = _game(session, "pokemon")
        _game(session, "onepiece")
        session.add(ExternalCatalogProduct(
            source="cardmarket",
            external_id="300",
            game_id=pokemon.id,
            product_group="single",
            name="Old",
            last_seen_at=NOW,
        ))
        session.commit()

        plan = build_catalog_ingest_plan(
            session,
            [_feed("onepiece", "non_single", ProductListRow("300", "Changed", "", "", ""))],
            seen_at=NOW,
        )
        assert len(plan.conflicts) == 1
        assert plan.conflicts[0]["status"] == "immutable_identity_change"
        assert plan.summary()["write_ready"] is False
        with pytest.raises(ValueError, match="identity conflicts"):
            apply_catalog_ingest_plan(session, plan)


def test_one_external_cardmarket_product_can_link_to_multiple_canonical_prints(client):
    with db.SessionLocal() as session:
        game = _game(session, "pokemon")
        external = ExternalCatalogProduct(
            source="cardmarket",
            external_id="400",
            game_id=game.id,
            product_group="single",
            name="Pikachu",
            last_seen_at=NOW,
        )
        session.add(external)
        session.flush()
        nonfoil = _single(session, game, number="001", foil=False)
        foil = _single(session, game, number="002", foil=True)
        session.add_all([
            ExternalCatalogPrintLink(
                external_product_id=external.id,
                print_id=nonfoil.id,
                mapping_method="test",
                confidence="exact",
                link_status="confirmed",
                reviewed=True,
            ),
            ExternalCatalogPrintLink(
                external_product_id=external.id,
                print_id=foil.id,
                mapping_method="test",
                confidence="exact",
                link_status="confirmed",
                reviewed=True,
            ),
        ])
        session.commit()

        assert session.scalar(select(func.count()).select_from(ExternalCatalogPrintLink)) == 2


def test_price_plan_splits_single_nonfoil_and_foil_and_keeps_sealed_separate(client):
    with db.SessionLocal() as session:
        game = _game(session, "pokemon")
        session.add_all([
            ExternalCatalogProduct(
                source="cardmarket", external_id="500", game_id=game.id,
                product_group="single", name="Pikachu", last_seen_at=NOW,
            ),
            ExternalCatalogProduct(
                source="cardmarket", external_id="501", game_id=game.id,
                product_group="non_single", name="Booster Box", last_seen_at=NOW,
            ),
        ])
        session.commit()

        rows = [
            CardmarketPriceRow(
                product_id="500",
                avg=Decimal("12.00"), low=Decimal("9.00"), low_ex=Decimal("10.00"), trend=Decimal("11.00"),
                avg1=Decimal("11.50"), avg7=Decimal("11.25"), avg30=Decimal("10.75"),
                foil_avg=Decimal("30.00"), foil_low=Decimal("25.00"), foil_trend=Decimal("28.00"),
                foil_avg1=Decimal("29.00"), foil_avg7=Decimal("28.50"), foil_avg30=Decimal("27.50"),
            ),
            CardmarketPriceRow(
                product_id="501",
                avg=Decimal("100.00"), low=Decimal("85.00"), low_ex=Decimal("90.00"), trend=Decimal("95.00"),
            ),
        ]
        plan = build_external_price_plan(session, rows, as_of=NOW, game_slug="pokemon")
        assert plan.snapshot_count == 3
        assert plan.nonfoil_snapshots == 1
        assert plan.foil_snapshots == 1
        assert plan.sealed_snapshots == 1
        variants = {row["price_variant"]: row for row in plan.snapshots}
        assert variants["nonfoil"]["price_mid"] == Decimal("10.00")
        assert variants["foil"]["price_mid"] == Decimal("25.00")
        assert variants["sealed"]["price_mid"] == Decimal("85.00")

        apply_external_price_plan(session, plan)
        session.commit()
        assert session.scalar(select(func.count()).select_from(ExternalMarketPriceSnapshot)) == 3

        # Same Cardmarket timestamp is an upsert, not a duplicate historical row.
        apply_external_price_plan(session, plan)
        session.commit()
        assert session.scalar(select(func.count()).select_from(ExternalMarketPriceSnapshot)) == 3


def test_price_plan_detects_catalog_gap_before_canonical_projection(client):
    with db.SessionLocal() as session:
        _game(session, "pokemon")
        session.commit()
        plan = build_external_price_plan(
            session,
            [CardmarketPriceRow(product_id="missing", low=Decimal("1.00"))],
            as_of=NOW,
            game_slug="pokemon",
        )
    assert plan.matched_external_products == 0
    assert plan.missing_external_products == 1
    assert plan.snapshot_count == 0
