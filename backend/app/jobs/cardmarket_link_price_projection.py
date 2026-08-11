from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import func, select

from app.external_catalog_models import (
    ExternalCatalogPrintLink,
    ExternalCatalogProduct,
    ExternalMarketPriceSnapshot,
)
from app.models import Card, Game, PriceSnapshot, PriceSource, Print


CARDMARKET_SOURCE = "cardmarket"
CARDMARKET_CURRENCY = "EUR"
_ACCEPTED_STATUSES = {"accepted", "mapped", "exact"}
_UNSUPPORTED_FINISH_TOKENS = ("etched", "glossy")


@dataclass(frozen=True)
class LinkPriceProjectionPlan:
    game_slug: str
    as_of: datetime | None
    accepted_links: int
    canonical_prints: int
    priceable_prints: int
    unsupported_finish: int
    missing_external_price: int
    ambiguous_print_links: int
    cross_game_links: int
    snapshots: tuple[dict, ...]

    def summary(self) -> dict:
        return {
            "source": CARDMARKET_SOURCE,
            "currency": CARDMARKET_CURRENCY,
            "game": self.game_slug,
            "as_of": self.as_of.isoformat() if self.as_of else None,
            "accepted_links": self.accepted_links,
            "canonical_prints": self.canonical_prints,
            "priceable_prints": self.priceable_prints,
            "unsupported_finish": self.unsupported_finish,
            "missing_external_price": self.missing_external_price,
            "ambiguous_print_links": self.ambiguous_print_links,
            "cross_game_links": self.cross_game_links,
            "snapshot_count": len(self.snapshots),
            "write_ready": self.ambiguous_print_links == 0 and self.cross_game_links == 0,
        }


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _price_variant(*, is_foil: bool, variant: str | None) -> str | None:
    normalized = str(variant or "").strip().lower()
    if any(token in normalized for token in _UNSUPPORTED_FINISH_TOKENS):
        return None
    if is_foil:
        return "foil"
    return "nonfoil"


def _positive(value) -> bool:
    if value is None:
        return False
    try:
        return Decimal(str(value)) > 0
    except Exception:
        return False


def _has_meaningful_price(external_price: ExternalMarketPriceSnapshot) -> bool:
    # Cardmarket PriceGuide uses zero-valued placeholders in some unavailable
    # finish blocks. A physical card cannot have a useful public market value
    # of EUR 0.00, so those rows stay traceable externally but are not projected
    # as a canonical price.
    return any(
        _positive(value)
        for value in (
            external_price.price_low,
            external_price.price_mid,
            external_price.price_market,
            external_price.price_last,
            external_price.avg1,
            external_price.avg7,
            external_price.avg30,
        )
    )


def _money_payload(
    external_price: ExternalMarketPriceSnapshot,
    *,
    print_id: int,
    external_product: ExternalCatalogProduct,
    link: ExternalCatalogPrintLink,
    finish: str,
) -> dict:
    return {
        "entity_type": "print",
        "entity_id": int(print_id),
        "currency": CARDMARKET_CURRENCY,
        "price_low": external_price.price_low if _positive(external_price.price_low) else None,
        "price_mid": external_price.price_mid if _positive(external_price.price_mid) else None,
        "price_high": None,
        "price_market": external_price.price_market if _positive(external_price.price_market) else None,
        "price_last": external_price.price_last if _positive(external_price.price_last) else None,
        "quantity": None,
        "raw_json": {
            "idProduct": str(external_product.external_id),
            "external_product_id": int(external_product.id),
            "price_variant": str(external_price.price_variant),
            "finish": finish,
            "mapping_method": str(link.mapping_method),
            "mapping_confidence": str(link.confidence),
            "mapping_evidence": link.evidence or {},
            "website_path": external_product.website_path,
            "external_avg1": str(external_price.avg1) if external_price.avg1 is not None else None,
            "external_avg7": str(external_price.avg7) if external_price.avg7 is not None else None,
            "external_avg30": str(external_price.avg30) if external_price.avg30 is not None else None,
        },
    }


