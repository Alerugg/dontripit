from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import text

from app import db
from app.search_v2.normalization import normalize_search_text
from app.search_v2.onepiece_don_query import onepiece_don_market_page


OUTPUT = Path(os.getenv("ONEPIECE_DON_SEARCH_CERT_OUTPUT", "artifacts/onepiece-don-search-source-v1.json"))


def _assert_source_owned(items: list[dict]) -> None:
    for item in items:
        if item.get("type") != "don_market" or item.get("identity_scope") != "source_owned":
            raise AssertionError(f"unexpected DON identity shape: {item}")
        if item.get("card_id") is not None or item.get("print_id") is not None:
            raise AssertionError("source-owned DON row must not impersonate canonical Card/Print")
        if item.get("collector_number") is not None or item.get("language") is not None or item.get("rarity") is not None:
            raise AssertionError("unresolved DON row must not invent collector/language/rarity")


def main() -> int:
    if not (os.getenv("DATABASE_URL_UNPOOLED") or os.getenv("DATABASE_URL")):
        raise RuntimeError("DATABASE_URL_UNPOOLED or DATABASE_URL is required")

    report = {
        "certified_at": datetime.now(timezone.utc).isoformat(),
        "status": "running",
        "production_writes": 0,
        "transaction_read_only": False,
        "probes": {},
    }

    with db.SessionLocal() as session:
        session.execute(text("SET TRANSACTION READ ONLY"))
        read_only = session.execute(text("SHOW transaction_read_only")).scalar_one()
        report["transaction_read_only"] = read_only == "on"
        if not report["transaction_read_only"]:
            raise AssertionError("DON source search certifier is not read-only")

        all_first = onepiece_don_market_page(session, query="DON", limit=100, offset=0)
        all_second = onepiece_don_market_page(session, query="DON", limit=100, offset=100)
        all_items = [*all_first["items"], *all_second["items"]]
        _assert_source_owned(all_items)
        if all_first["total"] < 150:
            raise AssertionError(f"expected >=150 current DON metacards, got {all_first['total']}")
        if len(all_items) != all_first["total"]:
            raise AssertionError({"paged": len(all_items), "total": all_first["total"]})

        luffy = onepiece_don_market_page(session, query="Luffy", limit=100, offset=0)
        _assert_source_owned(luffy["items"])
        if luffy["total"] < 1:
            raise AssertionError("Luffy DON probe returned no rows")
        for item in luffy["items"]:
            if "luffy" not in normalize_search_text(item.get("subject") or ""):
                raise AssertionError(f"non-Luffy DON leaked into Luffy probe: {item}")

        zoro = onepiece_don_market_page(session, query="Zoro", limit=100, offset=0)
        _assert_source_owned(zoro["items"])
        if zoro["total"] < 1:
            raise AssertionError("Zoro DON probe returned no rows")
        for item in zoro["items"]:
            if "zoro" not in normalize_search_text(item.get("subject") or ""):
                raise AssertionError(f"non-Zoro DON leaked into Zoro probe: {item}")

        false_positive = onepiece_don_market_page(session, query="Donquixote", limit=100, offset=0)
        if false_positive["total"] != 0:
            raise AssertionError(f"Donquixote false positives leaked into DON path: {false_positive['total']}")

        price_rows = sum(1 for item in all_items if item.get("cardmarket_price") is not None)
        image_rows = sum(1 for item in all_items if item.get("primary_image_url"))
        report["probes"] = {
            "all_don": all_first["total"],
            "luffy": luffy["total"],
            "zoro": zoro["total"],
            "donquixote_false_positive": false_positive["total"],
            "with_cardmarket_price": price_rows,
            "with_cardmarket_image_url": image_rows,
        }
        if price_rows < 1:
            raise AssertionError("DON path exposed no current Cardmarket prices")
        if image_rows < 1:
            raise AssertionError("DON path exposed no Cardmarket image URLs")
        session.rollback()

    report["status"] = "pass"
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
