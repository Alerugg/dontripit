from __future__ import annotations

import argparse
from collections import Counter
from datetime import date, datetime, timezone
import json
import os
from pathlib import Path
import re
from typing import Any

import requests
from sqlalchemy import create_engine, text

from app.jobs.regional_content import (
    CANONICAL_SOURCE_KEYS,
    DEPRECATED_SOURCE_KEYS,
    SOURCES,
    TPCI_SCHEDULE_URL,
    TPCI_SOURCE_KEY,
    USER_AGENT,
    _fetch_tpci_eu_schedule,
    scrape_source,
)


ACTIVE_GAMES = ("pokemon", "onepiece", "mtg", "yugioh")
ACTIVE_REGIONS = ("jp", "eu", "us")


def _normal_title(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip().casefold()


def _iso(value: date | None) -> str | None:
    return value.isoformat() if value is not None else None


def _date_value(value: Any) -> date | None:
    if value is None or isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def _json_object(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    try:
        return dict(value)
    except (TypeError, ValueError):
        return {}


def _record_for_source(source, item: dict[str, Any], region: str) -> dict[str, Any]:
    return {
        "game": source.game,
        "region": region,
        "locale": source.locale,
        "kind": item["kind"],
        "source_key": source.key,
        "source_name": source.name,
        "source_url": source.url,
        "item_url": item["item_url"],
        "title": item["title"][:1000],
        "published_date": item["published_date"],
        "release_date": item["release_date"],
        "raw_json": {
            "official": True,
            "source_context": item["source_context"],
            "regions": list(source.regions),
        },
    }


def _record_for_tpci(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "game": "pokemon",
        "region": "eu",
        "locale": "en-GB",
        "kind": "release",
        "source_key": TPCI_SOURCE_KEY,
        "source_name": "The Pokemon Company International Official Press Site",
        "source_url": TPCI_SCHEDULE_URL,
        "item_url": row["item_url"],
        "title": row["title"][:1000],
        "published_date": None,
        "release_date": row["release_date"],
        "raw_json": {
            "official": True,
            "regional_basis": "tpci_manages_pokemon_outside_asia",
            "feed_role": "europe_product_release_schedule",
            "raw_cells": row["raw_cells"],
        },
    }


def _dedupe_winner(records: list[dict[str, Any]]) -> dict[str, Any]:
    def score(record: dict[str, Any]) -> tuple[Any, ...]:
        release = _date_value(record.get("release_date"))
        published = _date_value(record.get("published_date"))
        return (
            release is not None,
            published is not None,
            release or published or date.min,
            record.get("kind") == "release",
            record.get("kind") == "product",
            -len(str(record.get("item_url") or "")),
            str(record.get("item_url") or ""),
        )

    return max(records, key=score)


def _dedupe_records(records: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for record in records:
        key = (record["game"], record["region"], _normal_title(record["title"]))
        groups.setdefault(key, []).append(record)

    kept: list[dict[str, Any]] = []
    decisions: list[dict[str, Any]] = []
    for key, candidates in sorted(groups.items()):
        if len(candidates) == 1:
            kept.append(candidates[0])
            continue
        winner = dict(_dedupe_winner(candidates))
        loser_urls = sorted(
            str(record["item_url"])
            for record in candidates
            if record["item_url"] != winner["item_url"]
        )
        winner["raw_json"] = {
            **_json_object(winner.get("raw_json")),
            "deduplicated_alias_urls": loser_urls,
        }
        kept.append(winner)
        decisions.append(
            {
                "game": key[0],
                "region": key[1],
                "normalized_title": key[2],
                "keep_url": winner["item_url"],
                "remove_urls": loser_urls,
            }
        )

    kept.sort(key=lambda row: (row["game"], row["region"], row["source_key"], row["item_url"]))
    return kept, decisions


def _source_report(source, items: list[dict[str, Any]]) -> dict[str, Any]:
    published = sorted(item["published_date"] for item in items if item["published_date"] is not None)
    released = sorted(item["release_date"] for item in items if item["release_date"] is not None)
    kinds = Counter(str(item["kind"]) for item in items)
    return {
        "source": source.key,
        "game": source.game,
        "regions": list(source.regions),
        "locale": source.locale,
        "items": len(items),
        "kinds": dict(sorted(kinds.items())),
        "published_dates": {
            "count": len(published),
            "min": _iso(published[0]) if published else None,
            "max": _iso(published[-1]) if published else None,
        },
        "release_dates": {
            "count": len(released),
            "min": _iso(released[0]) if released else None,
            "max": _iso(released[-1]) if released else None,
        },
        "ok": True,
    }


def collect_official_content(*, strict: bool = True) -> dict[str, Any]:
    http = requests.Session()
    http.headers.update(
        {
            "User-Agent": USER_AGENT,
            "Accept-Language": "en-US,en;q=0.8,ja;q=0.6,es;q=0.5",
        }
    )
    fetched_at = datetime.now(timezone.utc)
    raw_records: list[dict[str, Any]] = []
    source_reports: list[dict[str, Any]] = []
    failed_sources: list[str] = []

    for source in SOURCES:
        try:
            items = scrape_source(source, http=http)
            if not items:
                raise ValueError("source yielded zero candidate items")
            raw_records.extend(
                _record_for_source(source, item, region)
                for item in items
                for region in source.regions
            )
            source_reports.append(_source_report(source, items))
        except Exception as exc:
            failed_sources.append(source.key)
            source_reports.append(
                {
                    "source": source.key,
                    "game": source.game,
                    "regions": list(source.regions),
                    "locale": source.locale,
                    "items": 0,
                    "kinds": {},
                    "published_dates": {"count": 0, "min": None, "max": None},
                    "release_dates": {"count": 0, "min": None, "max": None},
                    "ok": False,
                    "error": str(exc),
                }
            )

    try:
        rows = _fetch_tpci_eu_schedule(http)
        raw_records.extend(_record_for_tpci(row) for row in rows)
        released = sorted(row["release_date"] for row in rows if row["release_date"] is not None)
        source_reports.append(
            {
                "source": TPCI_SOURCE_KEY,
                "game": "pokemon",
                "regions": ["eu"],
                "locale": "en-GB",
                "items": len(rows),
                "kinds": {"release": len(rows)},
                "published_dates": {"count": 0, "min": None, "max": None},
                "release_dates": {
                    "count": len(released),
                    "min": _iso(released[0]) if released else None,
                    "max": _iso(released[-1]) if released else None,
                },
                "ok": True,
                "regional_basis": "official_tpci_product_schedule_for_market_outside_asia",
            }
        )
    except Exception as exc:
        failed_sources.append(TPCI_SOURCE_KEY)
        source_reports.append(
            {
                "source": TPCI_SOURCE_KEY,
                "game": "pokemon",
                "regions": ["eu"],
                "locale": "en-GB",
                "items": 0,
                "kinds": {},
                "published_dates": {"count": 0, "min": None, "max": None},
                "release_dates": {"count": 0, "min": None, "max": None},
                "ok": False,
                "error": str(exc),
            }
        )

    if strict and failed_sources:
        raise RuntimeError(f"Official regional sources failed: {failed_sources}; reports={source_reports}")

    identity_keys = [(r["source_key"], r["region"], r["item_url"]) for r in raw_records]
    if len(identity_keys) != len(set(identity_keys)):
        raise RuntimeError("Official regional collection produced duplicate source/region/item identities")

    records, dedupe_decisions = _dedupe_records(raw_records)
    seen_sources = {report["source"] for report in source_reports if report.get("ok")}
    if strict and seen_sources != set(CANONICAL_SOURCE_KEYS):
        raise RuntimeError(
            f"Canonical source registry mismatch: missing={sorted(set(CANONICAL_SOURCE_KEYS) - seen_sources)}"
        )

    return {
        "fetched_at": fetched_at.isoformat(),
        "canonical_sources": len(CANONICAL_SOURCE_KEYS),
        "failed_sources": failed_sources,
        "raw_records": len(raw_records),
        "records": records,
        "deduplicated_records": len(raw_records) - len(records),
        "dedupe_decisions": dedupe_decisions,
        "source_reports": source_reports,
    }


def _material_state(record: dict[str, Any], *, game_id: int, current: dict[str, Any] | None = None) -> dict[str, Any]:
    published = _date_value(record.get("published_date"))
    released = _date_value(record.get("release_date"))
    if current is not None:
        published = published or _date_value(current.get("published_date"))
        released = released or _date_value(current.get("release_date"))
    return {
        "game_id": game_id,
        "locale": str(record["locale"]),
        "kind": str(record["kind"]),
        "source_name": str(record["source_name"]),
        "source_url": str(record["source_url"]),
        "title": str(record["title"]),
        "published_date": published,
        "release_date": released,
        "raw_json": _json_object(record.get("raw_json")),
    }


def _same_material(current: dict[str, Any], target: dict[str, Any]) -> bool:
    return (
        int(current["game_id"]) == int(target["game_id"])
        and str(current["locale"]) == str(target["locale"])
        and str(current["kind"]) == str(target["kind"])
        and str(current["source_name"]) == str(target["source_name"])
        and str(current["source_url"]) == str(target["source_url"])
        and str(current["title"]) == str(target["title"])
        and _date_value(current.get("published_date")) == target["published_date"]
        and _date_value(current.get("release_date")) == target["release_date"]
        and _json_object(current.get("raw_json")) == target["raw_json"]
    )


def apply_records(records: list[dict[str, Any]]) -> dict[str, Any]:
    database_url = os.getenv("DATABASE_URL_UNPOOLED") or os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL_UNPOOLED or DATABASE_URL is required")

    engine = create_engine(database_url)
    now = datetime.now(timezone.utc)
    inserted = 0
    updated = 0
    unchanged = 0

    with engine.begin() as conn:
        game_ids = {
            str(row.slug): int(row.id)
            for row in conn.execute(
                text("SELECT slug,id FROM games WHERE slug IN ('pokemon','onepiece','mtg','yugioh')")
            )
        }
        missing = sorted(set(ACTIVE_GAMES) - set(game_ids))
        if missing:
            raise RuntimeError(f"Missing canonical games for regional content: {missing}")

        for record in records:
            current_row = conn.execute(
                text(
                    """
                    SELECT id, game_id, locale, kind, source_name, source_url, title,
                           published_date, release_date, raw_json
                    FROM regional_tcg_content
                    WHERE source_key = :source_key
                      AND region = :region
                      AND item_url = :item_url
                    """
                ),
                {
                    "source_key": record["source_key"],
                    "region": record["region"],
                    "item_url": record["item_url"],
                },
            ).mappings().one_or_none()
            current = dict(current_row) if current_row is not None else None
            target = _material_state(record, game_id=game_ids[record["game"]], current=current)

            if current is None:
                conn.execute(
                    text(
                        """
                        INSERT INTO regional_tcg_content
                          (game_id, region, locale, kind, source_key, source_name, source_url,
                           item_url, title, published_date, release_date, raw_json,
                           first_seen_at, last_seen_at)
                        VALUES
                          (:game_id, :region, :locale, :kind, :source_key, :source_name, :source_url,
                           :item_url, :title, :published_date, :release_date, CAST(:raw_json AS jsonb),
                           :now, :now)
                        """
                    ),
                    {
                        **target,
                        "region": record["region"],
                        "source_key": record["source_key"],
                        "item_url": record["item_url"],
                        "raw_json": json.dumps(target["raw_json"], ensure_ascii=False, sort_keys=True),
                        "now": now,
                    },
                )
                inserted += 1
                continue

            if _same_material(current, target):
                unchanged += 1
                continue

            conn.execute(
                text(
                    """
                    UPDATE regional_tcg_content
                    SET game_id = :game_id,
                        locale = :locale,
                        kind = :kind,
                        source_name = :source_name,
                        source_url = :source_url,
                        title = :title,
                        published_date = :published_date,
                        release_date = :release_date,
                        raw_json = CAST(:raw_json AS jsonb),
                        last_seen_at = :now
                    WHERE id = :id
                    """
                ),
                {
                    **target,
                    "id": int(current["id"]),
                    "raw_json": json.dumps(target["raw_json"], ensure_ascii=False, sort_keys=True),
                    "now": now,
                },
            )
            updated += 1

    return {
        "applied_at": now.isoformat(),
        "records": len(records),
        "inserted": inserted,
        "updated": updated,
        "unchanged": unchanged,
        "material_writes": inserted + updated,
    }


def cleanup_deprecated_sources() -> dict[str, int]:
    database_url = os.getenv("DATABASE_URL_UNPOOLED") or os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL_UNPOOLED or DATABASE_URL is required")
    engine = create_engine(database_url)
    with engine.begin() as conn:
        deleted = int(
            conn.execute(
                text("DELETE FROM regional_tcg_content WHERE source_key = ANY(:keys) RETURNING id"),
                {"keys": list(DEPRECATED_SOURCE_KEYS)},
            ).rowcount
            or 0
        )
    return {"deleted_deprecated_rows": deleted}


def _expected_sources() -> dict[str, dict[str, Any]]:
    expected = {
        source.key: {
            "game": source.game,
            "regions": set(source.regions),
            "locale": source.locale,
            "source_url": source.url,
        }
        for source in SOURCES
    }
    expected[TPCI_SOURCE_KEY] = {
        "game": "pokemon",
        "regions": {"eu"},
        "locale": "en-GB",
        "source_url": TPCI_SCHEDULE_URL,
    }
    return expected


def verify_database() -> dict[str, Any]:
    database_url = os.getenv("DATABASE_URL_UNPOOLED") or os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL_UNPOOLED or DATABASE_URL is required")
    expected = _expected_sources()
    engine = create_engine(database_url)
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT g.slug, c.region, c.locale, c.kind, c.source_key, c.source_url,
                       c.item_url, c.title, c.raw_json
                FROM regional_tcg_content c
                JOIN games g ON g.id = c.game_id
                WHERE c.source_key = ANY(:keys)
                ORDER BY g.slug, c.region, c.source_key, c.item_url
                """
            ),
            {"keys": list(CANONICAL_SOURCE_KEYS)},
        ).mappings().all()
        deprecated = int(
            conn.execute(
                text("SELECT count(*) FROM regional_tcg_content WHERE source_key = ANY(:keys)"),
                {"keys": list(DEPRECATED_SOURCE_KEYS)},
            ).scalar_one()
        )
        riftbound = int(
            conn.execute(
                text(
                    """
                    SELECT count(*) FROM regional_tcg_content c
                    JOIN games g ON g.id = c.game_id
                    WHERE g.slug = 'riftbound'
                    """
                )
            ).scalar_one()
        )
        duplicate_rows = conn.execute(
            text(
                """
                SELECT g.slug, c.region,
                       lower(regexp_replace(trim(c.title), '\\s+', ' ', 'g')) AS normalized_title,
                       count(*) AS copies
                FROM regional_tcg_content c
                JOIN games g ON g.id = c.game_id
                WHERE c.source_key = ANY(:keys)
                GROUP BY g.slug, c.region,
                         lower(regexp_replace(trim(c.title), '\\s+', ' ', 'g'))
                HAVING count(*) > 1
                ORDER BY g.slug, c.region, normalized_title
                """
            ),
            {"keys": list(CANONICAL_SOURCE_KEYS)},
        ).mappings().all()

    seen_sources: set[str] = set()
    cells: set[tuple[str, str]] = set()
    invalid: list[dict[str, Any]] = []
    nonofficial = 0
    blank_titles = 0
    for row in rows:
        source_key = str(row["source_key"])
        game = str(row["slug"])
        region = str(row["region"])
        seen_sources.add(source_key)
        cells.add((game, region))
        source = expected.get(source_key)
        if (
            source is None
            or game != source["game"]
            or region not in source["regions"]
            or str(row["locale"]) != source["locale"]
            or str(row["source_url"] or "") != source["source_url"]
            or not str(row["item_url"] or "").startswith(("https://", "http://"))
        ):
            invalid.append(
                {
                    "game": game,
                    "region": region,
                    "source_key": source_key,
                    "item_url": str(row["item_url"] or ""),
                }
            )
        raw_json = _json_object(row["raw_json"])
        if raw_json.get("official") is not True:
            nonofficial += 1
        if not str(row["title"] or "").strip():
            blank_titles += 1

    required_cells = {(game, region) for game in ACTIVE_GAMES for region in ACTIVE_REGIONS}
    failures: list[dict[str, Any]] = []
    missing_sources = sorted(set(CANONICAL_SOURCE_KEYS) - seen_sources)
    if cells != required_cells:
        failures.append(
            {
                "regional_cells": {
                    "missing": sorted(required_cells - cells),
                    "unexpected": sorted(cells - required_cells),
                }
            }
        )
    if missing_sources:
        failures.append({"missing_sources": missing_sources})
    if deprecated:
        failures.append({"deprecated_source_rows": deprecated})
    if riftbound:
        failures.append({"riftbound_rows": riftbound})
    if invalid:
        failures.append({"invalid_source_rows": invalid[:25], "count": len(invalid)})
    if nonofficial:
        failures.append({"nonofficial_rows": nonofficial})
    if blank_titles:
        failures.append({"blank_titles": blank_titles})
    if duplicate_rows:
        failures.append(
            {
                "duplicate_titles": [dict(row) for row in duplicate_rows[:25]],
                "count": len(duplicate_rows),
            }
        )

    return {
        "gate": "PASS" if not failures else "FAIL",
        "verified_at": datetime.now(timezone.utc).isoformat(),
        "canonical_source_count": len(CANONICAL_SOURCE_KEYS),
        "seen_source_count": len(seen_sources),
        "regional_cell_count": len(cells),
        "canonical_rows": len(rows),
        "deprecated_source_rows": deprecated,
        "riftbound_rows": riftbound,
        "invalid_source_rows": len(invalid),
        "nonofficial_rows": nonofficial,
        "blank_titles": blank_titles,
        "duplicate_titles": len(duplicate_rows),
        "failures": failures,
    }


def _reportable_collection(collection: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in collection.items()
        if key != "records"
    } | {
        "records": [
            {
                **record,
                "published_date": _iso(_date_value(record.get("published_date"))),
                "release_date": _iso(_date_value(record.get("release_date"))),
            }
            for record in collection["records"]
        ]
    }


def _write_report(path: Path | None, report: dict[str, Any]) -> None:
    rendered = json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False, default=str)
    print(rendered)
    if path:
        path.write_text(rendered + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Strict idempotent daily official regional TCG sync.")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    mode.add_argument("--verify-db", action="store_true")
    mode.add_argument("--cleanup-deprecated", action="store_true")
    parser.add_argument("--certify-two-pass", action="store_true")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()

    if args.certify_two_pass and not args.apply:
        parser.error("--certify-two-pass requires --apply")

    if args.cleanup_deprecated:
        report = {"mode": "cleanup-deprecated", **cleanup_deprecated_sources()}
        _write_report(args.report, report)
        return 0

    if args.verify_db:
        report = {"mode": "verify-db", **verify_database()}
        _write_report(args.report, report)
        if report["gate"] != "PASS":
            raise SystemExit(1)
        return 0

    collection = collect_official_content(strict=True)
    if args.dry_run:
        report = {"mode": "dry-run", **_reportable_collection(collection)}
        _write_report(args.report, report)
        return 0

    first = apply_records(collection["records"])
    report: dict[str, Any] = {
        "mode": "apply",
        "collection": _reportable_collection(collection),
        "first_pass": first,
    }
    if args.certify_two_pass:
        second = apply_records(collection["records"])
        report["second_pass"] = second
        if second["inserted"] != 0 or second["updated"] != 0 or second["material_writes"] != 0:
            report["gate"] = "FAIL"
            _write_report(args.report, report)
            raise SystemExit(1)
        report["gate"] = "PASS"
    _write_report(args.report, report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
