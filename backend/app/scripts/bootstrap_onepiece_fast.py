from __future__ import annotations

import json
from datetime import date, datetime, timezone

from sqlalchemy import func, select

from app import db
from app.ingest.normalization import normalize_collector_number, normalize_variant
from app.ingest.registry import get_connector
from app.models import (
    Card,
    Game,
    IngestRun,
    Print,
    PrintIdentifier,
    PrintImage,
    Set,
    Source,
    SourceRecord,
    SourceSyncState,
)
from app.scripts.catalog_health import get_catalog_health
from app.scripts.reindex_search import rebuild_search_documents


def _safe_date(value: object) -> date | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        return None


def _normalize_language(value: object) -> str:
    raw = str(value or "").strip().lower()
    if raw in {"", "english", "en-us", "en-gb"}:
        return "en"
    if raw in {"japanese", "ja-jp"}:
        return "ja"
    return raw


def _game_snapshot(health: dict, slug: str) -> dict | None:
    return next((row for row in health.get("games", []) if row.get("slug") == slug), None)


def _prepare_payload(payload: dict) -> dict:
    language = _normalize_language(payload.get("language") or "en")

    sets: dict[str, dict] = {}
    for item in payload.get("sets") or []:
        code = str(item.get("code") or "").strip().lower()
        if not code:
            continue
        sets[code] = {
            "code": code,
            "name": str(item.get("name") or code).strip(),
            "release_date": _safe_date(item.get("release_date")),
        }

    cards: dict[str, dict] = {}
    prints: dict[str, dict] = {}

    for card_item in payload.get("cards") or []:
        card_name = str(card_item.get("name") or "").strip()
        card_key = str(card_item.get("id") or "").strip().lower()
        if not card_name or not card_key:
            continue

        cards.setdefault(card_key, {"card_key": card_key, "name": card_name})

        for print_item in card_item.get("prints") or []:
            set_code = str(print_item.get("set_code") or "").strip().lower()
            collector_raw = str(print_item.get("collector_number") or "").strip()
            collector_norm = normalize_collector_number(collector_raw)
            if not set_code or not collector_norm:
                continue

            if set_code not in sets:
                sets[set_code] = {
                    "code": set_code,
                    "name": set_code.upper(),
                    "release_date": None,
                }

            variant = normalize_variant(print_item.get("variant"))
            print_key = f"onepiece:{set_code}:{collector_norm}:{language}:{variant}"
            candidate = {
                "print_key": print_key,
                "card_key": card_key,
                "set_code": set_code,
                "collector_number": collector_raw,
                "language": language,
                "rarity": str(print_item.get("rarity") or "").strip() or None,
                "variant": variant,
                "external_id": str(print_item.get("id") or "").strip() or None,
                "image_url": str(print_item.get("image_url") or "").strip() or None,
            }

            existing = prints.get(print_key)
            if existing is None:
                prints[print_key] = candidate
            else:
                # Preserve the first canonical identity but backfill richer fields.
                for field in ("rarity", "external_id", "image_url"):
                    if not existing.get(field) and candidate.get(field):
                        existing[field] = candidate[field]

    used_card_keys = {row["card_key"] for row in prints.values()}
    cards = {key: row for key, row in cards.items() if key in used_card_keys}

    return {
        "language": language,
        "sets": list(sets.values()),
        "cards": list(cards.values()),
        "prints": list(prints.values()),
    }


def _assert_onepiece_empty(session) -> None:
    game_id = session.execute(select(Game.id).where(Game.slug == "onepiece")).scalar_one_or_none()
    if game_id is None:
        return
    print_count = session.execute(
        select(func.count(Print.id)).join(Set, Set.id == Print.set_id).where(Set.game_id == game_id)
    ).scalar_one()
    card_count = session.execute(select(func.count(Card.id)).where(Card.game_id == game_id)).scalar_one()
    set_count = session.execute(select(func.count(Set.id)).where(Set.game_id == game_id)).scalar_one()
    if print_count or card_count or set_count:
        raise RuntimeError(
            "Fast One Piece bootstrap is only allowed on an empty One Piece catalog "
            f"(sets={set_count}, cards={card_count}, prints={print_count})."
        )


def _ensure_source(session) -> Source:
    source = session.execute(select(Source).where(Source.name == "onepiece")).scalar_one_or_none()
    if source is None:
        source = Source(
            name="onepiece",
            description="ONE PIECE Card Game catalog source",
        )
        session.add(source)
        session.flush()
    return source


