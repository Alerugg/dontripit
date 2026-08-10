from __future__ import annotations

import csv
import gzip
import io
import json
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from sqlalchemy import select

from app.external_catalog_models import (
    ExternalCatalogPrintLink,
    ExternalCatalogProduct,
    ExternalCatalogProductVariantLink,
)
from app.jobs.cardmarket_catalog_audit import ProductListRow, audit_product_list
from app.models import (
    Card,
    Game,
    Print,
    PrintIdentifier,
    Product,
    ProductIdentifier,
    ProductVariant,
)


CARDMARKET_SOURCE = "cardmarket"
SUPPORTED_GAMES = {"pokemon", "onepiece", "mtg", "yugioh"}
PRODUCT_GROUPS = {"single", "non_single"}
_ACCEPTED_LINK_STATUSES = {"accepted", "mapped", "exact"}

_CONFLICT_STATUSES = {
    "duplicate_catalog_product",
    "identifier_entity_conflict",
    "ambiguous_print_identifier",
    "ambiguous_product_identifier",
    "cross_game_identifier",
    "wrong_entity_mapping",
    "external_id_conflict",
    "print_identifier_conflict",
    "game_conflict",
}


@dataclass(frozen=True)
class CatalogFeed:
    game_slug: str
    product_group: str
    rows: tuple[ProductListRow, ...]
    created_at: datetime | None
    raw_records: int
    rejected_records: int


@dataclass(frozen=True)
class InventoryDecision:
    product_id: str
    game: str
    product_group: str
    name: str
    category: str
    expansion_id: str
    status: str
    entity_type: str | None = None
    entity_id: int | None = None
    evidence: dict | None = None

    def as_dict(self) -> dict:
        return {
            "product_id": self.product_id,
            "game": self.game,
            "product_group": self.product_group,
            "name": self.name,
            "category": self.category,
            "expansion_id": self.expansion_id,
            "status": self.status,
            "entity_type": self.entity_type,
            "entity_id": self.entity_id,
            "evidence": self.evidence or {},
        }


def _parse_created_at(value) -> datetime | None:
    if not value:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    # Cardmarket currently also emits offsets such as +0200.
    if len(raw) >= 5 and raw[-5] in {"+", "-"} and raw[-3] != ":":
        raw = raw[:-2] + ":" + raw[-2:]
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _first(mapping: dict, *names: str) -> str:
    normalized = {str(key).strip().casefold(): value for key, value in mapping.items()}
    for name in names:
        value = normalized.get(name.casefold())
        if value is not None:
            return str(value).strip()
    return ""


def _parse_master_product(raw: dict) -> ProductListRow | None:
    """Retain every Cardmarket row that has its two irreducible fields.

    Master completeness must never depend on expansion/category metadata being
    populated. Rows missing optional hints remain in the denominator and become
    explicit unresolved decisions later.
    """
    product_id = _first(raw, "idProduct", "product_id")
    name = _first(raw, "Name", "name")
    if not product_id or not name:
        return None
    return ProductListRow(
        product_id=product_id,
        name=name,
        category_id=_first(raw, "Category ID", "idCategory", "category_id"),
        category=_first(raw, "Category", "category", "categoryName"),
        expansion_id=_first(raw, "Expansion ID", "idExpansion", "expansion_id"),
        date_added=_first(raw, "Date Added", "date_added", "dateAdded") or None,
        metacard_id=_first(raw, "idMetacard", "metacard_id") or None,
    )


def _extract_raw_records(content: bytes) -> tuple[datetime | None, list]:
    if content[:2] == b"\x1f\x8b":
        content = gzip.decompress(content)
    stripped = content.lstrip()
    if stripped.startswith((b"{", b"[")):
        payload = json.loads(content.decode("utf-8-sig"))
        created_at = _parse_created_at(payload.get("createdAt") or payload.get("created_at")) if isinstance(payload, dict) else None
        if isinstance(payload, dict):
            raw_rows = payload.get("products")
            if raw_rows is None:
                raw_rows = payload.get("data")
            if raw_rows is None:
                raw_rows = payload.get("items")
        elif isinstance(payload, list):
            raw_rows = payload
        else:
            raise ValueError("Unsupported Cardmarket Product List JSON root")
        if not isinstance(raw_rows, list):
            raise ValueError("Cardmarket Product List JSON must contain a product list")
        return created_at, raw_rows

    text = content.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise ValueError("Cardmarket Product List CSV has no header")
    return None, list(reader)


