from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import re

from sqlalchemy import func, select

from app.external_catalog_models import (
    ExternalCatalogProduct,
    ExternalCatalogProductVariantLink,
    ExternalMarketPriceSnapshot,
)
from app.models import (
    Game,
    PriceSnapshot,
    PriceSource,
    Product,
    ProductIdentifier,
    ProductVariant,
)

CARDMARKET_SOURCE = "cardmarket"
SUPPORTED_GAMES = {"pokemon", "onepiece", "mtg", "yugioh"}

# Product taxonomy is deliberately commercial, not inferred card identity.
# Values in SEALED_TYPES are safe to expose under a future `sealed` filter.
CATEGORY_TYPES = {
    "MtG Set": "set_product",
    "Magic Intropack": "intro_pack",
    "Magic Booster": "booster_pack",
    "Magic Display": "booster_box",
    "Magic Theme Deck Display": "deck_display",
    "Magic TournamentPack": "tournament_pack",
    "Magic Fatpack": "fatpack",
    "Magic Starter Deck": "starter_deck",
    "Magic Event Tickets": "event_ticket",
    "Magic Lot": "lot",
    "Magic Miscellaneous": "miscellaneous",
    "One Piece Booster": "booster_pack",
    "One Piece Lots": "lot",
    "One Piece Promo Products": "promo_product",
    "One Piece Preconstructed Decks": "preconstructed_deck",
    "One Piece Booster Boxes": "booster_box",
    "Pokémon Box Set": "box_set",
    "Pokémon Booster": "booster_pack",
    "Pokémon Coins": "coin",
    "Pokémon Display": "booster_box",
    "Pokémon Tins": "tin",
    "Pokémon Theme Deck": "theme_deck",
    "Pokémon Blisters": "blister",
    "Pokémon Elite Trainer Boxes": "elite_trainer_box",
    "Pokémon Lot": "lot",
    "PCG Set": "set_product",
    "Pokémon Trainer Kits": "trainer_kit",
    "Pokémon Pokémon Sets": "set_product",
    "Yugioh Booster": "booster_pack",
    "Yugioh Display": "booster_box",
    "Yugioh Structure Deck": "structure_deck",
    "Yugioh Special Edition": "special_edition",
    "Yugioh Promo Products": "promo_product",
    "Yugioh Collector Tins": "collector_tin",
    "Yugioh Starter Deck": "starter_deck",
    "Yugioh Lot": "lot",
    "Yugioh Event Tickets": "event_ticket",
}

SEALED_TYPES = {
    "intro_pack", "booster_pack", "booster_box", "deck_display", "tournament_pack",
    "fatpack", "starter_deck", "promo_product", "preconstructed_deck", "box_set",
    "tin", "theme_deck", "blister", "elite_trainer_box", "trainer_kit",
    "structure_deck", "special_edition", "collector_tin",
}


def _slug(value: str | None) -> str:
    value = re.sub(r"[^a-z0-9]+", "_", str(value or "").casefold()).strip("_")
    return value[:100] or "market_product"


def _product_type(category: str | None) -> str:
    return CATEGORY_TYPES.get(str(category or ""), _slug(category))


def _dimensions(name: str) -> tuple[str, str]:
    folded = str(name or "").casefold()
    if "chinese edition" in folded or "chinese version" in folded:
        return "zh", "cn"
    if "japanese" in folded:
        return "ja", "jp"
    if "asia region legal" in folded or "asian english" in folded:
        return "und", "asia"
    if "us version" in folded or "usa version" in folded:
        return "und", "us"
    if "(ocg)" in folded:
        return "und", "ocg"
    return "und", "global"


def _packaging(category: str | None, product_type: str) -> str:
    return product_type[:100]


