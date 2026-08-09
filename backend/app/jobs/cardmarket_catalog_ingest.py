from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Iterable

from sqlalchemy import select

from app.external_catalog_models import ExternalCatalogProduct
from app.jobs.cardmarket_master_inventory import CatalogFeed
from app.models import Game


CARDMARKET_SOURCE = "cardmarket"


@dataclass(frozen=True)
class CatalogIngestPlan:
    seen_at: datetime
    source: str
    feed_count: int
    raw_records: int
    accepted_records: int
    rejected_records: int
    unique_products: int
    inserts: int
    updates: int
    unchanged: int
    conflicts: tuple[dict, ...]
    rows: tuple[dict, ...]

    def summary(self) -> dict:
        return {
            "source": self.source,
            "seen_at": self.seen_at.isoformat(),
            "feed_count": self.feed_count,
            "raw_records": self.raw_records,
            "accepted_records": self.accepted_records,
            "rejected_records": self.rejected_records,
            "unique_products": self.unique_products,
            "inserts": self.inserts,
            "updates": self.updates,
            "unchanged": self.unchanged,
            "conflicts": len(self.conflicts),
            "write_rows": len(self.rows),
            "write_ready": self.rejected_records == 0 and not self.conflicts,
        }


def _date(value: str | None) -> date | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    candidates = [raw, raw[:10]]
    for candidate in candidates:
        try:
            return date.fromisoformat(candidate)
        except ValueError:
            continue
    return None


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _comparable(payload: dict) -> tuple:
    return (
        payload["game_id"],
        payload["product_group"],
        payload["name"],
        payload["category_id"],
        payload["category"],
        payload["expansion_external_id"],
        payload["date_added"],
        payload["metacard_external_id"],
        payload["source_updated_at"],
    )


def build_catalog_ingest_plan(
    session,
    feeds: Iterable[CatalogFeed],
    *,
    seen_at: datetime | None = None,
    source: str = CARDMARKET_SOURCE,
) -> CatalogIngestPlan:
    """Build a source-owned catalog upsert plan without writing the database.

    Immutable provenance boundaries are enforced before apply: an existing
    Cardmarket external ID may never silently jump to a different game or from
    single to non-single (or vice versa). Optional source metadata may evolve.
    """
    feeds = list(feeds)
    if not feeds:
        raise ValueError("At least one Cardmarket catalog feed is required")

    seen_at = seen_at or datetime.now(timezone.utc)
    if seen_at.tzinfo is None:
        seen_at = seen_at.replace(tzinfo=timezone.utc)
    seen_at = seen_at.astimezone(timezone.utc)
    source = str(source or "").strip().lower()
    if not source:
        raise ValueError("source is required")

    feed_keys = [(feed.game_slug, feed.product_group) for feed in feeds]
    duplicate_feed_keys = [key for key, count in Counter(feed_keys).items() if count > 1]
    if duplicate_feed_keys:
        raise ValueError(f"Duplicate catalog feeds: {duplicate_feed_keys}")

    game_rows = session.execute(select(Game.id, Game.slug)).all()
    game_ids = {str(slug): int(game_id) for game_id, slug in game_rows}

    product_ids = [row.product_id for feed in feeds for row in feed.rows]
    duplicate_ids = {product_id for product_id, count in Counter(product_ids).items() if count > 1}

    existing_rows = session.execute(
        select(
            ExternalCatalogProduct.external_id,
            ExternalCatalogProduct.game_id,
            ExternalCatalogProduct.product_group,
            ExternalCatalogProduct.name,
            ExternalCatalogProduct.category_id,
            ExternalCatalogProduct.category,
            ExternalCatalogProduct.expansion_external_id,
            ExternalCatalogProduct.date_added,
            ExternalCatalogProduct.metacard_external_id,
            ExternalCatalogProduct.source_updated_at,
        ).where(ExternalCatalogProduct.source == source)
    ).all()
    existing = {
        str(row.external_id): {
            "game_id": int(row.game_id),
            "product_group": str(row.product_group),
            "name": str(row.name),
            "category_id": row.category_id,
            "category": row.category,
            "expansion_external_id": row.expansion_external_id,
            "date_added": row.date_added,
            "metacard_external_id": row.metacard_external_id,
            "source_updated_at": _utc(row.source_updated_at),
        }
        for row in existing_rows
    }

    conflicts: list[dict] = []
    rows: list[dict] = []
    inserts = updates = unchanged = 0

    for feed in feeds:
        game_id = game_ids.get(feed.game_slug)
        if game_id is None:
            conflicts.append({
                "status": "missing_internal_game",
                "game": feed.game_slug,
                "product_group": feed.product_group,
            })
            continue

        source_updated_at = _utc(feed.created_at)
        for row in feed.rows:
            if row.product_id in duplicate_ids:
                conflicts.append({
                    "status": "duplicate_external_id_in_input",
                    "external_id": row.product_id,
                    "game": feed.game_slug,
                    "product_group": feed.product_group,
                })
                continue

            payload = {
                "source": source,
                "external_id": row.product_id,
                "game_id": game_id,
                "product_group": feed.product_group,
                "name": row.name,
                "category_id": row.category_id or None,
                "category": row.category or None,
                "expansion_external_id": row.expansion_id or None,
                "date_added": _date(row.date_added),
                "metacard_external_id": row.metacard_id or None,
                "raw_json": {
                    "idProduct": row.product_id,
                    "idMetacard": row.metacard_id or None,
                    "category_id": row.category_id or None,
                    "expansion_id": row.expansion_id or None,
                    "feed_game": feed.game_slug,
                    "feed_group": feed.product_group,
                },
                "source_updated_at": source_updated_at,
                "last_seen_at": seen_at,
            }
            current = existing.get(row.product_id)
            if current is None:
                inserts += 1
                rows.append(payload)
                continue

            if current["game_id"] != game_id or current["product_group"] != feed.product_group:
                conflicts.append({
                    "status": "immutable_identity_change",
                    "external_id": row.product_id,
                    "existing_game_id": current["game_id"],
                    "incoming_game_id": game_id,
                    "existing_product_group": current["product_group"],
                    "incoming_product_group": feed.product_group,
                })
                continue

            incoming_compare = _comparable(payload)
            current_compare = (
                current["game_id"],
                current["product_group"],
                current["name"],
                current["category_id"],
                current["category"],
                current["expansion_external_id"],
                current["date_added"],
                current["metacard_external_id"],
                current["source_updated_at"],
            )
            if incoming_compare == current_compare:
                unchanged += 1
            else:
                updates += 1
            # last_seen_at must advance even when source content itself did not.
            rows.append(payload)

    return CatalogIngestPlan(
        seen_at=seen_at,
        source=source,
        feed_count=len(feeds),
        raw_records=sum(feed.raw_records for feed in feeds),
        accepted_records=sum(len(feed.rows) for feed in feeds),
        rejected_records=sum(feed.rejected_records for feed in feeds),
        unique_products=len(set(product_ids)),
        inserts=inserts,
        updates=updates,
        unchanged=unchanged,
        conflicts=tuple(conflicts),
        rows=tuple(rows),
    )


