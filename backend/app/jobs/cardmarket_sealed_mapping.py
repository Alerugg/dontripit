from __future__ import annotations

import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass

from sqlalchemy import select

from app.external_catalog_models import (
    ExternalCatalogProduct,
    ExternalCatalogProductVariantLink,
)
from app.models import Game, Product, ProductIdentifier, ProductVariant


CARDMARKET_SOURCE = "cardmarket"
_ACCEPTED_LINK_STATUSES = {"accepted", "mapped", "exact"}


@dataclass(frozen=True)
class SealedMappingDecision:
    external_product_id: int
    external_id: str
    game: str
    name: str
    category: str | None
    status: str
    product_variant_id: int | None = None
    mapping_method: str | None = None
    evidence: dict | None = None

    def as_dict(self) -> dict:
        return {
            "external_product_id": self.external_product_id,
            "external_id": self.external_id,
            "game": self.game,
            "name": self.name,
            "category": self.category,
            "status": self.status,
            "product_variant_id": self.product_variant_id,
            "mapping_method": self.mapping_method,
            "evidence": self.evidence or {},
        }


@dataclass(frozen=True)
class SealedMappingPlan:
    decisions: tuple[SealedMappingDecision, ...]

    def summary(self) -> dict:
        statuses = Counter(item.status for item in self.decisions)
        safe = statuses.get("identifier_verified", 0) + statuses.get("exact_candidate", 0)
        return {
            "source": CARDMARKET_SOURCE,
            "external_products": len(self.decisions),
            "already_mapped": statuses.get("already_mapped", 0),
            "safe_candidates": safe,
            "unresolved": len(self.decisions) - statuses.get("already_mapped", 0) - safe,
            "status_counts": dict(sorted(statuses.items())),
            "write_ready": not any(
                statuses.get(status, 0)
                for status in ("accepted_link_conflict", "cross_game_identifier")
            ),
        }


def normalize_product_name(value: str | None) -> str:
    raw = unicodedata.normalize("NFKD", str(value or "").casefold())
    raw = "".join(char for char in raw if not unicodedata.combining(char))
    return " ".join(re.sub(r"[^a-z0-9]+", " ", raw).split())


def _category_is_compatible(product_type: str | None, category: str | None, name: str | None) -> bool:
    """Require positive type evidence; unknown product types never auto-map."""
    kind = normalize_product_name(product_type)
    market_text = normalize_product_name(f"{category or ''} {name or ''}")
    if not kind or kind == "unknown":
        return False

    aliases = {
        "booster box": ("display", "booster box"),
        "display": ("display", "booster box"),
        "booster pack": ("booster pack", "booster"),
        "starter deck": ("starter deck", "deck"),
        "structure deck": ("structure deck", "deck"),
        "elite trainer box": ("elite trainer box", "trainer box", "etb"),
        "collection": ("collection",),
        "bundle": ("bundle",),
        "tin": ("tin",),
        "case": ("case",),
    }
    accepted_tokens = aliases.get(kind)
    if accepted_tokens is None:
        # Catalogs may use a more specific canonical type. Requiring its full
        # normalized text in Cardmarket evidence remains conservative.
        accepted_tokens = (kind,) if len(kind) >= 4 else ()
    return any(token in market_text for token in accepted_tokens)


