#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path

import psycopg2
from psycopg2 import sql
from psycopg2.extras import RealDictCursor

# Point 9 is about synthetic/demo residue created by the application or CI. Words
# such as "Demo", "Sample", "Fake" and "Dummy" are NOT enough: they occur in
# real, source-backed TCG names (for example official demo decks and cards).
# The fail-closed checks below therefore target identity/provenance fields only.
IDENTITY_TARGETS = {
    "games": ("slug",),
    "sets": ("tcgdex_id", "yugioh_id", "riftbound_id"),
    "cards": ("card_key", "oracle_id", "tcgdex_id", "yugoprodeck_id", "riftbound_id"),
    "prints": ("print_key", "scryfall_id", "tcgdex_id", "yugioh_id", "riftbound_id"),
    "product_variants": ("sku",),
    "catalog_releases": ("external_id", "source"),
    "print_identifiers": ("source", "external_id"),
    "product_identifiers": ("source", "external_id"),
    "external_catalog_products": ("source", "external_id"),
    "regional_tcg_content": ("source_key", "source_url", "item_url"),
}

# Exact provenance values that are unambiguously synthetic rather than a real
# upstream connector. Keep this deliberately narrow.
SYNTHETIC_SOURCE = re.compile(
    r"^(demo|dummy|fake|fixture|lorem|placeholder|sample|test|e2e|mock)$",
    re.IGNORECASE,
)

# Synthetic identity namespaces used by fixtures/local demos. Requiring an
# explicit separator avoids false positives such as official set code DEM1.
SYNTHETIC_NAMESPACE = re.compile(
    r"(^|[:/|])(?:demo|dummy|fake|fixture|lorem|placeholder|sample|test|e2e|mock)(?=[:/|_-]|$)",
    re.IGNORECASE,
)

# Human-readable names containing these words are observations only. They are
# retained in the report to prove why name-based deletion would be unsafe.
NAME_MARKER = re.compile(
    r"(^|[^a-z0-9])(demo|dummy|fake|fixture|lorem(?:\s+ipsum)?|placeholder|sample)([^a-z0-9]|$)",
    re.IGNORECASE,
)

NAME_TARGETS = {
    "games": ("name",),
    "sets": ("name",),
    "cards": ("name",),
    "products": ("name",),
    "catalog_releases": ("name",),
    "external_catalog_products": ("name",),
    "regional_tcg_content": ("title",),
}

TEST_ACCOUNT = re.compile(
    r"(^|[+._-])(demo|dummy|fixture|placeholder|e2e|test)([+._-]|@)|@(example\.com|resend\.dev)$",
    re.IGNORECASE,
)


def _database_url() -> str:
    value = os.getenv("DATABASE_URL_UNPOOLED") or os.getenv("DATABASE_URL")
    if not value:
        raise SystemExit("DATABASE_URL_UNPOOLED or DATABASE_URL is required")
    return value


def _available_columns(cur) -> dict[str, set[str]]:
    cur.execute(
        "SELECT table_name,column_name FROM information_schema.columns "
        "WHERE table_schema='public'"
    )
    available: dict[str, set[str]] = {}
    for row in cur.fetchall():
        available.setdefault(str(row["table_name"]), set()).add(str(row["column_name"]))
    return available


def _select_rows(cur, table: str, columns: list[str], available: set[str]) -> list[dict]:
    select_columns = (["id"] if "id" in available else []) + columns
    query = sql.SQL("SELECT {} FROM {}").format(
        sql.SQL(",").join(sql.Identifier(column) for column in select_columns),
        sql.Identifier(table),
    )
    cur.execute(query)
    return [dict(row) for row in cur.fetchall()]


