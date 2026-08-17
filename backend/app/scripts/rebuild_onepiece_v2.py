from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy import delete, func, select

from app import db
from app.catalog_release_models import CatalogRelease, PrintRelease
from app.ingest.connectors.onepiece_v2 import OnePieceV2Connector
from app.ingest.normalization import normalize_collector_number, normalize_variant
from app.models import (
    Card,
    FieldProvenance,
    Game,
    Price,
    PriceDailyOHLC,
    PriceSnapshot,
    Print,
    PrintIdentifier,
    PrintImage,
    Product,
    SearchDocument,
    Set,
)
from app.scripts.catalog_health import get_catalog_health
from app.scripts.reindex_search import rebuild_search_documents

EXPECTED_APPEARANCES = 4673


def _game_snapshot(health: dict, slug: str) -> dict | None:
    return next((row for row in health.get("games", []) if row.get("slug") == slug), None)


def _count(session, statement) -> int:
    return int(session.execute(statement).scalar_one() or 0)


def _assert_safe_for_rebuild(session, game: Game) -> dict[str, int]:
    print_ids = select(Print.id).join(Set, Set.id == Print.set_id).where(Set.game_id == game.id)
    card_ids = select(Card.id).where(Card.game_id == game.id)
    set_ids = select(Set.id).where(Set.game_id == game.id)

    counts = {
        "prices_by_game": _count(session, select(func.count(Price.id)).where(Price.game_id == game.id)),
        "prices_by_print": _count(session, select(func.count(Price.id)).where(Price.print_id.in_(print_ids))),
        "prices_by_card": _count(session, select(func.count(Price.id)).where(Price.card_id.in_(card_ids))),
        "price_snapshots_print": _count(
            session,
            select(func.count(PriceSnapshot.id)).where(
                PriceSnapshot.entity_type == "print", PriceSnapshot.entity_id.in_(print_ids)
            ),
        ),
        "price_snapshots_card": _count(
            session,
            select(func.count(PriceSnapshot.id)).where(
                PriceSnapshot.entity_type == "card", PriceSnapshot.entity_id.in_(card_ids)
            ),
        ),
        "price_daily_ohlc_print": _count(
            session,
            select(func.count(PriceDailyOHLC.id)).where(
                PriceDailyOHLC.entity_type == "print", PriceDailyOHLC.entity_id.in_(print_ids)
            ),
        ),
        "price_daily_ohlc_card": _count(
            session,
            select(func.count(PriceDailyOHLC.id)).where(
                PriceDailyOHLC.entity_type == "card", PriceDailyOHLC.entity_id.in_(card_ids)
            ),
        ),
        "products": _count(session, select(func.count(Product.id)).where(Product.game_id == game.id)),
        "products_linked_to_sets": _count(session, select(func.count(Product.id)).where(Product.set_id.in_(set_ids))),
    }
    blockers = {key: value for key, value in counts.items() if value > 0}
    if blockers:
        raise RuntimeError(f"One Piece V2 rebuild blocked by valuable dependencies: {blockers}")
    return counts


def _validate_payload(payload: dict) -> dict:
    diagnostics = payload.get("diagnostics") or {}
    cards = payload.get("cards") or []
    releases = payload.get("releases") or []
    prints = [row for card in cards for row in card.get("prints") or []]
    links = [
        appearance
        for print_row in prints
        for appearance in print_row.get("release_appearances") or []
    ]

    counts = {
        "sets": len(payload.get("sets") or []),
        "cards": len(cards),
        "prints": len(prints),
        "releases": len(releases),
        "release_links": len(links),
        "physical_identity_conflicts": len(diagnostics.get("physical_identity_conflicts") or []),
    }
    if counts["cards"] <= 0 or counts["prints"] <= 0 or counts["releases"] <= 0:
        raise RuntimeError(f"One Piece V2 payload is empty/incomplete: {counts}")
    if counts["release_links"] != EXPECTED_APPEARANCES:
        raise RuntimeError(
            f"One Piece V2 source coverage changed: expected {EXPECTED_APPEARANCES} appearances, "
            f"got {counts['release_links']}"
        )
    if counts["physical_identity_conflicts"] != 0:
        raise RuntimeError(f"One Piece V2 physical identity conflicts remain: {counts}")
    return counts


