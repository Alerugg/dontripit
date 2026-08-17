from __future__ import annotations

import json
import os
import time
from pathlib import Path

import requests
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.search_v2.advanced import advanced_onepiece_search
from app.search_v2.mtg_advanced import advanced_mtg_search
from app.search_v2.pokemon_advanced import advanced_pokemon_search
from app.search_v2.yugioh_advanced import advanced_yugioh_search


OUT = Path("/tmp/pagination-point2-final-v2.json")


def _disjoint(a: list[dict], b: list[dict], key: str = "id") -> bool:
    return not ({item[key] for item in a} & {item[key] for item in b})


def main() -> int:
    engine = create_engine(
        os.getenv("DATABASE_URL_UNPOOLED") or os.environ["DATABASE_URL"],
        pool_pre_ping=True,
    )
    report: dict = {
        "pass": False,
        "hard_failures": [],
        "db": {},
        "http": {},
        "point1": {},
    }
    failures: list[str] = report["hard_failures"]

    cases = {
        "onepiece": (advanced_onepiece_search, "Luffy"),
        "pokemon": (advanced_pokemon_search, "Pikachu"),
        "yugioh": (advanced_yugioh_search, "Dark Magician"),
        "mtg": (advanced_mtg_search, "Sol Ring"),
    }
    for game, (fn, query) in cases.items():
        started = time.time()
        try:
            with Session(engine) as session:
                session.execute(text("SET LOCAL statement_timeout='12000ms'"))
                first = fn(
                    session,
                    filters={},
                    query=query,
                    sort="price_desc",
                    has_price=True,
                    limit=5,
                    offset=0,
                )
                second = fn(
                    session,
                    filters={},
                    query=query,
                    sort="price_desc",
                    has_price=True,
                    limit=5,
                    offset=5,
                )
            first_prices = [float(item["cardmarket_price"]) for item in first["items"]]
            second_prices = [float(item["cardmarket_price"]) for item in second["items"]]
            first_ids = [int(item["print_id"]) for item in first["items"]]
            second_ids = [int(item["print_id"]) for item in second["items"]]
            refs = [str(item.get("cardmarket_id_product") or "") for item in first["items"] + second["items"]]
            local: list[str] = []
            if not first["total"]:
                local.append("no_results")
            if first_prices != sorted(first_prices, reverse=True) or second_prices != sorted(second_prices, reverse=True):
                local.append("page_order")
            if first_prices and second_prices and first_prices[-1] < second_prices[0]:
                local.append("cross_page_order")
            if set(first_ids) & set(second_ids):
                local.append("duplicate_page_ids")
            if any(not value for value in refs):
                local.append("missing_exact_market_reference")
            if local:
                failures.append(f"{game}:{local}")
            report["db"][game] = {
                "total_priced": first["total"],
                "page1_ids": first_ids,
                "page2_ids": second_ids,
                "page1_prices": first_prices,
                "page2_prices": second_prices,
                "seconds": round(time.time() - started, 3),
                "failures": local,
            }
        except Exception as exc:  # pragma: no cover - audit path
            failures.append(f"{game}:{type(exc).__name__}:{exc}")
            report["db"][game] = {"error": str(exc)[:500]}

    with engine.connect() as connection:
        links = [
            dict(row)
            for row in connection.execute(
                text(
                    """
                    SELECT e.external_id id_product,l.link_status,l.mapping_method
                    FROM external_catalog_print_links l
                    JOIN external_catalog_products e ON e.id=l.external_product_id
                    WHERE l.print_id=44257 AND e.source='cardmarket'
                    ORDER BY l.link_status,e.external_id
                    """
                )
            ).mappings().all()
        ]
    accepted = [row for row in links if row["link_status"] in ("accepted", "mapped", "exact")]
    quarantined = [row for row in links if row["link_status"] == "quarantined"]
    report["point1"] = {"accepted": accepted, "quarantined": quarantined}
    if len(accepted) != 1 or str(accepted[0]["id_product"]) != "720061":
        failures.append("point1:44257_wrong_mapping")
    if not any(str(row["id_product"]) == "749435" for row in quarantined):
        failures.append("point1:749435_not_quarantined")

    session = requests.Session()

    def get(name: str, url: str, timeout: int = 25):
        started = time.time()
        try:
            response = session.get(url, timeout=timeout)
        except Exception as exc:  # pragma: no cover - audit path
            failures.append(f"{name}:{type(exc).__name__}")
            report["http"][name] = {"error": str(exc), "seconds": round(time.time() - started, 3)}
            return None
        elapsed = round(time.time() - started, 3)
        report["http"][name] = {"status": response.status_code, "seconds": elapsed}
        if response.status_code != 200:
            failures.append(f"{name}:http_{response.status_code}")
            report["http"][name]["body"] = response.text[:250]
            return None
        return response.json()

    sets1 = get("sets1", "https://api.dontripit.com/api/v1/sets?game=mtg&limit=5&offset=0&meta=1")
    sets2 = get("sets2", "https://api.dontripit.com/api/v1/sets?game=mtg&limit=5&offset=5&meta=1")
    if sets1 and sets2 and (sets1["total"] != sets2["total"] or not _disjoint(sets1["items"], sets2["items"])):
        failures.append("sets_directory:paging")

    deep = get("prints1200", "https://api.dontripit.com/api/v1/prints?game=mtg&limit=5&offset=1200&meta=1")
    if deep and (deep["offset"] != 1200 or len(deep["items"]) != 5 or deep["total"] < 1205):
        failures.append("prints:deep_paging")

    singles1 = get("search_singles1", "https://dontripit.com/api/search-v2/federated?q=Luffy&game=onepiece&kind=singles&page=1&limit=5&sort=price_desc&has_price=1")
    singles2 = get("search_singles2", "https://dontripit.com/api/search-v2/federated?q=Luffy&game=onepiece&kind=singles&page=2&limit=5&sort=price_desc&has_price=1")
    if singles1 and singles2:
        first = singles1["singles"]
        second = singles2["singles"]
        first_prices = [item["cardmarket_price"] for item in first["items"]]
        second_prices = [item["cardmarket_price"] for item in second["items"]]
        if (
            first["total"] != second["total"]
            or not _disjoint(first["items"], second["items"], "print_id")
            or first_prices != sorted(first_prices, reverse=True)
            or second_prices != sorted(second_prices, reverse=True)
            or first_prices[-1] < second_prices[0]
        ):
            failures.append("search_singles:paging_order")
        target = [item for item in second["items"] if item["print_id"] == 44257]
        if not target or str(target[0].get("market", {}).get("reference", {}).get("id_product")) != "720061":
            failures.append("search_singles:point1_regression")

    search_sets1 = get("search_sets1", "https://dontripit.com/api/search-v2/federated?q=Secret&game=mtg&kind=sets&page=1&limit=5")
    search_sets2 = get("search_sets2", "https://dontripit.com/api/search-v2/federated?q=Secret&game=mtg&kind=sets&page=2&limit=5")
    if search_sets1 and search_sets2:
        if (
            search_sets1["sets_page"]["page"] != 1
            or search_sets2["sets_page"]["page"] != 2
            or search_sets1["sets_page"]["total"] != search_sets2["sets_page"]["total"]
            or not _disjoint(search_sets1["sets"], search_sets2["sets"])
        ):
            failures.append("search_sets:paging")

    sealed1 = get("search_sealed1", "https://dontripit.com/api/search-v2/federated?q=Luffy&game=onepiece&kind=sealed&page=1&limit=5")
    sealed2 = get("search_sealed2", "https://dontripit.com/api/search-v2/federated?q=Luffy&game=onepiece&kind=sealed&page=2&limit=5")
    if sealed1 and sealed2:
        first = sealed1["sealed"]
        second = sealed2["sealed"]
        if (
            first["page"] != 1
            or second["page"] != 2
            or first["total"] != second["total"]
            or not _disjoint(first["items"], second["items"])
        ):
            failures.append("search_sealed:paging")

    op1 = get("op15_1", "https://api.dontripit.com/api/v1/set-ui/prints?game=onepiece&set_code=op-15&sort=price_desc&has_price=1&limit=5&offset=0")
    op2 = get("op15_2", "https://api.dontripit.com/api/v1/set-ui/prints?game=onepiece&set_code=op-15&sort=price_desc&has_price=1&limit=5&offset=5")
    if op1 and op2:
        first_prices = [item["cardmarket_price"] for item in op1["items"]]
        second_prices = [item["cardmarket_price"] for item in op2["items"]]
        if (
            op1["total"] != op2["total"]
            or not _disjoint(op1["items"], op2["items"], "print_id")
            or first_prices != sorted(first_prices, reverse=True)
            or second_prices != sorted(second_prices, reverse=True)
            or first_prices[-1] < second_prices[0]
        ):
            failures.append("set_ui:paging_order")

    get("search_all", "https://dontripit.com/api/search-v2/federated?q=Luffy&game=onepiece&kind=all&page=1&limit=5")

    for name in ("search_singles1", "search_singles2", "search_sets1", "search_sets2"):
        if report["http"].get(name, {}).get("seconds", 99) > 15:
            failures.append(f"{name}:slow_{report['http'][name]['seconds']}s")
    if report["http"].get("search_all", {}).get("seconds", 99) > 18:
        failures.append(f"search_all:slow_{report['http']['search_all']['seconds']}s")

    report["pass"] = not failures
    OUT.write_text(json.dumps(report, indent=2, sort_keys=True, default=str) + "\n")
    print(json.dumps(report, indent=2, sort_keys=True, default=str))
    return 0 if report["pass"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
