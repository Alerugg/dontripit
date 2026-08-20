from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from sqlalchemy import func, select

from app.external_catalog_models import ExternalCatalogProduct, ExternalCatalogProductVariantLink
from app.models import Game, Product, ProductIdentifier, ProductVariant


CARDMARKET_SOURCE: Final = "cardmarket"
_ACCEPTED: Final = {"accepted", "mapped", "exact"}


@dataclass(frozen=True)
class CertifiedSealedGap:
    external_id: str
    name: str
    category: str
    expansion_external_id: str
    product_type: str
    language: str = "und"
    region: str = "global"
    packaging: str = ""

    def expected_packaging(self) -> str:
        return self.packaging or self.product_type


CERTIFIED_GAPS: Final = (
    CertifiedSealedGap(
        external_id="903128",
        name="Secret Lair Drop Series: A Marvelous Mathom Superdrop: Secret Lair x The Hobbit: He Who Walks Unseen",
        category="MtG Set",
        expansion_external_id="6692",
        product_type="set_product",
    ),
    CertifiedSealedGap(
        external_id="903129",
        name="Secret Lair Drop Series: A Marvelous Mathom Superdrop: Secret Lair x The Hobbit: He Who Walks Unseen Set",
        category="MtG Set",
        expansion_external_id="6692",
        product_type="set_product",
    ),
    CertifiedSealedGap(
        external_id="903175",
        name="Star Trek: Scene Box Set",
        category="Magic Theme Deck Display",
        expansion_external_id="6657",
        product_type="deck_display",
    ),
    CertifiedSealedGap(
        external_id="903183",
        name="The Hobbit: Prerelease Cards Booster",
        category="Magic Booster",
        expansion_external_id="6570",
        product_type="booster_pack",
    ),
)