def load_catalog_feed_bytes(content: bytes, *, game_slug: str, product_group: str) -> CatalogFeed:
    game_slug = str(game_slug or "").strip().lower()
    product_group = str(product_group or "").strip().lower()
    if game_slug not in SUPPORTED_GAMES:
        raise ValueError(f"Unsupported Don’tRipIt game slug: {game_slug!r}")
    if product_group not in PRODUCT_GROUPS:
        raise ValueError(f"Unsupported Cardmarket product group: {product_group!r}")

    created_at, raw_rows = _extract_raw_records(content)
    if not raw_rows:
        raise ValueError("Cardmarket Product List contained zero records")

    rows: list[ProductListRow] = []
    rejected = 0
    for raw in raw_rows:
        if not isinstance(raw, dict):
            rejected += 1
            continue
        parsed = _parse_master_product(raw)
        if parsed is None:
            rejected += 1
            continue
        rows.append(parsed)

    return CatalogFeed(
        game_slug=game_slug,
        product_group=product_group,
        rows=tuple(rows),
        created_at=created_at,
        raw_records=len(raw_rows),
        rejected_records=rejected,
    )


def load_catalog_feed_file(path: str | Path, *, game_slug: str, product_group: str) -> CatalogFeed:
    return load_catalog_feed_bytes(Path(path).read_bytes(), game_slug=game_slug, product_group=product_group)


def _print_identifier_index(session) -> dict[str, list[tuple[int, str]]]:
    legacy_rows = session.execute(
        select(PrintIdentifier.external_id, Print.id, Game.slug)
        .join(Print, Print.id == PrintIdentifier.print_id)
        .join(Card, Card.id == Print.card_id)
        .join(Game, Game.id == Card.game_id)
        .where(PrintIdentifier.source == CARDMARKET_SOURCE)
    ).all()
    linked_rows = session.execute(
        select(ExternalCatalogProduct.external_id, Print.id, Game.slug)
        .join(
            ExternalCatalogPrintLink,
            ExternalCatalogPrintLink.external_product_id == ExternalCatalogProduct.id,
        )
        .join(Print, Print.id == ExternalCatalogPrintLink.print_id)
        .join(Card, Card.id == Print.card_id)
        .join(Game, Game.id == Card.game_id)
        .where(
            ExternalCatalogProduct.source == CARDMARKET_SOURCE,
            ExternalCatalogPrintLink.link_status.in_(_ACCEPTED_LINK_STATUSES),
        )
    ).all()
    result: dict[str, set[tuple[int, str]]] = {}
    for external_id, print_id, game_slug in [*legacy_rows, *linked_rows]:
        result.setdefault(str(external_id), set()).add((int(print_id), str(game_slug)))
    return {external_id: sorted(mappings) for external_id, mappings in result.items()}


def _product_identifier_index(session) -> dict[str, list[tuple[int, str]]]:
    legacy_rows = session.execute(
        select(ProductIdentifier.external_id, ProductVariant.id, Game.slug)
        .join(ProductVariant, ProductVariant.id == ProductIdentifier.product_variant_id)
        .join(Product, Product.id == ProductVariant.product_id)
        .join(Game, Game.id == Product.game_id)
        .where(ProductIdentifier.source == CARDMARKET_SOURCE)
    ).all()
    linked_rows = session.execute(
        select(ExternalCatalogProduct.external_id, ProductVariant.id, Game.slug)
        .join(
            ExternalCatalogProductVariantLink,
            ExternalCatalogProductVariantLink.external_product_id == ExternalCatalogProduct.id,
        )
        .join(ProductVariant, ProductVariant.id == ExternalCatalogProductVariantLink.product_variant_id)
        .join(Product, Product.id == ProductVariant.product_id)
        .join(Game, Game.id == Product.game_id)
        .where(
            ExternalCatalogProduct.source == CARDMARKET_SOURCE,
            ExternalCatalogProductVariantLink.link_status.in_(_ACCEPTED_LINK_STATUSES),
        )
    ).all()
    result: dict[str, set[tuple[int, str]]] = {}
    for external_id, variant_id, game_slug in [*legacy_rows, *linked_rows]:
        result.setdefault(str(external_id), set()).add((int(variant_id), str(game_slug)))
    return {external_id: sorted(mappings) for external_id, mappings in result.items()}


def _single_audit_index(session, feed: CatalogFeed, crosswalk: dict[str, dict]) -> dict[str, object]:
    if feed.product_group != "single":
        return {}
    _, audit_decisions = audit_product_list(
        session,
        list(feed.rows),
        crosswalk,
        game_filter=feed.game_slug,
    )
    return {item.product_id: item for item in audit_decisions}


