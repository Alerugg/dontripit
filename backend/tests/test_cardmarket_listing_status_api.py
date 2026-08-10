from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app import db
from app.external_catalog_models import (
    ExternalCatalogProduct,
    ExternalCatalogProductVariantLink,
    ExternalMarketPriceSnapshot,
)
from app.models import Game, Product, ProductImage, ProductVariant


NOW = datetime(2026, 8, 10, 12, 0, tzinfo=timezone.utc)


def test_market_catalog_separates_listing_observation_from_canonical_identity(client, monkeypatch):
    monkeypatch.setenv("PUBLIC_API_ENABLED", "true")
    with db.SessionLocal() as session:
        game = Game(slug="pokemon", name="Pokémon")
        session.add(game)
        session.flush()
        product = Product(game_id=game.id, name="Current Box", product_type="booster_box")
        session.add(product)
        session.flush()
        variant = ProductVariant(product_id=product.id, language="en", region="eu", packaging="sealed")
        session.add(variant)
        session.flush()
        session.add(ProductImage(
            product_variant_id=variant.id,
            url="https://images.example/current-box.jpg",
            is_primary=True,
            source="test",
        ))
        current = ExternalCatalogProduct(
            source="cardmarket",
            external_id="100",
            game_id=game.id,
            product_group="non_single",
            name="Current Box",
            category="Pokemon Display",
            last_seen_at=NOW,
        )
        stale = ExternalCatalogProduct(
            source="cardmarket",
            external_id="101",
            game_id=game.id,
            product_group="non_single",
            name="Old Collection",
            category="Pokemon Collection",
            last_seen_at=NOW - timedelta(days=1),
        )
        session.add_all([current, stale])
        session.flush()
        session.add(ExternalCatalogProductVariantLink(
            external_product_id=current.id,
            product_variant_id=variant.id,
            mapping_method="test_exact",
            confidence="exact",
            link_status="accepted",
            reviewed=True,
        ))
        session.add(ExternalMarketPriceSnapshot(
            external_product_id=current.id,
            currency="EUR",
            price_variant="sealed",
            price_low=95,
            price_mid=100,
            price_market=98,
            as_of=NOW,
        ))
        session.commit()
        product_id = int(product.id)

    response = client.get("/api/market/products?game=pokemon&group=non_single")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["total"] == 2
    by_id = {item["external_id"]: item for item in payload["items"]}
    assert by_id["100"]["listing_status"] == "available_verified"
    assert by_id["100"]["identity_status"] == "verified"
    assert by_id["100"]["canonical_product_id"] == product_id
    assert by_id["100"]["primary_image_url"].endswith("current-box.jpg")
    assert by_id["100"]["price_low"] == 95.0
    assert by_id["101"]["listing_status"] == "not_listed_latest_feed"
    assert by_id["101"]["identity_status"] == "unverified"
    assert by_id["101"]["canonical_product_id"] is None


def test_market_catalog_rejects_unknown_group(client, monkeypatch):
    monkeypatch.setenv("PUBLIC_API_ENABLED", "true")
    response = client.get("/api/market/products?group=anything")
    assert response.status_code == 400
    assert response.get_json()["error"] == "invalid_params"