@dataclass(frozen=True)
class ProductBootstrapResult:
    capture: datetime
    current_products: int
    created_products: int
    reused_products: int
    accepted_links_created: int
    accepted_links_reused: int
    sealed_products: int
    nonsealed_market_products: int

    def summary(self) -> dict:
        return {
            "capture": self.capture.isoformat(),
            "current_products": self.current_products,
            "created_products": self.created_products,
            "reused_products": self.reused_products,
            "accepted_links_created": self.accepted_links_created,
            "accepted_links_reused": self.accepted_links_reused,
            "sealed_products": self.sealed_products,
            "nonsealed_market_products": self.nonsealed_market_products,
        }


def bootstrap_cardmarket_product_catalog(session) -> ProductBootstrapResult:
    capture = session.execute(
        select(func.max(ExternalCatalogProduct.last_seen_at)).where(
            ExternalCatalogProduct.source == CARDMARKET_SOURCE
        )
    ).scalar_one()
    if capture is None:
        raise ValueError("Cardmarket external catalog is empty")

    rows = session.execute(
        select(ExternalCatalogProduct, Game.slug)
        .join(Game, Game.id == ExternalCatalogProduct.game_id)
        .where(
            ExternalCatalogProduct.source == CARDMARKET_SOURCE,
            ExternalCatalogProduct.last_seen_at == capture,
            ExternalCatalogProduct.product_group == "non_single",
            Game.slug.in_(SUPPORTED_GAMES),
        )
        .order_by(ExternalCatalogProduct.id)
    ).all()

    existing_identifiers = {
        str(external_id): (int(variant_id), int(game_id))
        for external_id, variant_id, game_id in session.execute(
            select(ProductIdentifier.external_id, ProductVariant.id, Product.game_id)
            .join(ProductVariant, ProductVariant.id == ProductIdentifier.product_variant_id)
            .join(Product, Product.id == ProductVariant.product_id)
            .where(ProductIdentifier.source == CARDMARKET_SOURCE)
        ).all()
    }
    existing_links: dict[int, list[int]] = {}
    for external_product_id, variant_id in session.execute(
        select(
            ExternalCatalogProductVariantLink.external_product_id,
            ExternalCatalogProductVariantLink.product_variant_id,
        ).where(ExternalCatalogProductVariantLink.link_status == "accepted")
    ).all():
        existing_links.setdefault(int(external_product_id), []).append(int(variant_id))

    created = reused = links_created = links_reused = sealed = nonsealed = 0
    for external, game_slug in rows:
        ptype = _product_type(external.category)
        if ptype in SEALED_TYPES:
            sealed += 1
        else:
            nonsealed += 1

        found = existing_identifiers.get(str(external.external_id))
        if found is not None:
            variant_id, game_id = found
            if game_id != int(external.game_id):
                raise ValueError(
                    f"Cross-game Cardmarket product identifier {external.external_id}: "
                    f"canonical game={game_id} external game={external.game_id}"
                )
            reused += 1
        else:
            language, region = _dimensions(external.name)
            product = Product(
                game_id=external.game_id,
                set_id=None,
                product_type=ptype,
                name=str(external.name),
                # date_added is Cardmarket catalog metadata, not a release date.
                release_date=None,
            )
            session.add(product)
            session.flush()
            variant = ProductVariant(
                product_id=product.id,
                language=language,
                region=region,
                packaging=_packaging(external.category, ptype),
                sku=f"cardmarket:{external.external_id}"[:100],
            )
            session.add(variant)
            session.flush()
            session.add(
                ProductIdentifier(
                    product_variant_id=variant.id,
                    source=CARDMARKET_SOURCE,
                    external_id=str(external.external_id),
                )
            )
            session.flush()
            variant_id = int(variant.id)
            existing_identifiers[str(external.external_id)] = (variant_id, int(external.game_id))
            created += 1

        current_links = existing_links.get(int(external.id), [])
        wrong = [value for value in current_links if value != variant_id]
        if wrong:
            raise ValueError(
                f"Cardmarket external product {external.external_id} already has conflicting "
                f"accepted ProductVariant links: {wrong} expected={variant_id}"
            )
        if variant_id in current_links:
            links_reused += 1
        else:
            session.add(
                ExternalCatalogProductVariantLink(
                    external_product_id=external.id,
                    product_variant_id=variant_id,
                    mapping_method="cardmarket_product_identity",
                    confidence="exact",
                    link_status="accepted",
                    reviewed=True,
                    evidence={
                        "source": CARDMARKET_SOURCE,
                        "idProduct": str(external.external_id),
                        "category": external.category,
                        "product_type": ptype,
                        "catalog_scope": "sealed" if ptype in SEALED_TYPES else "nonsealed_market_product",
                        "idExpansion": external.expansion_external_id,
                        "date_added": external.date_added.isoformat() if external.date_added else None,
                        "website_path": external.website_path,
                        "identity_policy": "1:1 source commercial product; no name inference",
                    },
                )
            )
            existing_links.setdefault(int(external.id), []).append(variant_id)
            links_created += 1

    session.flush()
    return ProductBootstrapResult(
        capture=capture,
        current_products=len(rows),
        created_products=created,
        reused_products=reused,
        accepted_links_created=links_created,
        accepted_links_reused=links_reused,
        sealed_products=sealed,
        nonsealed_market_products=nonsealed,
    )