def _delete_existing_catalog(session, game: Game) -> dict[str, int]:
    old_print_ids = set(
        session.execute(
            select(Print.id).join(Set, Set.id == Print.set_id).where(Set.game_id == game.id)
        ).scalars().all()
    )
    old_card_ids = set(session.execute(select(Card.id).where(Card.game_id == game.id)).scalars().all())
    old_set_ids = set(session.execute(select(Set.id).where(Set.game_id == game.id)).scalars().all())

    session.execute(delete(SearchDocument).where(SearchDocument.game_id == game.id))
    if old_print_ids:
        session.execute(
            delete(FieldProvenance).where(
                FieldProvenance.entity_type == "print", FieldProvenance.entity_id.in_(old_print_ids)
            )
        )
        session.execute(delete(PrintIdentifier).where(PrintIdentifier.print_id.in_(old_print_ids)))
        session.execute(delete(PrintImage).where(PrintImage.print_id.in_(old_print_ids)))
        session.execute(delete(Print).where(Print.id.in_(old_print_ids)))
    if old_card_ids:
        session.execute(
            delete(FieldProvenance).where(
                FieldProvenance.entity_type == "card", FieldProvenance.entity_id.in_(old_card_ids)
            )
        )
        session.execute(delete(Card).where(Card.id.in_(old_card_ids)))
    session.execute(delete(CatalogRelease).where(CatalogRelease.game_id == game.id))
    if old_set_ids:
        session.execute(delete(Set).where(Set.id.in_(old_set_ids)))

    return {
        "sets": len(old_set_ids),
        "cards": len(old_card_ids),
        "prints": len(old_print_ids),
    }


def _insert_v2(session, game: Game, payload: dict) -> dict[str, int]:
    language = str(payload.get("language") or "en").strip().lower()

    set_rows = []
    for item in payload.get("sets") or []:
        code = str(item.get("code") or "").strip().lower()
        if not code:
            continue
        set_rows.append(
            Set(
                game_id=game.id,
                code=code,
                name=str(item.get("name") or code).strip(),
                release_date=None,
            )
        )
    session.add_all(set_rows)
    session.flush()
    set_by_code = {row.code: row for row in set_rows}

    card_rows = []
    source_cards = {}
    for item in payload.get("cards") or []:
        card_key = str(item.get("id") or "").strip().lower()
        name = str(item.get("name") or "").strip()
        if not card_key or not name:
            continue
        row = Card(game_id=game.id, name=name, card_key=card_key)
        card_rows.append(row)
        source_cards[card_key] = item
    session.add_all(card_rows)
    session.flush()
    card_by_key = {row.card_key: row for row in card_rows}

    print_rows = []
    print_source_by_key: dict[str, dict] = {}
    for card_key, card_item in source_cards.items():
        card_row = card_by_key[card_key]
        for item in card_item.get("prints") or []:
            set_code = str(item.get("set_code") or "").strip().lower()
            set_row = set_by_code.get(set_code)
            if set_row is None:
                raise RuntimeError(f"Missing canonical set for One Piece print: {set_code}")
            collector = str(item.get("collector_number") or "").strip()
            variant = normalize_variant(item.get("variant"))
            identity = str(item.get("identity_key") or "").strip().lower()
            if not identity:
                identity = ":".join(
                    [set_code, normalize_collector_number(collector), language, variant]
                )
            print_key = f"onepiece:{identity}"
            row = Print(
                set_id=set_row.id,
                card_id=card_row.id,
                collector_number=collector,
                language=language,
                rarity=str(item.get("rarity") or "").strip() or None,
                is_foil=False,
                variant=variant,
                print_key=print_key,
            )
            print_rows.append(row)
            print_source_by_key[print_key] = item
    session.add_all(print_rows)
    session.flush()
    print_by_key = {row.print_key: row for row in print_rows}

    image_rows = []
    identifier_rows = []
    for print_key, row in print_by_key.items():
        item = print_source_by_key[print_key]
        image_url = str(item.get("image_url") or "").strip()
        external_id = str(item.get("id") or "").strip()
        if image_url:
            image_rows.append(
                PrintImage(
                    print_id=row.id,
                    url=image_url,
                    is_primary=True,
                    source="onepiece_official",
                )
            )
        if external_id:
            identifier_rows.append(
                PrintIdentifier(
                    print_id=row.id,
                    source="onepiece_official",
                    external_id=external_id,
                )
            )
    session.add_all(image_rows)
    session.add_all(identifier_rows)

    release_rows = []
    for item in payload.get("releases") or []:
        release_rows.append(
            CatalogRelease(
                game_id=game.id,
                source=str(item.get("source") or "onepiece_official"),
                external_id=str(item.get("external_id") or "").strip(),
                name=str(item.get("name") or "").strip(),
                code=str(item.get("code") or "").strip() or None,
                release_type=str(item.get("release_type") or "").strip() or None,
                release_date=None,
                language=str(item.get("language") or language).strip().lower() or language,
                region=str(item.get("region") or "global-en").strip() or None,
                metadata_json=item.get("metadata_json"),
            )
        )
    session.add_all(release_rows)
    session.flush()
    release_by_external_id = {row.external_id: row for row in release_rows}

    link_rows = []
    for print_key, print_row in print_by_key.items():
        item = print_source_by_key[print_key]
        for appearance in item.get("release_appearances") or []:
            release_id = str(appearance.get("release_external_id") or "").strip()
            release_row = release_by_external_id.get(release_id)
            if release_row is None:
                raise RuntimeError(f"Missing CatalogRelease for appearance: {release_id}")
            link_rows.append(
                PrintRelease(
                    print_id=print_row.id,
                    release_id=release_row.id,
                    source_print_id=str(appearance.get("source_print_id") or "").strip() or None,
                    appearance_type="official_cardlist",
                    metadata_json=None,
                )
            )
    session.add_all(link_rows)
    session.flush()

    return {
        "sets": len(set_rows),
        "cards": len(card_rows),
        "prints": len(print_rows),
        "images": len(image_rows),
        "identifiers": len(identifier_rows),
        "releases": len(release_rows),
        "release_links": len(link_rows),
    }


