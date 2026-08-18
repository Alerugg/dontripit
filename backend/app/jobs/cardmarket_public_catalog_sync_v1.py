from __future__ import annotations

import argparse
import hashlib
import json
import time
import urllib.error
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable

from sqlalchemy import func, select

from app import db
from app.external_catalog_models import ExternalCatalogProduct
from app.jobs.cardmarket_catalog_ingest import (
    CARDMARKET_SOURCE,
    apply_catalog_ingest_plan,
    build_catalog_ingest_plan,
)
from app.jobs.cardmarket_master_inventory import CatalogFeed, load_catalog_feed_bytes
from app.models import Game


CARDMARKET_DOWNLOAD_BASE = "https://downloads.s3.cardmarket.com/productCatalog/productList"
CARDMARKET_GAME_IDS = {
    "mtg": 1,
    "yugioh": 3,
    "pokemon": 6,
    "onepiece": 18,
    "riftbound": 22,
}
PRODUCT_GROUP_FILES = {
    "single": "products_singles_{game_id}.json",
    "non_single": "products_nonsingles_{game_id}.json",
}
FULL_SURFACE_KEYS = tuple(
    (game_slug, product_group)
    for game_slug in CARDMARKET_GAME_IDS
    for product_group in PRODUCT_GROUP_FILES
)
APPLY_CONFIRMATION = "APPLY_CARDMARKET_PUBLIC_CATALOG_V1"
USER_AGENT = "DontRipIt-Cardmarket-Catalog/1.0 (+https://github.com/Alerugg/dontripit)"


def catalog_url(game_slug: str, product_group: str) -> str:
    game_slug = str(game_slug or "").strip().lower()
    product_group = str(product_group or "").strip().lower()
    if game_slug not in CARDMARKET_GAME_IDS:
        raise ValueError(f"Unsupported Cardmarket game: {game_slug!r}")
    if product_group not in PRODUCT_GROUP_FILES:
        raise ValueError(f"Unsupported Cardmarket product group: {product_group!r}")
    filename = PRODUCT_GROUP_FILES[product_group].format(game_id=CARDMARKET_GAME_IDS[game_slug])
    return f"{CARDMARKET_DOWNLOAD_BASE}/{filename}"


def resolve_game_slugs(value: str) -> tuple[str, ...]:
    raw = str(value or "all").strip().lower()
    if raw == "all":
        return tuple(CARDMARKET_GAME_IDS)
    requested = tuple(part.strip() for part in raw.split(",") if part.strip())
    if not requested:
        raise ValueError("At least one game is required")
    unsupported = sorted(set(requested) - set(CARDMARKET_GAME_IDS))
    if unsupported:
        raise ValueError(f"Unsupported Cardmarket games: {unsupported}")
    return tuple(dict.fromkeys(requested))


