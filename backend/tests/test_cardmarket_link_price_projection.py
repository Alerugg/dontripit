from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select

from app import db
from app.external_catalog_models import (
    ExternalCatalogPrintLink,
    ExternalCatalogProduct,
    ExternalMarketPriceSnapshot,
)
from app.jobs.cardmarket_link_price_projection import (
    apply_link_price_projection_plan,
    build_link_price_projection_plan,
)
from app.models import Card, Game, PriceSnapshot, Print, Set


def _seed_print(session, *, variant="nonfoil", is_foil=False, number="1"):
    game = session.execute(select(Game).where(Game.slug == "mtg")).scalar_one_or_none()
    if game is None:
        game = Game(slug="mtg", name="Magic")
        session.add(game)
        session.flush()
    set_row = session.execute(select(Set).where(Set.game_id == game.id, Set.code == "tst")).scalar_one_or_none()
    if set_row is None:
        set_row = Set(game_id=game.id, code="tst", name="Test")
        session.add(set_row)
        session.flush()
    card = Card(game_id=game.id, name=f"Card {number}", card_key=f"mtg:test:{number}")
    session.add(card)
    session.flush()
    print_row = Print(
        set_id=set_row.id,
        card_id=card.id,
        collector_number=number,
        language="en",
        is_foil=is_foil,
        variant=variant,
        print_key=f"mtg:test:{number}:{variant}",
    )
    session.add(print_row)
    session.flush()
    return game, print_row


def _seed_external(session, game, *, product_id="100", price_variant="nonfoil", low="2.00", trend="3.00"):
    external = ExternalCatalogProduct(
        source="cardmarket",
        external_id=product_id,
        game_id=game.id,
        product_group="single",
        name=f"CM {product_id}",
        website_path=f"/Magic/Products?idProduct={product_id}",
    )
    session.add(external)
    session.flush()
    as_of = datetime(2026, 8, 9, 2, 0, tzinfo=timezone.utc)
    session.add(
        ExternalMarketPriceSnapshot(
            external_product_id=external.id,
            currency="EUR",
            price_variant=price_variant,
            price_low=Decimal(low),
            price_mid=Decimal(low),
            price_market=Decimal(trend),
            price_last=Decimal("2.50"),
            as_of=as_of,
        )
    )
    session.flush()
    return external, as_of


def _link(session, external, print_row):
    session.add(
        ExternalCatalogPrintLink(
            external_product_id=external.id,
            print_id=print_row.id,
            mapping_method="scryfall_cardmarket_id",
            confidence="exact",
            link_status="accepted",
            reviewed=False,
            evidence={"source": "scryfall", "cardmarket_id": external.external_id},
        )
    )
    session.flush()


def test_projects_accepted_nonfoil_link_and_preserves_provenance(client):
    with db.SessionLocal() as session:
        game, print_row = _seed_print(session)
        external, as_of = _seed_external(session, game)
        _link(session, external, print_row)
        session.commit()

        plan = build_link_price_projection_plan(session, game_slug="mtg")
        assert plan.summary()["write_ready"] is True
        assert plan.as_of == as_of
        assert plan.priceable_prints == 1
        assert plan.unsupported_finish == 0
        snapshot = plan.snapshots[0]
        assert snapshot["entity_id"] == print_row.id
        assert snapshot["price_low"] == Decimal("2.00")
        assert snapshot["price_market"] == Decimal("3.00")
        assert snapshot["raw_json"]["idProduct"] == "100"
        assert snapshot["raw_json"]["website_path"] == "/Magic/Products?idProduct=100"
        assert snapshot["raw_json"]["mapping_method"] == "scryfall_cardmarket_id"

        result = apply_link_price_projection_plan(session, plan)
        session.commit()
        assert result["inserted"] == 1
        saved = session.execute(select(PriceSnapshot)).scalar_one()
        assert saved.entity_type == "print"
        assert saved.entity_id == print_row.id
        assert saved.raw_json["idProduct"] == "100"


def test_foil_uses_foil_external_variant(client):
    with db.SessionLocal() as session:
        game, print_row = _seed_print(session, variant="foil", is_foil=True)
        external, _ = _seed_external(session, game, product_id="101", price_variant="foil", low="9.00", trend="10.00")
        _link(session, external, print_row)
        session.commit()

        plan = build_link_price_projection_plan(session, game_slug="mtg")
        assert plan.priceable_prints == 1
        assert plan.snapshots[0]["price_low"] == Decimal("9.00")
        assert plan.snapshots[0]["raw_json"]["price_variant"] == "foil"


def test_etched_finish_is_never_guessed_from_foil_price(client):
    with db.SessionLocal() as session:
        game, print_row = _seed_print(session, variant="etched", is_foil=True)
        external, _ = _seed_external(session, game, product_id="102", price_variant="foil")
        _link(session, external, print_row)
        session.commit()

        plan = build_link_price_projection_plan(session, game_slug="mtg")
        assert plan.unsupported_finish == 1
        assert plan.priceable_prints == 0
        assert plan.snapshots == ()


def test_multiple_cardmarket_products_claiming_one_print_is_blocked(client):
    with db.SessionLocal() as session:
        game, print_row = _seed_print(session)
        external_a, _ = _seed_external(session, game, product_id="201")
        external_b, _ = _seed_external(session, game, product_id="202")
        _link(session, external_a, print_row)
        _link(session, external_b, print_row)
        session.commit()

        plan = build_link_price_projection_plan(session, game_slug="mtg")
        assert plan.ambiguous_print_links == 1
        assert plan.summary()["write_ready"] is False
        with pytest.raises(ValueError, match="ambiguous"):
            apply_link_price_projection_plan(session, plan)