def _verify_db(session, game: Game) -> dict[str, int]:
    counts = {
        "sets": _count(session, select(func.count(Set.id)).where(Set.game_id == game.id)),
        "cards": _count(session, select(func.count(Card.id)).where(Card.game_id == game.id)),
        "prints": _count(
            session,
            select(func.count(Print.id)).join(Set, Set.id == Print.set_id).where(Set.game_id == game.id),
        ),
        "releases": _count(
            session, select(func.count(CatalogRelease.id)).where(CatalogRelease.game_id == game.id)
        ),
        "release_links": _count(
            session,
            select(func.count(PrintRelease.id))
            .join(CatalogRelease, CatalogRelease.id == PrintRelease.release_id)
            .where(CatalogRelease.game_id == game.id),
        ),
    }
    expected = {
        "sets": 59,
        "cards": 2665,
        "prints": 4672,
        "releases": 59,
        "release_links": 4673,
    }
    if counts != expected:
        raise RuntimeError(f"One Piece V2 DB verification failed. expected={expected} actual={counts}")
    return counts


def run_rebuild() -> dict:
    started_at = datetime.now(timezone.utc)

    # All remote I/O happens before opening the write transaction.
    connector = OnePieceV2Connector()
    payload = connector._load_official_cardlist_remote(limit=None)
    source_counts = _validate_payload(payload)

    db.init_engine()
    with db.SessionLocal() as session:
        before_health = get_catalog_health(session, sample_limit=20, runs_limit=10)
        game = session.execute(select(Game).where(Game.slug == "onepiece")).scalar_one_or_none()
        if game is None:
            game = Game(slug="onepiece", name="ONE PIECE Card Game")
            session.add(game)
            session.flush()

        safety_counts = _assert_safe_for_rebuild(session, game)
        deleted = _delete_existing_catalog(session, game)
        inserted = _insert_v2(session, game, payload)
        verified = _verify_db(session, game)
        session.commit()

    search_error = None
    search_stats = None
    try:
        with db.SessionLocal() as search_session:
            search_stats = rebuild_search_documents(search_session)
            search_session.commit()
    except Exception as exc:  # noqa: BLE001
        search_error = f"{type(exc).__name__}: {exc}"

    with db.SessionLocal() as session:
        after_health = get_catalog_health(session, sample_limit=30, runs_limit=10)

    return {
        "strategy": "onepiece_canonical_v2_rebuild",
        "started_at": started_at.isoformat(),
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "source_counts": source_counts,
        "safety_counts": safety_counts,
        "deleted": deleted,
        "inserted": inserted,
        "verified": verified,
        "search": {"ok": search_error is None, "stats": search_stats, "error": search_error},
        "before": _game_snapshot(before_health, "onepiece"),
        "after": _game_snapshot(after_health, "onepiece"),
        "catalog_totals_after": after_health.get("totals"),
    }


def main() -> int:
    payload = run_rebuild()
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    return 0 if payload.get("search", {}).get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