def _decision(
    feed: CatalogFeed,
    row: ProductListRow,
    status: str,
    *,
    entity_type: str | None = None,
    entity_id: int | None = None,
    evidence: dict | None = None,
) -> InventoryDecision:
    return InventoryDecision(
        product_id=row.product_id,
        game=feed.game_slug,
        product_group=feed.product_group,
        name=row.name,
        category=row.category,
        expansion_id=row.expansion_id,
        status=status,
        entity_type=entity_type,
        entity_id=entity_id,
        evidence=evidence or {},
    )


def build_master_inventory(
    session,
    feeds: Iterable[CatalogFeed],
    *,
    crosswalks: dict[str, dict[str, dict]] | None = None,
) -> tuple[dict, list[InventoryDecision]]:
    """Classify every accepted Cardmarket product row against Don’tRipIt.

    This is the reverse completeness gate: Cardmarket is the denominator. The
    audit is evidence-only and performs no writes. A game is ``ready`` only when
    every raw row is accounted for and every accepted product is already mapped
    to the correct canonical entity without ambiguity or conflict.
    """
    feeds = list(feeds)
    crosswalks = crosswalks or {}
    if not feeds:
        raise ValueError("At least one Cardmarket catalog feed is required")

    seen_feed_keys: set[tuple[str, str]] = set()
    for feed in feeds:
        key = (feed.game_slug, feed.product_group)
        if key in seen_feed_keys:
            raise ValueError(f"Duplicate catalog feed for {feed.game_slug}:{feed.product_group}")
        seen_feed_keys.add(key)

    print_index = _print_identifier_index(session)
    product_index = _product_identifier_index(session)
    all_product_ids = Counter(row.product_id for feed in feeds for row in feed.rows)
    single_audits = {
        (feed.game_slug, feed.product_group): _single_audit_index(
            session,
            feed,
            crosswalks.get(feed.game_slug, {}),
        )
        for feed in feeds
        if feed.product_group == "single"
    }

    decisions: list[InventoryDecision] = []
    for feed in feeds:
        audit_index = single_audits.get((feed.game_slug, feed.product_group), {})
        for row in feed.rows:
            print_mappings = print_index.get(row.product_id, [])
            product_mappings = product_index.get(row.product_id, [])
            evidence = {
                "print_mappings": [{"entity_id": entity_id, "game": game} for entity_id, game in print_mappings],
                "product_variant_mappings": [{"entity_id": entity_id, "game": game} for entity_id, game in product_mappings],
                "date_added": row.date_added,
                "metacard_id": row.metacard_id,
                "feed_created_at": feed.created_at.isoformat() if feed.created_at else None,
            }

            if all_product_ids[row.product_id] > 1:
                decisions.append(_decision(feed, row, "duplicate_catalog_product", evidence={**evidence, "catalog_rows": all_product_ids[row.product_id]}))
                continue
            if print_mappings and product_mappings:
                decisions.append(_decision(feed, row, "identifier_entity_conflict", evidence=evidence))
                continue

            if feed.product_group == "single":
                if product_mappings:
                    decisions.append(_decision(feed, row, "wrong_entity_mapping", entity_type="product_variant", entity_id=product_mappings[0][0] if len(product_mappings) == 1 else None, evidence=evidence))
                    continue
                if len(print_mappings) > 1:
                    decisions.append(_decision(feed, row, "ambiguous_print_identifier", entity_type="print", evidence=evidence))
                    continue
                if len(print_mappings) == 1:
                    print_id, mapped_game = print_mappings[0]
                    if mapped_game != feed.game_slug:
                        decisions.append(_decision(feed, row, "cross_game_identifier", entity_type="print", entity_id=print_id, evidence=evidence))
                    else:
                        decisions.append(_decision(feed, row, "mapped_print", entity_type="print", entity_id=print_id, evidence=evidence))
                    continue

                audit = audit_index.get(row.product_id)
                if audit is not None:
                    audit_evidence = {
                        **evidence,
                        "single_audit": getattr(audit, "evidence", None) or {},
                        "candidate_card_id": getattr(audit, "card_id", None),
                        "candidate_print_id": getattr(audit, "print_id", None),
                        "set_code": getattr(audit, "set_code", None),
                    }
                    decisions.append(_decision(
                        feed,
                        row,
                        getattr(audit, "status", "unmapped_single"),
                        entity_type="print" if getattr(audit, "print_id", None) is not None else None,
                        entity_id=getattr(audit, "print_id", None),
                        evidence=audit_evidence,
                    ))
                else:
                    decisions.append(_decision(feed, row, "unmapped_single", evidence=evidence))
                continue

            # Cardmarket non-single products belong to the sealed/product side.
            if print_mappings:
                decisions.append(_decision(feed, row, "wrong_entity_mapping", entity_type="print", entity_id=print_mappings[0][0] if len(print_mappings) == 1 else None, evidence=evidence))
                continue
            if len(product_mappings) > 1:
                decisions.append(_decision(feed, row, "ambiguous_product_identifier", entity_type="product_variant", evidence=evidence))
                continue
            if len(product_mappings) == 1:
                variant_id, mapped_game = product_mappings[0]
                if mapped_game != feed.game_slug:
                    decisions.append(_decision(feed, row, "cross_game_identifier", entity_type="product_variant", entity_id=variant_id, evidence=evidence))
                else:
                    decisions.append(_decision(feed, row, "mapped_product_variant", entity_type="product_variant", entity_id=variant_id, evidence=evidence))
                continue
            decisions.append(_decision(feed, row, "unmapped_product", evidence=evidence))

    per_game: dict[str, dict] = {}
    for game_slug in sorted({feed.game_slug for feed in feeds}):
        game_feeds = [feed for feed in feeds if feed.game_slug == game_slug]
        game_decisions = [item for item in decisions if item.game == game_slug]
        raw_records = sum(feed.raw_records for feed in game_feeds)
        accepted_records = sum(len(feed.rows) for feed in game_feeds)
        rejected_records = sum(feed.rejected_records for feed in game_feeds)
        classified_records = len(game_decisions)
        unclassified_records = max(0, accepted_records - classified_records)
        lost_records = max(0, raw_records - accepted_records - rejected_records)
        status_counts = Counter(item.status for item in game_decisions)
        mapped = status_counts.get("mapped_print", 0) + status_counts.get("mapped_product_variant", 0)
        unresolved = classified_records - mapped
        conflicts = sum(status_counts.get(status, 0) for status in _CONFLICT_STATUSES)
        singles = sum(len(feed.rows) for feed in game_feeds if feed.product_group == "single")
        non_singles = sum(len(feed.rows) for feed in game_feeds if feed.product_group == "non_single")
        per_game[game_slug] = {
            "raw_records": raw_records,
            "accepted_records": accepted_records,
            "rejected_records": rejected_records,
            "classified_records": classified_records,
            "unclassified_records": unclassified_records,
            "lost_records": lost_records,
            "single_records": singles,
            "non_single_records": non_singles,
            "mapped": mapped,
            "unresolved": unresolved,
            "conflicts": conflicts,
            "mapping_coverage_pct": round((mapped / accepted_records) * 100, 4) if accepted_records else 0.0,
            "status_counts": dict(sorted(status_counts.items())),
            "ready": bool(
                raw_records
                and rejected_records == 0
                and unclassified_records == 0
                and lost_records == 0
                and unresolved == 0
                and conflicts == 0
            ),
        }

    raw_records = sum(feed.raw_records for feed in feeds)
    accepted_records = sum(len(feed.rows) for feed in feeds)
    rejected_records = sum(feed.rejected_records for feed in feeds)
    classified_records = len(decisions)
    unclassified_records = max(0, accepted_records - classified_records)
    lost_records = max(0, raw_records - accepted_records - rejected_records)
    global_status_counts = Counter(item.status for item in decisions)
    mapped = global_status_counts.get("mapped_print", 0) + global_status_counts.get("mapped_product_variant", 0)
    unresolved = classified_records - mapped
    conflicts = sum(global_status_counts.get(status, 0) for status in _CONFLICT_STATUSES)

    summary = {
        "source": CARDMARKET_SOURCE,
        "games": per_game,
        "raw_records": raw_records,
        "accepted_records": accepted_records,
        "rejected_records": rejected_records,
        "classified_records": classified_records,
        "unclassified_records": unclassified_records,
        "lost_records": lost_records,
        "mapped": mapped,
        "unresolved": unresolved,
        "conflicts": conflicts,
        "mapping_coverage_pct": round((mapped / accepted_records) * 100, 4) if accepted_records else 0.0,
        "status_counts": dict(sorted(global_status_counts.items())),
        "ready": bool(
            raw_records
            and rejected_records == 0
            and unclassified_records == 0
            and lost_records == 0
            and unresolved == 0
            and conflicts == 0
        ),
        "write_mode": "disabled",
    }
    return summary, decisions