def _synthetic_matches(table: str, row: dict, columns: list[str]) -> list[dict]:
    matches = []
    for column in columns:
        value = row.get(column)
        if value is None:
            continue
        rendered = str(value).strip()
        if not rendered:
            continue
        if column in {"source", "source_key"}:
            suspicious = bool(SYNTHETIC_SOURCE.fullmatch(rendered))
        else:
            suspicious = bool(SYNTHETIC_NAMESPACE.search(rendered))
        if suspicious:
            matches.append({"column": column, "value": rendered[:500]})
    return matches


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fail-closed, read-only audit for synthetic production residue."
    )
    parser.add_argument("--report", help="Optional JSON report path")
    args = parser.parse_args()

    report = {
        "gate": "FAIL",
        "read_only": True,
        "identity_scan": {},
        "synthetic_rows": {},
        "name_marker_observations": {},
        "test_accounts": [],
        "failures": [],
    }

    conn = psycopg2.connect(_database_url())
    try:
        # PostgreSQL itself enforces that this audit cannot write production.
        conn.set_session(readonly=True, autocommit=False)
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            available = _available_columns(cur)

            for table, wanted in IDENTITY_TARGETS.items():
                existing = available.get(table, set())
                columns = [column for column in wanted if column in existing]
                if not columns:
                    continue
                rows = _select_rows(cur, table, columns, existing)
                hits = []
                for row in rows:
                    matches = _synthetic_matches(table, row, columns)
                    if matches:
                        hits.append({"row": row, "matches": matches})
                report["identity_scan"][table] = {
                    "rows": len(rows),
                    "columns": columns,
                    "synthetic": len(hits),
                }
                if hits:
                    report["synthetic_rows"][table] = hits[:100]

            # Informational only: prove that catalogue names containing words like
            # Demo/Sample/Fake/Dummy exist and must not be mistaken for fixtures.
            for table, wanted in NAME_TARGETS.items():
                existing = available.get(table, set())
                columns = [column for column in wanted if column in existing]
                if not columns:
                    continue
                rows = _select_rows(cur, table, columns, existing)
                observations = []
                count = 0
                for row in rows:
                    matches = []
                    for column in columns:
                        value = row.get(column)
                        if value is not None and NAME_MARKER.search(str(value)):
                            matches.append({"column": column, "value": str(value)[:500]})
                    if matches:
                        count += 1
                        if len(observations) < 20:
                            observations.append({"row": row, "matches": matches})
                report["name_marker_observations"][table] = {
                    "rows": len(rows),
                    "observations": count,
                    "examples": observations,
                    "failing": False,
                }

            # Obvious CI/demo accounts are not public catalogue content, but should
            # not become permanent production residue either.
            if "users" in available and "email" in available["users"]:
                select = ["id", "email"]
                if "created_at" in available["users"]:
                    select.append("created_at")
                cur.execute(
                    sql.SQL("SELECT {} FROM users ORDER BY {} LIMIT 5000").format(
                        sql.SQL(",").join(sql.Identifier(column) for column in select),
                        sql.Identifier("created_at" if "created_at" in select else "id"),
                    )
                )
                for row in cur.fetchall():
                    email = str(row.get("email") or "").strip().casefold()
                    if email and TEST_ACCOUNT.search(email):
                        report["test_accounts"].append(dict(row))

        conn.rollback()
    finally:
        conn.close()

    synthetic_total = sum(item["synthetic"] for item in report["identity_scan"].values())
    report["synthetic_identity_total"] = synthetic_total
    report["test_account_total"] = len(report["test_accounts"])
    if synthetic_total:
        report["failures"].append({"synthetic_identity_rows": synthetic_total})
    if report["test_account_total"]:
        report["failures"].append({"obvious_test_accounts": report["test_account_total"]})
    report["gate"] = "PASS" if not report["failures"] else "FAIL"

    rendered = json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False, default=str)
    print("FICTIONAL_PRODUCTION_DATA " + rendered)
    if args.report:
        Path(args.report).write_text(rendered + "\n", encoding="utf-8")
    return 0 if report["gate"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