def _fetch_bytes(url: str, *, timeout: int = 180, attempts: int = 3) -> bytes:
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        request = urllib.request.Request(
            url,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                status = int(getattr(response, "status", 200) or 200)
                if status != 200:
                    raise RuntimeError(f"Cardmarket download returned HTTP {status}: {url}")
                content = response.read()
                if not content:
                    raise RuntimeError(f"Cardmarket download returned an empty body: {url}")
                return content
        except (urllib.error.URLError, TimeoutError, RuntimeError) as exc:
            last_error = exc
            if attempt < attempts:
                time.sleep(attempt * 2)
    raise RuntimeError(f"Failed to download Cardmarket public catalog after {attempts} attempts: {url}: {last_error}")


def download_catalog_feeds(
    *,
    game_slugs: Iterable[str],
    output_dir: str | Path | None = None,
    fetcher: Callable[[str], bytes] | None = None,
) -> tuple[list[CatalogFeed], dict]:
    fetcher = fetcher or _fetch_bytes
    output_path = Path(output_dir) if output_dir else None
    if output_path:
        output_path.mkdir(parents=True, exist_ok=True)

    feeds: list[CatalogFeed] = []
    source_files: dict[str, dict] = {}
    for game_slug in game_slugs:
        for product_group in PRODUCT_GROUP_FILES:
            url = catalog_url(game_slug, product_group)
            content = fetcher(url)
            feed = load_catalog_feed_bytes(
                content,
                game_slug=game_slug,
                product_group=product_group,
            )
            if feed.rejected_records:
                raise RuntimeError(
                    f"Refusing Cardmarket source with rejected rows: "
                    f"{game_slug}:{product_group} rejected={feed.rejected_records} raw={feed.raw_records}"
                )
            if len(feed.rows) != feed.raw_records or not feed.rows:
                raise RuntimeError(
                    f"Cardmarket source accounting failed for {game_slug}:{product_group}: "
                    f"raw={feed.raw_records} accepted={len(feed.rows)}"
                )
            feeds.append(feed)
            key = f"{game_slug}:{product_group}"
            filename = PRODUCT_GROUP_FILES[product_group].format(game_id=CARDMARKET_GAME_IDS[game_slug])
            if output_path:
                (output_path / filename).write_bytes(content)
            source_files[key] = {
                "game": game_slug,
                "game_id": CARDMARKET_GAME_IDS[game_slug],
                "product_group": product_group,
                "url": url,
                "filename": filename,
                "sha256": hashlib.sha256(content).hexdigest(),
                "bytes": len(content),
                "raw_records": feed.raw_records,
                "accepted_records": len(feed.rows),
                "rejected_records": feed.rejected_records,
                "created_at": feed.created_at.isoformat() if feed.created_at else None,
            }
    return feeds, source_files


def validate_source_feeds(feeds: Iterable[CatalogFeed], *, require_full_surface: bool) -> dict:
    feeds = list(feeds)
    keys = [(feed.game_slug, feed.product_group) for feed in feeds]
    key_counts = Counter(keys)
    duplicate_keys = sorted(key for key, count in key_counts.items() if count > 1)
    if duplicate_keys:
        raise RuntimeError(f"Duplicate Cardmarket source feeds: {duplicate_keys}")

    if require_full_surface:
        missing = sorted(set(FULL_SURFACE_KEYS) - set(keys))
        extra = sorted(set(keys) - set(FULL_SURFACE_KEYS))
        if missing or extra:
            raise RuntimeError(f"Incomplete Cardmarket full surface: missing={missing} extra={extra}")

    product_ids = [row.product_id for feed in feeds for row in feed.rows]
    duplicate_ids = sorted(product_id for product_id, count in Counter(product_ids).items() if count > 1)
    if duplicate_ids:
        sample = duplicate_ids[:20]
        raise RuntimeError(
            f"Cardmarket source contains duplicate idProduct values across feeds: "
            f"count={len(duplicate_ids)} sample={sample}"
        )

    return {
        "feed_count": len(feeds),
        "product_count": len(product_ids),
        "unique_products": len(set(product_ids)),
        "full_surface": set(keys) == set(FULL_SURFACE_KEYS),
        "keys": [f"{game}:{group}" for game, group in keys],
    }


def current_capture_counts(session) -> tuple[datetime | None, dict[tuple[str, str], int]]:
    capture = session.execute(
        select(func.max(ExternalCatalogProduct.last_seen_at)).where(
            ExternalCatalogProduct.source == CARDMARKET_SOURCE
        )
    ).scalar_one_or_none()
    if capture is None:
        return None, {}
    rows = session.execute(
        select(Game.slug, ExternalCatalogProduct.product_group, func.count(ExternalCatalogProduct.id))
        .join(Game, Game.id == ExternalCatalogProduct.game_id)
        .where(
            ExternalCatalogProduct.source == CARDMARKET_SOURCE,
            ExternalCatalogProduct.last_seen_at == capture,
        )
        .group_by(Game.slug, ExternalCatalogProduct.product_group)
    ).all()
    return capture, {(str(game), str(group)): int(count) for game, group, count in rows}


def validate_no_capture_regression(
    feeds: Iterable[CatalogFeed],
    previous_counts: dict[tuple[str, str], int],
) -> dict:
    incoming = {(feed.game_slug, feed.product_group): len(feed.rows) for feed in feeds}
    regressions = []
    for key, previous in sorted(previous_counts.items()):
        if key not in incoming or key not in FULL_SURFACE_KEYS:
            continue
        current = incoming[key]
        if previous > 0 and current < previous:
            regressions.append(
                {
                    "surface": f"{key[0]}:{key[1]}",
                    "previous": previous,
                    "incoming": current,
                }
            )
    if regressions:
        raise RuntimeError(f"Refusing Cardmarket catalog count regression: {regressions}")
    return {
        "incoming_counts": {f"{game}:{group}": count for (game, group), count in sorted(incoming.items())},
        "previous_counts": {
            f"{game}:{group}": count for (game, group), count in sorted(previous_counts.items())
            if (game, group) in FULL_SURFACE_KEYS
        },
        "regressions": regressions,
    }


def _post_apply_proof(session, *, expected_seen_at: datetime, feeds: Iterable[CatalogFeed]) -> dict:
    capture, counts = current_capture_counts(session)
    expected = {(feed.game_slug, feed.product_group): len(feed.rows) for feed in feeds}
    if capture is None:
        raise RuntimeError("Cardmarket catalog disappeared after apply")
    capture_utc = capture if capture.tzinfo else capture.replace(tzinfo=timezone.utc)
    expected_utc = expected_seen_at if expected_seen_at.tzinfo else expected_seen_at.replace(tzinfo=timezone.utc)
    if capture_utc.astimezone(timezone.utc) != expected_utc.astimezone(timezone.utc):
        raise RuntimeError(f"Unexpected Cardmarket current capture after apply: {capture} != {expected_seen_at}")
    if counts != expected:
        raise RuntimeError(
            f"Cardmarket full-surface proof failed after apply: expected={expected} actual={counts}"
        )
    return {
        "capture": capture_utc.astimezone(timezone.utc).isoformat(),
        "counts": {f"{game}:{group}": count for (game, group), count in sorted(counts.items())},
        "full_surface": True,
    }


def run_sync(
    *,
    game: str = "all",
    output_dir: str | Path | None = None,
    apply: bool = False,
    confirm: str = "",
    source_only: bool = False,
) -> dict:
    game_slugs = resolve_game_slugs(game)
    if apply and game_slugs != tuple(CARDMARKET_GAME_IDS):
        raise RuntimeError(
            "Production Cardmarket catalog apply must include all supported games so the global current capture remains atomic"
        )
    if apply and source_only:
        raise RuntimeError("--apply and --source-only are mutually exclusive")
    if apply and confirm != APPLY_CONFIRMATION:
        raise RuntimeError(f"Production apply requires --confirm {APPLY_CONFIRMATION}")

    feeds, source_files = download_catalog_feeds(game_slugs=game_slugs, output_dir=output_dir)
    source_validation = validate_source_feeds(feeds, require_full_surface=(apply or game == "all"))
    payload = {
        "source": CARDMARKET_SOURCE,
        "mode": "source_only" if source_only else ("apply" if apply else "dry_run"),
        "source_validation": source_validation,
        "source_files": source_files,
    }
    if source_only:
        return payload

    db.init_engine()
    with db.SessionLocal() as session:
        previous_capture, previous_counts = current_capture_counts(session)
        regression = validate_no_capture_regression(feeds, previous_counts)
        seen_at = datetime.now(timezone.utc)
        plan = build_catalog_ingest_plan(session, feeds, seen_at=seen_at)
        plan_summary = plan.summary()
        if plan.rejected_records:
            raise RuntimeError(f"Cardmarket ingest plan rejected {plan.rejected_records} source rows")
        if plan.conflicts:
            raise RuntimeError(f"Cardmarket ingest plan has {len(plan.conflicts)} identity conflicts: {list(plan.conflicts[:20])}")
        if len(plan.rows) != source_validation["product_count"]:
            raise RuntimeError(
                f"Cardmarket ingest plan accounting failed: rows={len(plan.rows)} source={source_validation['product_count']}"
            )

        payload.update(
            {
                "previous_capture": previous_capture.isoformat() if previous_capture else None,
                "capture_regression_gate": regression,
                "ingest_plan": plan_summary,
            }
        )
        if not apply:
            session.rollback()
            return payload

        applied = apply_catalog_ingest_plan(session, plan)
        session.commit()

    with db.SessionLocal() as session:
        proof = _post_apply_proof(session, expected_seen_at=seen_at, feeds=feeds)
        second_plan = build_catalog_ingest_plan(session, feeds, seen_at=seen_at)
        second_summary = second_plan.summary()
        if second_plan.inserts or second_plan.updates or second_plan.conflicts or second_plan.rejected_records:
            raise RuntimeError(f"Cardmarket catalog second-pass proof failed: {second_summary}")
        session.rollback()

    payload.update(
        {
            "applied": applied,
            "post_apply_proof": proof,
            "second_pass": second_summary,
        }
    )
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Download and fail-closed sync Cardmarket's public Product Catalog")
    parser.add_argument("--game", default="all", help="all or comma-separated Don’tRipIt game slugs")
    parser.add_argument("--output-dir", default=None, help="Optional directory for downloaded source files")
    parser.add_argument("--report", default=None, help="Optional JSON report path")
    parser.add_argument("--source-only", action="store_true", help="Validate public downloads without opening the database")
    parser.add_argument("--apply", action="store_true", help="Apply the full five-game catalog atomically")
    parser.add_argument("--confirm", default="", help="Required exact confirmation token for --apply")
    args = parser.parse_args()

    payload = run_sync(
        game=args.game,
        output_dir=args.output_dir,
        apply=args.apply,
        confirm=args.confirm,
        source_only=args.source_only,
    )
    rendered = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if args.report:
        report_path = Path(args.report)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(rendered, encoding="utf-8")
    print(rendered, end="")


if __name__ == "__main__":
    main()
