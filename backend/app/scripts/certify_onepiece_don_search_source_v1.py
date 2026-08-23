from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import text

from app import db
from app.search_v2.normalization import normalize_search_text
from app.search_v2.onepiece_don_query import onepiece_don_market_page


OUTPUT = Path(os.getenv("ONEPIECE_DON_SEARCH_CERT_OUTPUT", "artifacts/onepiece-don-search-source-v1.json"))
MIN_PRICE_COVERAGE_RATIO = 0.95
MAX_QUERY_MS = 1500.0


def _assert_source_owned(items: list[dict]) -> None:
    for item in items:
        if item.get("type") != "don_market" or item.get("identity_scope") != "source_owned":
            raise AssertionError(f"unexpected DON identity shape: {item}")
        if item.get("card_id") is not None or item.get("print_id") is not None:
            raise AssertionError("source-owned DON row must not impersonate canonical Card/Print")
        if item.get("collector_number") is not None or item.get("language") is not None or item.get("rarity") is not None:
            raise AssertionError("unresolved DON row must not invent collector/language/rarity")


def _timed_page(session, *, query: str, limit: int, offset: int = 0) -> tuple[dict, float]:
    started = time.perf_counter()
    page = onepiece_don_market_page(session, query=query, limit=limit, offset=offset)
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    if elapsed_ms > MAX_QUERY_MS:
        raise AssertionError({"query": query, "latency_ms": round(elapsed_ms, 3), "budget_ms": MAX_QUERY_MS})
    return page, elapsed_ms


def main() -> int:
    if not (os.getenv("DATABASE_URL_UNPOOLED") or os.getenv("DATABASE_URL")):
        raise RuntimeError("DATABASE_URL_UNPOOLED or DATABASE_URL is required")

    report = {
        "certified_at": datetime.now(timezone.utc).isoformat(),
        "status": "running",
        "production_writes": 0,
        "transaction_read_only": False,
        "probes": {},
        "latency_ms": {},
        "unpriced_metacards": [],
    }

    with db.SessionLocal() as session:
        session.execute(text("SET TRANSACTION READ ONLY"))
        read_only = session.execute(text("SHOW transaction_read_only")).scalar_one()
        report["transaction_read_only"] = read_only == "on"
        if not report["transaction_read_only"]:
            raise AssertionError("DON source search certifier is not read-only")

        all_first, all_first_ms = _timed_page(session, query="DON", limit=100, offset=0)
        all_second, all_second_ms = _timed_page(session, query="DON", limit=100, offset=100)
        all_items = [*all_first["items"], *all_second["items"]]
        _assert_source_owned(all_items)
        if all_first["total"] < 150:
            raise AssertionError(f"expected >=150 current DON metacards, got {all_first['total']}")
        if len(all_items) != all_first["total"]:
            raise AssertionError({"paged": len(all_items), "total": all_first["total"]})

        luffy, luffy_ms = _timed_page(session, query="Luffy", limit=100)
        _assert_source_owned(luffy["items"])
        if luffy["total"] < 1:
            raise AssertionError("Luffy DON probe returned no rows")
        for item in luffy["items"]:
            if "luffy" not in normalize_search_text(item.get("subject") or ""):
                raise AssertionError(f"non-Luffy DON leaked into Luffy probe: {item}")

        zoro, zoro_ms = _timed_page(session, query="Zoro", limit=100)
        _assert_source_owned(zoro["items"])
        if zoro["total"] < 1:
            raise AssertionError("Zoro DON probe returned no rows")
        for item in zoro["items"]:
            if "zoro" not in normalize_search_text(item.get("subject") or ""):
                raise AssertionError(f"non-Zoro DON leaked into Zoro probe: {item}")

        false_positive, false_positive_ms = _timed_page(session, query="Donquixote", limit=100)
        if false_positive["total"] != 0:
            raise AssertionError(f"Donquixote false positives leaked into DON path: {false_positive['total']}")

        price_rows = sum(1 for item in all_items if item.get("cardmarket_price") is not None)
        derived_image_rows = sum(1 for item in all_items if item.get("primary_image_url"))
        price_coverage = price_rows / len(all_items) if all_items else 0.0
        unpriced = [
            {
                "metacard_external_id": item.get("metacard_external_id"),
                "name": item.get("name"),
                "subject": item.get("subject"),
                "representative_external_product_id": item.get("representative_external_product_id"),
                "product_count": item.get("product_count"),
            }
            for item in all_items
            if item.get("cardmarket_price") is None
        ]
        report["unpriced_metacards"] = unpriced
        report["probes"] = {
            "all_don": all_first["total"],
            "luffy": luffy["total"],
            "zoro": zoro["total"],
            "donquixote_false_positive": false_positive["total"],
            "with_cardmarket_price": price_rows,
            "price_coverage_ratio": round(price_coverage, 6),
            "unpriced_metacards": len(unpriced),
            "with_derived_cardmarket_image_url": derived_image_rows,
        }
        report["latency_ms"] = {
            "all_don_page_1": round(all_first_ms, 3),
            "all_don_page_2": round(all_second_ms, 3),
            "luffy": round(luffy_ms, 3),
            "zoro": round(zoro_ms, 3),
            "donquixote_negative": round(false_positive_ms, 3),
            "budget_max": MAX_QUERY_MS,
        }
        if price_coverage < MIN_PRICE_COVERAGE_RATIO:
            raise AssertionError({
                "price_coverage_ratio": price_coverage,
                "minimum": MIN_PRICE_COVERAGE_RATIO,
                "unpriced_metacards": unpriced,
            })
        if derived_image_rows != len(all_items):
            raise AssertionError({"derived_image_urls": derived_image_rows, "items": len(all_items)})
        session.rollback()

    report["status"] = "pass"
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