@dataclass(frozen=True)
class GapDecision:
    spec: CertifiedSealedGap
    external_product_id: int
    status: str
    product_id: int | None = None
    variant_id: int | None = None
    detail: str | None = None

    def as_dict(self) -> dict:
        return {
            "external_id": self.spec.external_id,
            "name": self.spec.name,
            "category": self.spec.category,
            "expansion_external_id": self.spec.expansion_external_id,
            "product_type": self.spec.product_type,
            "language": self.spec.language,
            "region": self.spec.region,
            "packaging": self.spec.expected_packaging(),
            "external_product_id": self.external_product_id,
            "status": self.status,
            "product_id": self.product_id,
            "variant_id": self.variant_id,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class GapClosurePlan:
    catalog_capture: object
    decisions: tuple[GapDecision, ...]

    def summary(self) -> dict:
        statuses: dict[str, int] = {}
        for item in self.decisions:
            statuses[item.status] = statuses.get(item.status, 0) + 1
        conflicts = sum(count for status, count in statuses.items() if status.startswith("conflict"))
        safe = statuses.get("safe_create_product", 0) + statuses.get("safe_attach_existing", 0)
        already = statuses.get("already_closed", 0)
        return {
            "game": "mtg",
            "source": CARDMARKET_SOURCE,
            "catalog_capture": self.catalog_capture.isoformat() if hasattr(self.catalog_capture, "isoformat") else self.catalog_capture,
            "target_count": len(CERTIFIED_GAPS),
            "safe_to_apply": safe,
            "already_closed": already,
            "conflicts": conflicts,
            "status_counts": dict(sorted(statuses.items())),
            "write_ready": conflicts == 0 and safe + already == len(CERTIFIED_GAPS),
        }


def _variant_matches(product: Product, variant: ProductVariant, spec: CertifiedSealedGap, game_id: int) -> bool:
    return (
        int(product.game_id) == int(game_id)
        and product.name == spec.name
        and product.product_type == spec.product_type
        and product.set_id is None
        and variant.language == spec.language
        and variant.region == spec.region
        and variant.packaging == spec.expected_packaging()
    )


def build_mtg_sealed_gap_closure_plan(session) -> GapClosurePlan:
    game = session.execute(select(Game).where(Game.slug == "mtg")).scalar_one_or_none()
    if game is None:
        raise ValueError("MTG game is missing")

    capture = session.execute(
        select(func.max(ExternalCatalogProduct.last_seen_at)).where(
            ExternalCatalogProduct.source == CARDMARKET_SOURCE,
            ExternalCatalogProduct.game_id == game.id,
            ExternalCatalogProduct.product_group == "non_single",
        )
    ).scalar_one_or_none()
    if capture is None:
        raise ValueError("Current Cardmarket MTG non-single capture is missing")

    external_rows = session.execute(
        select(ExternalCatalogProduct).where(
            ExternalCatalogProduct.source == CARDMARKET_SOURCE,
            ExternalCatalogProduct.game_id == game.id,
            ExternalCatalogProduct.product_group == "non_single",
            ExternalCatalogProduct.last_seen_at == capture,
            ExternalCatalogProduct.external_id.in_([item.external_id for item in CERTIFIED_GAPS]),
        )
    ).scalars().all()
    by_external = {str(row.external_id): row for row in external_rows}

    decisions: list[GapDecision] = []
    for spec in CERTIFIED_GAPS:
        external = by_external.get(spec.external_id)
        if external is None:
            decisions.append(GapDecision(spec, 0, "conflict_missing_current_external", detail="target absent from current capture"))
            continue
        if (
            str(external.name) != spec.name
            or str(external.category or "") != spec.category
            or str(external.expansion_external_id or "") != spec.expansion_external_id
        ):
            decisions.append(
                GapDecision(
                    spec,
                    int(external.id),
                    "conflict_source_evidence_changed",
                    detail=(
                        f"current name={external.name!r} category={external.category!r} "
                        f"expansion={external.expansion_external_id!r}"
                    ),
                )
            )
            continue

        links = session.execute(
            select(ExternalCatalogProductVariantLink).where(
                ExternalCatalogProductVariantLink.external_product_id == external.id
            )
        ).scalars().all()
        accepted = [row for row in links if row.link_status in _ACCEPTED]
        if len(accepted) > 1:
            decisions.append(GapDecision(spec, int(external.id), "conflict_multiple_accepted_links"))
            continue
        if links and not accepted:
            decisions.append(GapDecision(spec, int(external.id), "conflict_review_pending_link"))
            continue

        identifier = session.execute(
            select(ProductIdentifier).where(
                ProductIdentifier.source == CARDMARKET_SOURCE,
                ProductIdentifier.external_id == spec.external_id,
            )
        ).scalar_one_or_none()
        identified_variant = session.get(ProductVariant, identifier.product_variant_id) if identifier is not None else None
        identified_product = session.get(Product, identified_variant.product_id) if identified_variant is not None else None

        if accepted:
            linked_variant = session.get(ProductVariant, accepted[0].product_variant_id)
            linked_product = session.get(Product, linked_variant.product_id) if linked_variant is not None else None
            if linked_variant is None or linked_product is None or not _variant_matches(linked_product, linked_variant, spec, game.id):
                decisions.append(GapDecision(spec, int(external.id), "conflict_existing_link_shape"))
                continue
            if identifier is None or int(identifier.product_variant_id) != int(linked_variant.id):
                decisions.append(GapDecision(spec, int(external.id), "conflict_identifier_link_disagree"))
                continue
            decisions.append(
                GapDecision(spec, int(external.id), "already_closed", int(linked_product.id), int(linked_variant.id))
            )
            continue

        if identifier is not None:
            if identified_variant is None or identified_product is None or not _variant_matches(identified_product, identified_variant, spec, game.id):
                decisions.append(GapDecision(spec, int(external.id), "conflict_identifier_shape"))
                continue
            decisions.append(
                GapDecision(
                    spec,
                    int(external.id),
                    "safe_attach_existing",
                    int(identified_product.id),
                    int(identified_variant.id),
                )
            )
            continue

        products = session.execute(
            select(Product).where(Product.game_id == game.id, Product.name == spec.name)
        ).scalars().all()
        if len(products) > 1:
            decisions.append(GapDecision(spec, int(external.id), "conflict_duplicate_canonical_name"))
            continue
        if products:
            product = products[0]
            if product.product_type != spec.product_type or product.set_id is not None:
                decisions.append(GapDecision(spec, int(external.id), "conflict_existing_product_shape"))
                continue
            variants = session.execute(
                select(ProductVariant).where(ProductVariant.product_id == product.id)
            ).scalars().all()
            exact = [
                row
                for row in variants
                if row.language == spec.language
                and row.region == spec.region
                and row.packaging == spec.expected_packaging()
            ]
            if len(exact) != 1 or len(variants) != 1:
                decisions.append(GapDecision(spec, int(external.id), "conflict_existing_variant_shape"))
                continue
            decisions.append(
                GapDecision(spec, int(external.id), "safe_attach_existing", int(product.id), int(exact[0].id))
            )
            continue

        decisions.append(GapDecision(spec, int(external.id), "safe_create_product"))

    return GapClosurePlan(capture, tuple(decisions))


def apply_mtg_sealed_gap_closure_plan(session, plan: GapClosurePlan) -> dict:
    summary = plan.summary()
    if not summary["write_ready"]:
        raise ValueError(f"Refusing MTG sealed gap closure: {summary}")

    game = session.execute(select(Game).where(Game.slug == "mtg")).scalar_one()
    created_products = created_variants = created_identifiers = created_links = 0

    for decision in plan.decisions:
        if decision.status == "already_closed":
            continue
        spec = decision.spec
        product = session.get(Product, decision.product_id) if decision.product_id else None
        variant = session.get(ProductVariant, decision.variant_id) if decision.variant_id else None

        if decision.status == "safe_create_product":
            product = Product(
                game_id=game.id,
                set_id=None,
                product_type=spec.product_type,
                name=spec.name,
                release_date=None,
            )
            session.add(product)
            session.flush()
            created_products += 1
            variant = ProductVariant(
                product_id=product.id,
                language=spec.language,
                region=spec.region,
                packaging=spec.expected_packaging(),
                sku=f"cardmarket:{spec.external_id}",
            )
            session.add(variant)
            session.flush()
            created_variants += 1

        if product is None or variant is None or not _variant_matches(product, variant, spec, game.id):
            raise ValueError(f"Unexpected canonical shape while applying {spec.external_id}")

        identifier = session.execute(
            select(ProductIdentifier).where(
                ProductIdentifier.source == CARDMARKET_SOURCE,
                ProductIdentifier.external_id == spec.external_id,
            )
        ).scalar_one_or_none()
        if identifier is None:
            session.add(
                ProductIdentifier(
                    product_variant_id=variant.id,
                    source=CARDMARKET_SOURCE,
                    external_id=spec.external_id,
                )
            )
            created_identifiers += 1
        elif int(identifier.product_variant_id) != int(variant.id):
            raise ValueError(f"Cardmarket identifier collision while applying {spec.external_id}")

        link = session.execute(
            select(ExternalCatalogProductVariantLink).where(
                ExternalCatalogProductVariantLink.external_product_id == decision.external_product_id,
                ExternalCatalogProductVariantLink.product_variant_id == variant.id,
            )
        ).scalar_one_or_none()
        if link is None:
            session.add(
                ExternalCatalogProductVariantLink(
                    external_product_id=decision.external_product_id,
                    product_variant_id=variant.id,
                    mapping_method="certified_mtg_current_gap_shape_v1",
                    confidence="exact",
                    link_status="accepted",
                    reviewed=False,
                    evidence={
                        "source": CARDMARKET_SOURCE,
                        "external_id": spec.external_id,
                        "name": spec.name,
                        "category": spec.category,
                        "expansion_external_id": spec.expansion_external_id,
                        "certified_shape": {
                            "product_type": spec.product_type,
                            "language": spec.language,
                            "region": spec.region,
                            "packaging": spec.expected_packaging(),
                            "set_id": None,
                        },
                        "audit": "Audit MTG Sealed Gap Canonical Shapes V1 / 2026-08-20",
                    },
                )
            )
            created_links += 1
        elif link.link_status not in _ACCEPTED:
            raise ValueError(f"Existing non-accepted link blocks {spec.external_id}")

    session.flush()
    return {
        **summary,
        "created_products": created_products,
        "created_variants": created_variants,
        "created_identifiers": created_identifiers,
        "created_links": created_links,
    }