def build_link_price_projection_plan(
    session,
    *,
    game_slug: str,
    as_of: datetime | None = None,
) -> LinkPriceProjectionPlan:
    """Project source-owned Cardmarket prices through already-accepted exact links.

    This function never infers identity. It consumes only previously accepted
    ExternalCatalogPrintLink rows and refuses to price a canonical Print if more
    than one Cardmarket product claims it. Finishes that Cardmarket's public
    Price Guide cannot represent independently (currently etched/glossy) remain
    unpriced rather than being guessed.
    """
    game_slug = str(game_slug or "").strip().lower()
    if not game_slug:
        raise ValueError("game_slug is required")

    game = session.execute(select(Game).where(Game.slug == game_slug)).scalar_one_or_none()
    if game is None:
        raise ValueError(f"Unknown game slug {game_slug!r}")

    link_rows = session.execute(
        select(
            ExternalCatalogPrintLink,
            ExternalCatalogProduct,
            Print.id,
            Print.is_foil,
            Print.variant,
            Card.game_id,
        )
        .join(ExternalCatalogProduct, ExternalCatalogProduct.id == ExternalCatalogPrintLink.external_product_id)
        .join(Print, Print.id == ExternalCatalogPrintLink.print_id)
        .join(Card, Card.id == Print.card_id)
        .where(
            ExternalCatalogProduct.source == CARDMARKET_SOURCE,
            ExternalCatalogProduct.game_id == game.id,
            ExternalCatalogProduct.product_group == "single",
            ExternalCatalogPrintLink.link_status.in_(_ACCEPTED_STATUSES),
        )
    ).all()

    by_print: dict[int, list[tuple]] = {}
    cross_game_links = 0
    for link, external_product, print_id, is_foil, variant, card_game_id in link_rows:
        if int(card_game_id) != int(game.id):
            cross_game_links += 1
            continue
        by_print.setdefault(int(print_id), []).append((link, external_product, bool(is_foil), str(variant or "")))

    ambiguous_print_ids = {print_id for print_id, rows in by_print.items() if len({int(row[1].id) for row in rows}) != 1}

    if as_of is None:
        as_of = session.execute(
            select(func.max(ExternalMarketPriceSnapshot.as_of))
            .join(ExternalCatalogProduct, ExternalCatalogProduct.id == ExternalMarketPriceSnapshot.external_product_id)
            .where(
                ExternalCatalogProduct.source == CARDMARKET_SOURCE,
                ExternalCatalogProduct.game_id == game.id,
                ExternalCatalogProduct.product_group == "single",
            )
        ).scalar_one_or_none()
    as_of = _utc(as_of)

    product_ids = sorted({int(row[1].id) for rows in by_print.values() for row in rows})
    external_prices: dict[tuple[int, str], ExternalMarketPriceSnapshot] = {}
    if as_of is not None and product_ids:
        price_rows = session.execute(
            select(ExternalMarketPriceSnapshot).where(
                ExternalMarketPriceSnapshot.external_product_id.in_(product_ids),
                ExternalMarketPriceSnapshot.currency == CARDMARKET_CURRENCY,
                ExternalMarketPriceSnapshot.as_of == as_of,
                ExternalMarketPriceSnapshot.price_variant.in_(("nonfoil", "foil")),
            )
        ).scalars().all()
        external_prices = {
            (int(row.external_product_id), str(row.price_variant)): row for row in price_rows
        }

    snapshots: list[dict] = []
    unsupported_finish = 0
    missing_external_price = 0
    priceable_prints = 0

    for print_id, rows in sorted(by_print.items()):
        if print_id in ambiguous_print_ids:
            continue
        link, external_product, is_foil, variant = rows[0]
        price_variant = _price_variant(is_foil=is_foil, variant=variant)
        if price_variant is None:
            unsupported_finish += 1
            continue
        external_price = external_prices.get((int(external_product.id), price_variant))
        if external_price is None or not _has_meaningful_price(external_price):
            missing_external_price += 1
            continue
        priceable_prints += 1
        snapshots.append(
            _money_payload(
                external_price,
                print_id=print_id,
                external_product=external_product,
                link=link,
                finish=variant or ("foil" if is_foil else "nonfoil"),
            )
        )

    return LinkPriceProjectionPlan(
        game_slug=game_slug,
        as_of=as_of,
        accepted_links=len(link_rows),
        canonical_prints=len(by_print),
        priceable_prints=priceable_prints,
        unsupported_finish=unsupported_finish,
        missing_external_price=missing_external_price,
        ambiguous_print_links=len(ambiguous_print_ids),
        cross_game_links=cross_game_links,
        snapshots=tuple(snapshots),
    )


