from __future__ import annotations

from datetime import datetime, timezone

from app import db
from app.external_catalog_models import ExternalCatalogProduct, ExternalCatalogProductVariantLink
from app.jobs.missing_image_report import build_missing_image_report
from app.models import Card, Game, Print, PrintImage, Product, ProductImage, ProductVariant, Set


NOW = datetime(2026, 8, 10, 12, 0, tzinfo=timezone.utc)


def test_missing_image_report_covers_canonical_and_current_external_gaps(client):
    with db.SessionLocal() as session:
        game = Game(slug="pokemon", name="Pokémon")
        session.add(game)
        session.flush()
        set_row = Set(game_id=game.id, code="TST", name="Test")
        session.add(set_row)
        session.flush()
        card = Card(game_id=game.id, name="Pikachu", card_key="pokemon:tst:1:pikachu")
        session.add(card)
        session.flush()
        missing_print = Print(
            card_id=card.id, set_id=set_row.id, collector_number="1", language="en",
            is_foil=False, variant="default", print_key="pokemon:tst:1:en:default",
        )
        imaged_print = Print(
            card_id=card.id, set_id=set_row.id, collector_number="2", language="en",
            is_foil=False, variant="default", print_key="pokemon:tst:2:en:default",
        )
        session.add_all([missing_print, imaged_print])
        session.flush()
        session.add(PrintImage(print_id=imaged_print.id, url="https://images.example/print.jpg", is_primary=False))

        missing_product = Product(game_id=game.id, name="Missing Box", product_type="booster_box")
        imaged_product = Product(game_id=game.id, name="Imaged Box", product_type="booster_box")
        session.add_all([missing_product, imaged_product])
        session.flush()
        missing_variant = ProductVariant(product_id=missing_product.id, language="en", region="eu", packaging="sealed")
        imaged_variant = ProductVariant(product_id=imaged_product.id, language="en", region="eu", packaging="sealed")
        session.add_all([missing_variant, imaged_variant])
        session.flush()
        session.add(ProductImage(product_variant_id=imaged_variant.id, url="https://images.example/box.jpg", is_primary=True))

        mapped_missing = ExternalCatalogProduct(
            source="cardmarket", external_id="100", game_id=game.id, product_group="non_single",
            name="Missing Box", category="Display", last_seen_at=NOW,
        )
        mapped_imaged = ExternalCatalogProduct(
            source="cardmarket", external_id="101", game_id=game.id, product_group="non_single",
            name="Imaged Box", category="Display", last_seen_at=NOW,
        )
        unmapped = ExternalCatalogProduct(
            source="cardmarket", external_id="102", game_id=game.id, product_group="non_single",
            name="Unknown Box", category="Display", last_seen_at=NOW,
        )
        session.add_all([mapped_missing, mapped_imaged, unmapped])
        session.flush()
        session.add_all([
            ExternalCatalogProductVariantLink(
                external_product_id=mapped_missing.id, product_variant_id=missing_variant.id,
                mapping_method="test", confidence="exact", link_status="accepted", reviewed=True,
            ),
            ExternalCatalogProductVariantLink(
                external_product_id=mapped_imaged.id, product_variant_id=imaged_variant.id,
                mapping_method="test", confidence="exact", link_status="accepted", reviewed=True,
            ),
        ])
        session.commit()

        report = build_missing_image_report(session)

    reasons = {(row["entity_type"], row["reason"], row.get("external_id")) for row in report.rows}
    assert ("print", "canonical_print_missing_image", None) in reasons
    assert ("product_variant", "canonical_product_variant_missing_image", None) in reasons
    assert ("external_product", "external_mapped_variant_missing_image", "100") in reasons
    assert ("external_product", "external_identity_unverified_image_unknown", "102") in reasons
    assert not any(row.get("external_id") == "101" for row in report.rows)
    assert report.summary()["entity_counts"] == {
        "external_product": 2,
        "print": 1,
        "product_variant": 1,
    }