def apply_catalog_ingest_plan(session, plan: CatalogIngestPlan) -> dict:
    if plan.rejected_records:
        raise ValueError(f"Refusing Cardmarket catalog apply with {plan.rejected_records} rejected source rows")
    if plan.conflicts:
        raise ValueError(f"Refusing Cardmarket catalog apply with {len(plan.conflicts)} identity conflicts")
    if not plan.rows:
        return {**plan.summary(), "written": 0}

    dialect_name = session.get_bind().dialect.name
    if dialect_name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        update_fields = (
            "name",
            "category_id",
            "category",
            "expansion_external_id",
            "date_added",
            "metacard_external_id",
            "raw_json",
            "source_updated_at",
            "last_seen_at",
        )
        chunk_size = 1000
        for offset in range(0, len(plan.rows), chunk_size):
            chunk = list(plan.rows[offset:offset + chunk_size])
            statement = pg_insert(ExternalCatalogProduct).values(chunk)
            statement = statement.on_conflict_do_update(
                constraint="uq_external_catalog_source_id",
                set_={field: getattr(statement.excluded, field) for field in update_fields},
            )
            session.execute(statement)
    else:
        external_ids = [row["external_id"] for row in plan.rows]
        existing_rows = session.execute(
            select(ExternalCatalogProduct).where(
                ExternalCatalogProduct.source == plan.source,
                ExternalCatalogProduct.external_id.in_(external_ids),
            )
        ).scalars().all()
        existing = {str(row.external_id): row for row in existing_rows}
        for payload in plan.rows:
            row = existing.get(payload["external_id"])
            if row is None:
                session.add(ExternalCatalogProduct(**payload))
                continue
            for field in (
                "name",
                "category_id",
                "category",
                "expansion_external_id",
                "date_added",
                "metacard_external_id",
                "raw_json",
                "source_updated_at",
                "last_seen_at",
            ):
                setattr(row, field, payload[field])

    return {**plan.summary(), "written": len(plan.rows)}