def _insert_catalog(session, prepared: dict) -> dict:
    game = session.execute(select(Game).where(Game.slug == "onepiece")).scalar_one_or_none()
    if game is None:
        game = Game(slug="onepiece", name="ONE PIECE Card Game")
        session.add(game)
        session.flush()

    set_rows = [
        Set(
            game_id=game.id,
            code=item["code"],
            name=item["name"],
            release_date=item["release_date"],
        )
        for item in prepared["sets"]
    ]
    session.add_all(set_rows)
    session.flush()
    set_by_code = {row.code: row for row in set_rows}

    card_rows = [
        Card(
            game_id=game.id,
            name=item["name"],
            card_key=item["card_key"],
        )
        for item in prepared["cards"]
    ]
    session.add_all(card_rows)
    session.flush()
    card_by_key = {row.card_key: row for row in card_rows}

    print_rows: list[Print] = []
    prepared_by_key: dict[str, dict] = {}
    for item in prepared["prints"]:
        set_row = set_by_code.get(item["set_code"])
        card_row = card_by_key.get(item["card_key"])
        if set_row is None or card_row is None:
            continue
        row = Print(
            set_id=set_row.id,
            card_id=card_row.id,
            collector_number=item["collector_number"],
            language=item["language"],
            rarity=item["rarity"],
            is_foil=False,
            variant=item["variant"],
            print_key=item["print_key"],
        )
        print_rows.append(row)
        prepared_by_key[item["print_key"]] = item

    session.add_all(print_rows)
    session.flush()

    images: list[PrintImage] = []
    identifiers: list[PrintIdentifier] = []
    for print_row in print_rows:
        item = prepared_by_key[print_row.print_key]
        if item.get("image_url"):
            images.append(
                PrintImage(
                    print_id=print_row.id,
                    url=item["image_url"],
                    is_primary=True,
                    source="punk_records",
                )
            )
        if item.get("external_id"):
            identifiers.append(
                PrintIdentifier(
                    print_id=print_row.id,
                    source="punk_records",
                    external_id=item["external_id"],
                )
            )

    session.add_all(images)
    session.add_all(identifiers)
    session.flush()

    return {
        "game_id": game.id,
        "sets": len(set_rows),
        "cards": len(card_rows),
        "prints": len(print_rows),
        "images": len(images),
        "identifiers": len(identifiers),
    }


def run_bootstrap() -> dict:
    started_at = datetime.now(timezone.utc)

    # Remote I/O happens before opening a database transaction. This avoids the
    # previous 10+ minute idle-in-transaction window while source data downloads.
    connector = get_connector("onepiece")
    loaded = connector.load(None, fixture=False, incremental=False, limit=None)
    if not loaded:
        raise RuntimeError("One Piece source returned no payload")
    _path, payload, checksum = loaded[0]
    prepared = _prepare_payload(payload)
    if not prepared["prints"]:
        raise RuntimeError("One Piece source normalized to zero prints")

    db.init_engine()
    with db.SessionLocal() as session:
        before_health = get_catalog_health(session, sample_limit=10, runs_limit=5)
        _assert_onepiece_empty(session)

        source = _ensure_source(session)
        ingest_run = IngestRun(
            source_id=source.id,
            started_at=started_at,
            status="running",
            counts_json={},
        )
        session.add(ingest_run)
        session.flush()

        try:
            inserted = _insert_catalog(session, prepared)
            now = datetime.now(timezone.utc)

            existing_source_record = session.execute(
                select(SourceRecord).where(
                    SourceRecord.source_id == source.id,
                    SourceRecord.checksum == checksum,
                )
            ).scalar_one_or_none()
            if existing_source_record is None:
                session.add(
                    SourceRecord(
                        source_id=source.id,
                        checksum=checksum,
                        raw_json=payload,
                    )
                )

            sync_state = session.execute(
                select(SourceSyncState).where(SourceSyncState.source_id == source.id)
            ).scalar_one_or_none()
            cursor = {
                "strategy": "fast_empty_catalog_bootstrap",
                "last_synced_at": now.isoformat(),
                "source_payload_checksum": checksum,
            }
            if sync_state is None:
                session.add(
                    SourceSyncState(
                        source_id=source.id,
                        cursor_json=cursor,
                        last_run_at=now,
                    )
                )
            else:
                sync_state.cursor_json = cursor
                sync_state.last_run_at = now

            ingest_run.finished_at = now
            ingest_run.status = "success"
            ingest_run.counts_json = {
                "files_seen": 1,
                "skipped": 0,
                "inserted": sum(
                    inserted[key]
                    for key in ("sets", "cards", "prints", "images", "identifiers")
                ) + 1,
                "updated": 0,
                "errors": 0,
            }
            session.commit()
        except Exception as exc:
            session.rollback()
            raise RuntimeError(f"One Piece fast bootstrap rolled back: {exc}") from exc

    # Search indexing is deliberately a separate transaction. The canonical
    # catalog is valid even if a derived search index rebuild needs a retry.
    search_stats = None
    search_error = None
    try:
        with db.SessionLocal() as search_session:
            search_stats = rebuild_search_documents(search_session)
            search_session.commit()
    except Exception as exc:  # noqa: BLE001
        search_error = f"{type(exc).__name__}: {exc}"

    with db.SessionLocal() as session:
        after_health = get_catalog_health(session, sample_limit=20, runs_limit=10)

    return {
        "source": "onepiece",
        "strategy": "fast_empty_catalog_bootstrap",
        "started_at": started_at.isoformat(),
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "source_payload": {
            "sets": len(payload.get("sets") or []),
            "cards": len(payload.get("cards") or []),
            "checksum": checksum,
        },
        "prepared": {
            "sets": len(prepared["sets"]),
            "cards": len(prepared["cards"]),
            "prints": len(prepared["prints"]),
        },
        "inserted": inserted,
        "search": {
            "ok": search_error is None,
            "stats": search_stats,
            "error": search_error,
        },
        "before": _game_snapshot(before_health, "onepiece"),
        "after": _game_snapshot(after_health, "onepiece"),
        "catalog_totals_after": after_health.get("totals"),
    }


def main() -> int:
    payload = run_bootstrap()
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))

    after = payload.get("after") or {}
    counts = after.get("counts") or {}
    if int(counts.get("cards") or 0) <= 0 or int(counts.get("prints") or 0) <= 0:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