def apply_link_price_projection_plan(session, plan: LinkPriceProjectionPlan) -> dict:
    if plan.ambiguous_print_links:
        raise ValueError(f"Refusing Cardmarket projection with {plan.ambiguous_print_links} ambiguous canonical Prints")
    if plan.cross_game_links:
        raise ValueError(f"Refusing Cardmarket projection with {plan.cross_game_links} cross-game links")
    if plan.as_of is None or not plan.snapshots:
        return {**plan.summary(), "inserted": 0, "updated": 0}

    source = session.execute(select(PriceSource).where(PriceSource.name == CARDMARKET_SOURCE)).scalar_one_or_none()
    if source is None:
        source = PriceSource(
            name=CARDMARKET_SOURCE,
            currency=CARDMARKET_CURRENCY,
            description="Cardmarket public daily Price Guide projected through accepted exact external catalog links.",
        )
        session.add(source)
        session.flush()
    elif source.currency != CARDMARKET_CURRENCY:
        raise ValueError(f"Cardmarket price source has unexpected currency {source.currency!r}")

    entity_ids = [int(payload["entity_id"]) for payload in plan.snapshots]
    existing_ids = set(session.execute(
        select(PriceSnapshot.entity_id).where(
            PriceSnapshot.entity_type == "print",
            PriceSnapshot.entity_id.in_(entity_ids),
            PriceSnapshot.source_id == source.id,
            PriceSnapshot.currency == CARDMARKET_CURRENCY,
            PriceSnapshot.as_of == plan.as_of,
        )
    ).scalars().all())

    rows = [{"source_id": source.id, "as_of": plan.as_of, **payload} for payload in plan.snapshots]
    inserted = sum(1 for payload in plan.snapshots if int(payload["entity_id"]) not in existing_ids)
    updated = len(rows) - inserted

    if session.get_bind().dialect.name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        update_fields = (
            "price_low",
            "price_mid",
            "price_high",
            "price_market",
            "price_last",
            "quantity",
            "raw_json",
        )
        for offset in range(0, len(rows), 1000):
            statement = pg_insert(PriceSnapshot).values(rows[offset:offset + 1000])
            statement = statement.on_conflict_do_update(
                constraint="uq_price_snapshot_identity",
                set_={field: getattr(statement.excluded, field) for field in update_fields},
            )
            session.execute(statement)
    else:
        existing_rows = session.execute(
            select(PriceSnapshot).where(
                PriceSnapshot.entity_type == "print",
                PriceSnapshot.entity_id.in_(entity_ids),
                PriceSnapshot.source_id == source.id,
                PriceSnapshot.currency == CARDMARKET_CURRENCY,
                PriceSnapshot.as_of == plan.as_of,
            )
        ).scalars().all()
        by_id = {int(row.entity_id): row for row in existing_rows}
        for row_data in rows:
            current = by_id.get(int(row_data["entity_id"]))
            if current is None:
                session.add(PriceSnapshot(**row_data))
                continue
            for field in (
                "price_low", "price_mid", "price_high", "price_market", "price_last", "quantity", "raw_json"
            ):
                setattr(current, field, row_data[field])

    return {**plan.summary(), "inserted": inserted, "updated": updated}
