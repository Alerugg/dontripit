from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass

from sqlalchemy import func, select

from app.external_catalog_models import ExternalCatalogProduct, ExternalCatalogProductVariantLink
from app.models import (
    Card,
    Game,
    Print,
    PrintImage,
    Product,
    ProductImage,
    ProductVariant,
    Set,
)


_ACCEPTED_LINK_STATUSES = {"accepted", "mapped", "exact"}


@dataclass(frozen=True)
class MissingImageReport:
    rows: tuple[dict, ...]

    def summary(self) -> dict:
        entity_counts = Counter(row["entity_type"] for row in self.rows)
        reason_counts = Counter(row["reason"] for row in self.rows)
        game_counts = Counter(row["game"] for row in self.rows)
        return {
            "missing_verified_images": len(self.rows),
            "entity_counts": dict(sorted(entity_counts.items())),
            "reason_counts": dict(sorted(reason_counts.items())),
            "game_counts": dict(sorted(game_counts.items())),
            "scope": {
                "prints": "Every canonical Print without any stored PrintImage",
                "product_variants": "Every canonical ProductVariant without any stored ProductImage",
                "external_products": (
                    "Current Cardmarket non-single listings without one accepted canonical variant "
                    "that has a stored image"
                ),
            },
        }


def build_missing_image_report(session) -> MissingImageReport:
    """Return a deterministic upload worklist without treating guesses as images."""
    rows: list[dict] = []

    print_rows = session.execute(
        select(
            Print.id,
            Game.slug,
            Card.name,
            Set.code,
            Set.name,
            Print.collector_number,
            Print.language,
            Print.is_foil,
            Print.variant,
        )
        .join(Card, Card.id == Print.card_id)
        .join(Set, Set.id == Print.set_id)
        .join(Game, Game.id == Card.game_id)
        .where(~select(PrintImage.id).where(PrintImage.print_id == Print.id).exists())
        .order_by(Game.slug, Set.code, Print.collector_number, Print.id)
    ).all()
    for print_id, game, card_name, set_code, set_name, collector, language, is_foil, variant in print_rows:
        rows.append({
            "entity_type": "print",
            "entity_id": int(print_id),
            "game": str(game),
            "label": str(card_name),
            "reason": "canonical_print_missing_image",
            "set_code": set_code,
            "set_name": set_name,
            "collector_number": collector,
            "language": language,
            "finish": "foil" if is_foil else "nonfoil",
            "variant": variant,
            "external_id": None,
            "category": None,
            "canonical_product_variant_id": None,
        })

    variant_rows = session.execute(
        select(
            ProductVariant.id,
            Game.slug,
            Product.name,
            Product.product_type,
            Set.code,
            ProductVariant.language,
            ProductVariant.region,
            ProductVariant.packaging,
        )
        .join(Product, Product.id == ProductVariant.product_id)
        .join(Game, Game.id == Product.game_id)
        .join(Set, Set.id == Product.set_id, isouter=True)
        .where(~select(ProductImage.id).where(ProductImage.product_variant_id == ProductVariant.id).exists())
        .order_by(Game.slug, Product.name, ProductVariant.id)
    ).all()
    for variant_id, game, product_name, product_type, set_code, language, region, packaging in variant_rows:
        rows.append({
            "entity_type": "product_variant",
            "entity_id": int(variant_id),
            "game": str(game),
            "label": str(product_name),
            "reason": "canonical_product_variant_missing_image",
            "set_code": set_code,
            "set_name": None,
            "collector_number": None,
            "language": language,
            "region": region,
            "finish": None,
            "variant": packaging,
            "product_type": product_type,
            "external_id": None,
            "category": None,
            "canonical_product_variant_id": int(variant_id),
        })

    latest_by_game = dict(session.execute(
        select(ExternalCatalogProduct.game_id, func.max(ExternalCatalogProduct.last_seen_at))
        .where(
            ExternalCatalogProduct.source == "cardmarket",
            ExternalCatalogProduct.product_group == "non_single",
        )
        .group_by(ExternalCatalogProduct.game_id)
    ).all())
    current_external = session.execute(
        select(ExternalCatalogProduct, Game.slug)
        .join(Game, Game.id == ExternalCatalogProduct.game_id)
        .where(
            ExternalCatalogProduct.source == "cardmarket",
            ExternalCatalogProduct.product_group == "non_single",
        )
        .order_by(Game.slug, ExternalCatalogProduct.name, ExternalCatalogProduct.external_id)
    ).all()
    accepted_link_rows = session.execute(
        select(
            ExternalCatalogProductVariantLink.external_product_id,
            ExternalCatalogProductVariantLink.product_variant_id,
        ).where(ExternalCatalogProductVariantLink.link_status.in_(_ACCEPTED_LINK_STATUSES))
    ).all()
    accepted_by_external: dict[int, set[int]] = defaultdict(set)
    for external_product_id, variant_id in accepted_link_rows:
        accepted_by_external[int(external_product_id)].add(int(variant_id))
    variants_with_images = set(session.execute(select(ProductImage.product_variant_id).distinct()).scalars().all())

    for external, game in current_external:
        latest_seen = latest_by_game.get(int(external.game_id))
        if latest_seen is None or external.last_seen_at != latest_seen:
            continue
        accepted_variants = accepted_by_external.get(int(external.id), set())
        if len(accepted_variants) == 1 and next(iter(accepted_variants)) in variants_with_images:
            continue
        if not accepted_variants:
            reason = "external_identity_unverified_image_unknown"
        elif len(accepted_variants) > 1:
            reason = "external_identity_ambiguous_image_unknown"
        else:
            reason = "external_mapped_variant_missing_image"
        rows.append({
            "entity_type": "external_product",
            "entity_id": int(external.id),
            "game": str(game),
            "label": str(external.name),
            "reason": reason,
            "set_code": None,
            "set_name": None,
            "collector_number": None,
            "language": None,
            "region": None,
            "finish": None,
            "variant": None,
            "external_id": str(external.external_id),
            "category": external.category,
            "canonical_product_variant_id": (
                next(iter(accepted_variants)) if len(accepted_variants) == 1 else None
            ),
        })

    rows.sort(key=lambda row: (
        row["game"],
        row["entity_type"],
        str(row["label"]).casefold(),
        str(row["entity_id"]),
    ))
    return MissingImageReport(tuple(rows))
