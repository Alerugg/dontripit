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

CATALOG_TARGETS = {
    "games": ("slug", "name"),
    "sets": ("code", "name"),
    "cards": ("card_key", "name"),
    "prints": ("print_key", "collector_number", "variant"),
    "products": ("name", "product_type"),
    "product_variants": ("sku", "language", "region"),
    "catalog_releases": ("external_id", "name", "source"),
    "external_catalog_products": ("source", "external_id", "name", "category", "website_path"),
    "regional_tcg_content": ("title", "source_key", "source_name", "item_url"),
}

# High-confidence fiction markers only. Deliberately exclude broad words such as
# "test" and "example" from catalogue data because they can occur legitimately
# in names or external identifiers.
FICTION_MARKER = re.compile(
    r"(^|[^a-z0-9])(demo|dummy|fake|fixture|lorem(?:\s+ipsum)?|placeholder|sample)([^a-z0-9]|$)",
    re.IGNORECASE,
)
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


def _scan_table(cur, table: str, columns: list[str], available: set[str]) -> dict:
    select_columns = (["id"] if "id" in available else []) + columns
    query = sql.SQL("SELECT {} FROM {}").format(
        sql.SQL(",").join(sql.Identifier(column) for column in select_columns),
        sql.Identifier(table),
    )
    cur.execute(query)
    rows = [dict(row) for row in cur.fetchall()]
    hits = []
    for row in rows:
        matches = []
        for column in columns:
            value = row.get(column)
            if value is not None and FICTION_MARKER.search(str(value)):
                matches.append({"column": column, "value": str(value)[:500]})
        if matches:
            identity = {key: value for key, value in row.items() if key == "id" or key in columns}
            hits.append({"row": identity, "matches": matches})
    return {"rows": len(rows), "columns": columns, "suspicious": len(hits), "hits": hits[:100]}


def main() -> int:
    parser = argparse.ArgumentParser(description="Fail-closed, read-only audit for fictional production residue.")
    parser.add_argument("--report", help="Optional JSON report path")
    args = parser.parse_args()

    report = {
        "gate": "FAIL",
        "read_only": True,
        "scanned": {},
        "suspicious_rows": {},
        "test_accounts": [],
        "failures": [],
    }

    conn = psycopg2.connect(_database_url())
    try:
        conn.set_session(readonly=True, autocommit=False)
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            available = _available_columns(cur)

            for table, wanted in CATALOG_TARGETS.items():
                existing = available.get(table, set())
                columns = [column for column in wanted if column in existing]
                if not columns:
                    continue
                result = _scan_table(cur, table, columns, existing)
                report["scanned"][table] = {
                    "rows": result["rows"],
                    "columns": result["columns"],
                    "suspicious": result["suspicious"],
                }
                if result["hits"]:
                    report["suspicious_rows"][table] = result["hits"]

            # User accounts are not public catalogue content, but obvious CI/demo
            # identities are still reported and fail the gate so they cannot become
            # permanent production residue. Query only when the schema exposes users.email.
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

    suspicious_total = sum(item["suspicious"] for item in report["scanned"].values())
    report["suspicious_total"] = suspicious_total
    report["test_account_total"] = len(report["test_accounts"])
    if suspicious_total:
        report["failures"].append({"fictional_catalog_rows": suspicious_total})
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