def build_sealed_mapping_plan(session) -> SealedMappingPlan:
    """Propose only provable Cardmarket non-single -> canonical variant links.

    Source-owned products remain usable even when canonical identity is absent.
    Exact-name candidates must be unique on both sides, have one physical
    variant, and carry compatible category evidence. The function is read-only.
    """
    external_rows = session.execute(
        select(ExternalCatalogProduct, Game.slug)
        .join(Game, Game.id == ExternalCatalogProduct.game_id)
        .where(
            ExternalCatalogProduct.source == CARDMARKET_SOURCE,
            ExternalCatalogProduct.product_group == "non_single",
        )
        .order_by(Game.slug, ExternalCatalogProduct.external_id)
    ).all()

    accepted_rows = session.execute(
        select(
            ExternalCatalogProductVariantLink.external_product_id,
            ExternalCatalogProductVariantLink.product_variant_id,
            Product.game_id,
        )
        .join(ProductVariant, ProductVariant.id == ExternalCatalogProductVariantLink.product_variant_id)
        .join(Product, Product.id == ProductVariant.product_id)
        .where(ExternalCatalogProductVariantLink.link_status.in_(_ACCEPTED_LINK_STATUSES))
    ).all()
    accepted_by_external: dict[int, list[tuple[int, int]]] = defaultdict(list)
    for external_product_id, variant_id, game_id in accepted_rows:
        accepted_by_external[int(external_product_id)].append((int(variant_id), int(game_id)))

    all_linked_external_ids = set(
        session.execute(select(ExternalCatalogProductVariantLink.external_product_id)).scalars().all()
    )

    legacy_rows = session.execute(
        select(ProductIdentifier.external_id, ProductVariant.id, Product.game_id)
        .join(ProductVariant, ProductVariant.id == ProductIdentifier.product_variant_id)
        .join(Product, Product.id == ProductVariant.product_id)
        .where(ProductIdentifier.source == CARDMARKET_SOURCE)
    ).all()
    legacy_by_external = {
        str(external_id): (int(variant_id), int(game_id))
        for external_id, variant_id, game_id in legacy_rows
    }

    canonical_rows = session.execute(
        select(Product, Game.slug)
        .join(Game, Game.id == Product.game_id)
        .order_by(Product.id)
    ).all()
    products_by_key: dict[tuple[str, str], list[Product]] = defaultdict(list)
    for product, game_slug in canonical_rows:
        products_by_key[(str(game_slug), normalize_product_name(product.name))].append(product)

    variant_rows = session.execute(select(ProductVariant).order_by(ProductVariant.id)).scalars().all()
    variants_by_product: dict[int, list[ProductVariant]] = defaultdict(list)
    for variant in variant_rows:
        variants_by_product[int(variant.product_id)].append(variant)

    external_name_counts = Counter(
        (str(game_slug), normalize_product_name(external.name))
        for external, game_slug in external_rows
    )

    decisions: list[SealedMappingDecision] = []
    for external, game_slug in external_rows:
        external_id = str(external.external_id)
        base = {
            "external_product_id": int(external.id),
            "external_id": external_id,
            "game": str(game_slug),
            "name": str(external.name),
            "category": external.category,
        }
        accepted = accepted_by_external.get(int(external.id), [])
        if accepted:
            unique_variants = {variant_id for variant_id, _ in accepted}
            same_game = all(game_id == int(external.game_id) for _, game_id in accepted)
            if len(unique_variants) == 1 and same_game:
                decisions.append(SealedMappingDecision(
                    **base,
                    status="already_mapped",
                    product_variant_id=next(iter(unique_variants)),
                    evidence={"accepted_links": len(accepted)},
                ))
            else:
                decisions.append(SealedMappingDecision(
                    **base,
                    status="accepted_link_conflict",
                    evidence={
                        "variant_ids": sorted(unique_variants),
                        "cross_game": not same_game,
                    },
                ))
            continue

        if int(external.id) in all_linked_external_ids:
            decisions.append(SealedMappingDecision(**base, status="review_pending"))
            continue

        legacy = legacy_by_external.get(external_id)
        if legacy is not None:
            variant_id, mapped_game_id = legacy
            if mapped_game_id != int(external.game_id):
                decisions.append(SealedMappingDecision(
                    **base,
                    status="cross_game_identifier",
                    product_variant_id=variant_id,
                ))
            else:
                decisions.append(SealedMappingDecision(
                    **base,
                    status="identifier_verified",
                    product_variant_id=variant_id,
                    mapping_method="legacy_product_identifier",
                    evidence={"source": CARDMARKET_SOURCE, "external_id": external_id},
                ))
            continue

        normalized_name = normalize_product_name(external.name)
        if not normalized_name:
            decisions.append(SealedMappingDecision(**base, status="missing_identity_evidence"))
            continue
        if external_name_counts[(str(game_slug), normalized_name)] != 1:
            decisions.append(SealedMappingDecision(
                **base,
                status="ambiguous_external_name",
                evidence={"normalized_name": normalized_name},
            ))
            continue

        products = products_by_key.get((str(game_slug), normalized_name), [])
        if not products:
            decisions.append(SealedMappingDecision(
                **base,
                status="canonical_catalog_gap",
                evidence={"normalized_name": normalized_name},
            ))
            continue
        if len(products) != 1:
            decisions.append(SealedMappingDecision(
                **base,
                status="ambiguous_canonical_name",
                evidence={"product_ids": [int(product.id) for product in products]},
            ))
            continue

        product = products[0]
        variants = variants_by_product.get(int(product.id), [])
        if len(variants) != 1:
            decisions.append(SealedMappingDecision(
                **base,
                status="ambiguous_physical_variant" if variants else "canonical_variant_gap",
                evidence={
                    "product_id": int(product.id),
                    "variant_ids": [int(variant.id) for variant in variants],
                },
            ))
            continue
        if not _category_is_compatible(product.product_type, external.category, external.name):
            decisions.append(SealedMappingDecision(
                **base,
                status="incompatible_category",
                evidence={"product_id": int(product.id), "product_type": product.product_type},
            ))
            continue

        variant = variants[0]
        decisions.append(SealedMappingDecision(
            **base,
            status="exact_candidate",
            product_variant_id=int(variant.id),
            mapping_method="exact_unique_name_category",
            evidence={
                "normalized_name": normalized_name,
                "product_id": int(product.id),
                "product_type": product.product_type,
                "language": variant.language,
                "region": variant.region,
                "packaging": variant.packaging,
            },
        ))

    return SealedMappingPlan(tuple(decisions))


def apply_sealed_mapping_plan(session, plan: SealedMappingPlan) -> dict:
    summary = plan.summary()
    if not summary["write_ready"]:
        raise ValueError("Refusing Cardmarket sealed mapping apply with identity conflicts")

    written = 0
    skipped = 0
    for decision in plan.decisions:
        if decision.status not in {"identifier_verified", "exact_candidate"}:
            continue
        if decision.product_variant_id is None:
            raise ValueError(f"Safe decision {decision.external_id} has no product variant")
        existing = session.execute(
            select(ExternalCatalogProductVariantLink).where(
                ExternalCatalogProductVariantLink.external_product_id == decision.external_product_id,
                ExternalCatalogProductVariantLink.product_variant_id == decision.product_variant_id,
            )
        ).scalar_one_or_none()
        if existing is not None:
            skipped += 1
            continue
        session.add(ExternalCatalogProductVariantLink(
            external_product_id=decision.external_product_id,
            product_variant_id=decision.product_variant_id,
            mapping_method=decision.mapping_method or "identity_verified",
            confidence="exact",
            link_status="accepted",
            reviewed=False,
            evidence=decision.evidence or {},
        ))
        written += 1
    session.flush()
    return {**summary, "written": written, "skipped_existing": skipped}
