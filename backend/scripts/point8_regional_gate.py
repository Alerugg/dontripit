from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path

from sqlalchemy import create_engine, text

from app import db
from app.jobs.regional_content import (
    CANONICAL_SOURCE_KEYS,
    DEPRECATED_SOURCE_KEYS,
    SOURCES,
    TPCI_SCHEDULE_URL,
    TPCI_SOURCE_KEY,
    ingest_official_regional_content,
)


def _in_clause(values: list[str], prefix: str) -> tuple[str, dict[str, str]]:
    params = {f"{prefix}{i}": value for i, value in enumerate(values)}
    return ",".join(f":{key}" for key in params), params


def _run_ingest() -> dict:
    with db.SessionLocal() as session:
        report = ingest_official_regional_content(session, strict=True)
        session.commit()
        return report


def main() -> None:
    database_url = os.getenv("DATABASE_URL_UNPOOLED") or os.environ["DATABASE_URL"]
    started = datetime.now(timezone.utc) - timedelta(seconds=2)
    db.init_engine()
    engine = create_engine(database_url)

    keys = list(CANONICAL_SOURCE_KEYS)
    key_sql, key_params = _in_clause(keys, "k")
    deprecated = list(DEPRECATED_SOURCE_KEYS)
    deprecated_sql, deprecated_params = _in_clause(deprecated, "d")

    first = _run_ingest()
    with engine.connect() as conn:
        after_first = int(
            conn.execute(
                text(f"SELECT count(*) FROM regional_tcg_content WHERE source_key IN ({key_sql})"),
                key_params,
            ).scalar_one()
        )

    second = _run_ingest()
    with engine.connect() as conn:
        after_second = int(
            conn.execute(
                text(f"SELECT count(*) FROM regional_tcg_content WHERE source_key IN ({key_sql})"),
                key_params,
            ).scalar_one()
        )
        cells = conn.execute(
            text(
                f"""
                SELECT g.slug,c.region,count(*) items,
                       count(*) FILTER (WHERE c.kind='news') news,
                       count(*) FILTER (WHERE c.kind IN ('product','release')) releases
                FROM regional_tcg_content c JOIN games g ON g.id=c.game_id
                WHERE g.slug IN ('pokemon','onepiece','mtg','yugioh')
                  AND c.source_key IN ({key_sql})
                  AND c.last_seen_at >= :started
                GROUP BY g.slug,c.region ORDER BY g.slug,c.region
                """
            ),
            {**key_params, "started": started},
        ).all()
        duplicates = int(
            conn.execute(
                text(
                    f"""
                    SELECT count(*) FROM (
                      SELECT source_key,region,item_url FROM regional_tcg_content
                      WHERE source_key IN ({key_sql})
                      GROUP BY source_key,region,item_url HAVING count(*) > 1
                    ) d
                    """
                ),
                key_params,
            ).scalar_one()
        )
        nonofficial = int(
            conn.execute(
                text(
                    f"""
                    SELECT count(*) FROM regional_tcg_content
                    WHERE source_key IN ({key_sql})
                      AND coalesce(raw_json->>'official','false') <> 'true'
                    """
                ),
                key_params,
            ).scalar_one()
        )
        deprecated_rows = int(
            conn.execute(
                text(f"SELECT count(*) FROM regional_tcg_content WHERE source_key IN ({deprecated_sql})"),
                deprecated_params,
            ).scalar_one()
        )
        riftbound = int(
            conn.execute(
                text(
                    """
                    SELECT count(*) FROM regional_tcg_content c
                    JOIN games g ON g.id=c.game_id WHERE g.slug='riftbound'
                    """
                )
            ).scalar_one()
        )
        source_rows = conn.execute(
            text(
                f"""
                SELECT DISTINCT source_key,source_url FROM regional_tcg_content
                WHERE source_key IN ({key_sql}) AND last_seen_at >= :started
                """
            ),
            {**key_params, "started": started},
        ).all()
        kind_counts = conn.execute(
            text(
                f"""
                SELECT kind,count(*) FROM regional_tcg_content
                WHERE source_key IN ({key_sql}) GROUP BY kind
                """
            ),
            key_params,
        ).all()

    expected_urls = {source.key: source.url for source in SOURCES}
    expected_urls[TPCI_SOURCE_KEY] = TPCI_SCHEDULE_URL
    observed_urls = {str(key): str(url) for key, url in source_rows}
    proof_cells = [
        {"game": str(g), "region": str(r), "items": int(i), "news": int(n), "releases": int(rel)}
        for g, r, i, n, rel in cells
    ]
    required = {(g, r) for g in ("pokemon", "onepiece", "mtg", "yugioh") for r in ("jp", "eu", "us")}
    actual = {(row["game"], row["region"]) for row in proof_cells}
    kinds = {str(kind): int(count) for kind, count in kind_counts}

    failures: list[dict] = []
    if first["failed_sources"] or second["failed_sources"]:
        failures.append({"source_failures": [first["failed_sources"], second["failed_sources"]]})
    if first["sources"] != len(keys) or second["sources"] != len(keys):
        failures.append({"source_count": [first["sources"], second["sources"], len(keys)]})
    if after_first != after_second:
        failures.append({"idempotency_row_growth": [after_first, after_second]})
    if actual != required:
        failures.append({"regional_cells": {"missing": sorted(required - actual), "unexpected": sorted(actual - required)}})
    if duplicates:
        failures.append({"duplicate_identities": duplicates})
    if nonofficial:
        failures.append({"nonofficial_rows": nonofficial})
    if deprecated_rows:
        failures.append({"deprecated_rows": deprecated_rows})
    if riftbound:
        failures.append({"riftbound_rows": riftbound})
    if observed_urls != expected_urls:
        failures.append({"source_urls": {"expected": expected_urls, "observed": observed_urls}})
    if kinds.get("news", 0) < 1 or kinds.get("release", 0) + kinds.get("product", 0) < 1:
        failures.append({"content_kinds": kinds})

    report = {
        "point": 8,
        "canonical_sources": keys,
        "first_ingest_upserts": int(first["upserts"]),
        "second_ingest_upserts": int(second["upserts"]),
        "canonical_rows_after_first": after_first,
        "canonical_rows_after_second": after_second,
        "idempotent": after_first == after_second,
        "cells": proof_cells,
        "content_kinds": kinds,
        "duplicate_identities": duplicates,
        "nonofficial_rows": nonofficial,
        "deprecated_rows": deprecated_rows,
        "riftbound_rows": riftbound,
        "source_urls": observed_urls,
        "daily_cron": "main 05:10 UTC -> checkout catalog-v2",
        "gate": "PASS" if not failures else "FAIL",
        "failures": failures,
    }
    Path("/tmp/point8-final.json").write_text(
        json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print("POINT8_FINAL " + json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False))
    if failures:
        raise SystemExit(json.dumps(failures, ensure_ascii=False))


if __name__ == "__main__":
    main()
