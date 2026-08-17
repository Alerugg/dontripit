from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path

from sqlalchemy import create_engine, text

from app.jobs.regional_content import (
    CANONICAL_SOURCE_KEYS,
    DEPRECATED_SOURCE_KEYS,
    SOURCES,
    TPCI_SCHEDULE_URL,
    TPCI_SOURCE_KEY,
)


ACTIVE_GAMES = ("pokemon", "onepiece", "mtg", "yugioh")
ACTIVE_REGIONS = ("jp", "eu", "us")


def _expected_sources() -> dict[str, dict[str, object]]:
    expected = {
        source.key: {
            "game": source.game,
            "regions": set(source.regions),
            "source_url": source.url,
        }
        for source in SOURCES
    }
    expected[TPCI_SOURCE_KEY] = {
        "game": "pokemon",
        "regions": {"eu"},
        "source_url": TPCI_SCHEDULE_URL,
    }
    return expected


def verify(*, window_hours: int) -> dict[str, object]:
    database_url = os.getenv("DATABASE_URL_UNPOOLED") or os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL_UNPOOLED or DATABASE_URL is required")

    expected_sources = _expected_sources()
    if set(expected_sources) != set(CANONICAL_SOURCE_KEYS):
        raise AssertionError("Canonical source registry and expected source map diverged")

    cutoff = datetime.now(timezone.utc) - timedelta(hours=window_hours)
    engine = create_engine(database_url)
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT g.slug, c.region, c.kind, c.source_key, c.source_url,
                       c.item_url, c.title, c.raw_json, c.last_seen_at
                FROM regional_tcg_content c
                JOIN games g ON g.id = c.game_id
                WHERE c.source_key = ANY(:keys)
                  AND c.last_seen_at >= :cutoff
                ORDER BY g.slug, c.region, c.source_key, c.item_url
                """
            ),
            {"keys": list(CANONICAL_SOURCE_KEYS), "cutoff": cutoff},
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
                    SELECT count(*)
                    FROM regional_tcg_content c
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
                       count(*) AS copies,
                       array_agg(c.item_url ORDER BY c.item_url) AS item_urls
                FROM regional_tcg_content c
                JOIN games g ON g.id = c.game_id
                WHERE c.source_key = ANY(:keys)
                  AND c.last_seen_at >= :cutoff
                GROUP BY g.slug, c.region,
                         lower(regexp_replace(trim(c.title), '\\s+', ' ', 'g'))
                HAVING count(*) > 1
                ORDER BY g.slug, c.region, normalized_title
                """
            ),
            {"keys": list(CANONICAL_SOURCE_KEYS), "cutoff": cutoff},
        ).mappings().all()

    failures: list[dict[str, object]] = []
    cells: dict[tuple[str, str], dict[str, int]] = {}
    seen_sources: set[str] = set()
    invalid_source_rows: list[dict[str, object]] = []
    nonofficial_rows = 0
    blank_titles = 0

    for row in rows:
        game = str(row["slug"])
        region = str(row["region"])
        kind = str(row["kind"])
        source_key = str(row["source_key"])
        source_url = str(row["source_url"] or "")
        item_url = str(row["item_url"] or "")
        title = str(row["title"] or "").strip()
        raw_json = row["raw_json"] or {}
        if isinstance(raw_json, str):
            try:
                raw_json = json.loads(raw_json)
            except json.JSONDecodeError:
                raw_json = {}

        seen_sources.add(source_key)
        metric = cells.setdefault((game, region), {"items": 0, "news": 0, "releases": 0})
        metric["items"] += 1
        if kind == "news":
            metric["news"] += 1
        if kind in {"product", "release"}:
            metric["releases"] += 1

        expected = expected_sources.get(source_key)
        if (
            expected is None
            or game != expected["game"]
            or region not in expected["regions"]
            or source_url != expected["source_url"]
            or not item_url.startswith(("https://", "http://"))
        ):
            invalid_source_rows.append(
                {
                    "game": game,
                    "region": region,
                    "source_key": source_key,
                    "source_url": source_url,
                    "item_url": item_url,
                }
            )
        if raw_json.get("official") is not True:
            nonofficial_rows += 1
        if not title:
            blank_titles += 1

    required_cells = {(game, region) for game in ACTIVE_GAMES for region in ACTIVE_REGIONS}
    actual_cells = set(cells)
    missing_sources = sorted(set(CANONICAL_SOURCE_KEYS) - seen_sources)

    if actual_cells != required_cells:
        failures.append(
            {
                "regional_cells": {
                    "missing": sorted(required_cells - actual_cells),
                    "unexpected": sorted(actual_cells - required_cells),
                }
            }
        )
    if missing_sources:
        failures.append({"missing_sources": missing_sources})
    if deprecated:
        failures.append({"deprecated_source_rows": deprecated})
    if riftbound:
        failures.append({"riftbound_rows": riftbound})
    if nonofficial_rows:
        failures.append({"nonofficial_rows": nonofficial_rows})
    if invalid_source_rows:
        failures.append({"invalid_source_rows": invalid_source_rows[:25], "count": len(invalid_source_rows)})
    if blank_titles:
        failures.append({"blank_titles": blank_titles})
    if duplicate_rows:
        failures.append(
            {
                "duplicate_titles": [
                    {
                        "game": str(row["slug"]),
                        "region": str(row["region"]),
                        "title": str(row["normalized_title"]),
                        "copies": int(row["copies"]),
                        "item_urls": list(row["item_urls"]),
                    }
                    for row in duplicate_rows[:25]
                ],
                "count": len(duplicate_rows),
            }
        )

    cell_report = [
        {
            "game": game,
            "region": region,
            **cells[(game, region)],
        }
        for game, region in sorted(cells)
    ]
    report: dict[str, object] = {
        "gate": "PASS" if not failures else "FAIL",
        "verified_at": datetime.now(timezone.utc).isoformat(),
        "window_hours": window_hours,
        "canonical_source_count": len(CANONICAL_SOURCE_KEYS),
        "seen_source_count": len(seen_sources),
        "regional_cell_count": len(actual_cells),
        "cells": cell_report,
        "deprecated_source_rows": deprecated,
        "riftbound_rows": riftbound,
        "nonofficial_rows": nonofficial_rows,
        "invalid_source_rows": len(invalid_source_rows),
        "blank_titles": blank_titles,
        "duplicate_titles": len(duplicate_rows),
        "failures": failures,
    }
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify the canonical regional TCG ingest in production.")
    parser.add_argument("--window-hours", type=int, default=2)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()

    report = verify(window_hours=args.window_hours)
    rendered = json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False)
    print("REGIONAL_INGEST " + json.dumps(report, sort_keys=True, ensure_ascii=False))
    if args.report:
        args.report.write_text(rendered + "\n", encoding="utf-8")
    if report["gate"] != "PASS":
        raise SystemExit(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