@dataclass(frozen=True)
class ProductPriceProjectionResult:
    linked_products: int
    priceable_products: int
    missing_price_products: int
    ambiguous_price_variants: int
    snapshots: int
    as_of_min: datetime | None
    as_of_max: datetime | None

    def summary(self) -> dict:
        return {
            "linked_products": self.linked_products,
            "priceable_products": self.priceable_products,
            "missing_price_products": self.missing_price_products,
            "ambiguous_price_variants": self.ambiguous_price_variants,
            "snapshots": self.snapshots,
            "as_of_min": self.as_of_min.isoformat() if self.as_of_min else None,
            "as_of_max": self.as_of_max.isoformat() if self.as_of_max else None,
        }


def project_cardmarket_product_prices(session) -> ProductPriceProjectionResult:
    linked = session.execute(
        select(
            ExternalCatalogProduct.id,
            ExternalCatalogProduct.external_id,
            ExternalCatalogProduct.game_id,
            ExternalCatalogProductVariantLink.product_variant_id,
        )
        .join(
            ExternalCatalogProductVariantLink,
            ExternalCatalogProductVariantLink.external_product_id == ExternalCatalogProduct.id,
        )
        .where(
            ExternalCatalogProduct.source == CARDMARKET_SOURCE,
            ExternalCatalogProduct.product_group == "non_single",
            ExternalCatalogProductVariantLink.link_status == "accepted",
        )
    ).all()

    by_external: dict[int, tuple[str, int, int]] = {}
    for external_id, id_product, game_id, variant_id in linked:
        key = int(external_id)
        candidate = (str(id_product), int(game_id), int(variant_id))
        prior = by_external.get(key)
        if prior is not None and prior != candidate:
            raise ValueError(f"Ambiguous accepted product links for external_product_id={key}")
        by_external[key] = candidate

    if not by_external:
        return ProductPriceProjectionResult(0, 0, 0, 0, 0, None, None)

    latest_by_product: dict[int, datetime] = {
        int(product_id): as_of
        for product_id, as_of in session.execute(
            select(
                ExternalMarketPriceSnapshot.external_product_id,
                func.max(ExternalMarketPriceSnapshot.as_of),
            )
            .where(ExternalMarketPriceSnapshot.external_product_id.in_(by_external))
            .group_by(ExternalMarketPriceSnapshot.external_product_id)
        ).all()
    }

    price_rows: dict[int, list[ExternalMarketPriceSnapshot]] = {}
    if latest_by_product:
        # Fetching all latest rows and resolving variants in Python keeps the rule explicit.
        candidates = session.execute(
            select(ExternalMarketPriceSnapshot).where(
                ExternalMarketPriceSnapshot.external_product_id.in_(latest_by_product)
            )
        ).scalars().all()
        for row in candidates:
            if row.as_of == latest_by_product.get(int(row.external_product_id)):
                price_rows.setdefault(int(row.external_product_id), []).append(row)

    selected: list[tuple[int, str, ExternalMarketPriceSnapshot]] = []
    missing = ambiguous = 0
    for external_id, (id_product, _game_id, variant_id) in by_external.items():
        rows = price_rows.get(external_id, [])
        if not rows:
            missing += 1
            continue
        preferred = [row for row in rows if row.price_variant in {"default", "nonfoil"}]
        if len(preferred) == 1:
            row = preferred[0]
        elif len(rows) == 1:
            row = rows[0]
        else:
            ambiguous += 1
            continue
        if all(
            value is None
            for value in (row.price_low, row.price_mid, row.price_market, row.price_last, row.avg1, row.avg7, row.avg30)
        ):
            missing += 1
            continue
        selected.append((variant_id, id_product, row))

    source = session.execute(
        select(PriceSource).where(PriceSource.name == CARDMARKET_SOURCE)
    ).scalar_one_or_none()
    if source is None:
        source = PriceSource(
            name=CARDMARKET_SOURCE,
            currency="EUR",
            description="Cardmarket downloadable market price guide",
        )
        session.add(source)
        session.flush()
    if source.currency != "EUR":
        raise ValueError(f"Unexpected Cardmarket PriceSource currency: {source.currency}")

    payloads = []
    for variant_id, id_product, row in selected:
        payloads.append(
            {
                "entity_type": "product_variant",
                "entity_id": variant_id,
                "source_id": source.id,
                "currency": row.currency,
                "price_low": row.price_low,
                "price_mid": row.price_mid,
                "price_high": None,
                "price_market": row.price_market,
                "price_last": row.price_last,
                "quantity": None,
                "as_of": row.as_of,
                "raw_json": {
                    "idProduct": id_product,
                    "price_variant": row.price_variant,
                    "avg1": str(row.avg1) if row.avg1 is not None else None,
                    "avg7": str(row.avg7) if row.avg7 is not None else None,
                    "avg30": str(row.avg30) if row.avg30 is not None else None,
                    "projection": "exact_cardmarket_product_variant",
                },
            }
        )

    if payloads:
        dialect = session.get_bind().dialect.name
        if dialect == "postgresql":
            from sqlalchemy.dialects.postgresql import insert as pg_insert
            for offset in range(0, len(payloads), 500):
                statement = pg_insert(PriceSnapshot).values(payloads[offset : offset + 500])
                statement = statement.on_conflict_do_update(
                    constraint="uq_price_snapshot_identity",
                    set_={
                        field: getattr(statement.excluded, field)
                        for field in (
                            "price_low", "price_mid", "price_high", "price_market",
                            "price_last", "quantity", "raw_json",
                        )
                    },
                )
                session.execute(statement)
        else:
            for payload in payloads:
                existing = session.execute(
                    select(PriceSnapshot).where(
                        PriceSnapshot.entity_type == payload["entity_type"],
                        PriceSnapshot.entity_id == payload["entity_id"],
                        PriceSnapshot.source_id == payload["source_id"],
                        PriceSnapshot.currency == payload["currency"],
                        PriceSnapshot.as_of == payload["as_of"],
                    )
                ).scalar_one_or_none()
                if existing is None:
                    session.add(PriceSnapshot(**payload))
                else:
                    for field, value in payload.items():
                        if field not in {"entity_type", "entity_id", "source_id", "currency", "as_of"}:
                            setattr(existing, field, value)

    session.flush()
    dates = [row.as_of for _, _, row in selected]
    return ProductPriceProjectionResult(
        linked_products=len(by_external),
        priceable_products=len(selected),
        missing_price_products=missing,
        ambiguous_price_variants=ambiguous,
        snapshots=len(payloads),
        as_of_min=min(dates) if dates else None,
        as_of_max=max(dates) if dates else None,
    )
