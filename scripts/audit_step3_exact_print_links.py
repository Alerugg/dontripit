from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

import requests
from sqlalchemy import create_engine, text


ACTIVE_GAMES = ("mtg", "onepiece", "pokemon", "yugioh")
ROOT = Path(__file__).resolve().parents[1]
REPORT_PATH = Path("/tmp/step3-exact-print-links-final.json")


def main() -> int:
    failures: list[str] = []
    report: dict = {"pass": False, "hard_failures": failures, "static": {}, "db": {}, "http": {}}

    # ---------- Static public-surface contracts ----------
    files = {
        "catalog_card": ROOT / "frontend/components/catalog/CatalogCard.js",
        "search_results": ROOT / "frontend/components/searchV2/SearchV2Results.js",
        "card_detail": ROOT / "frontend/components/cards/CardDetailLayout.js",
        "print_detail": ROOT / "frontend/app/prints/[id]/page.js",
        "set_bff": ROOT / "frontend/app/api/catalog/set-detail/route.js",
        "federated_bff": ROOT / "frontend/app/api/search-v2/federated/route.js",
        "card_prints_backend": ROOT / "backend/app/routes/card_prints.py",
        "catalog_backend": ROOT / "backend/app/routes/catalog.py",
    }
    content = {name: path.read_text() for name, path in files.items()}

    fake_patterns = ("Carta #", "Carta sin título")
    fake_hits = {
        name: [pattern for pattern in fake_patterns if pattern in source]
        for name, source in content.items()
        if any(pattern in source for pattern in fake_patterns)
    }
    if fake_hits:
        failures.append(f"static:fabricated_names:{fake_hits}")

    required = {
        "catalog_card": ["getPrintHref(exactPrintId)", "Lanzamiento:", "Print ${item.print_id}", "Nombre no disponible"],
        "search_results": ["`/prints/${printId}`", "Lanzamiento físico:", "Set/carta de origen:", "idProduct"],
        "card_detail": ["fetchCardPrintsPage", "PRINTS_PAGE_SIZE = 24", "getPrintHref(selectedPrint.id)", "Lanzamiento físico:", "Set/carta de origen:", "Print ID"],
        "print_detail": ["fetchPrintPhysicalReleases", "Versión física exacta", "Lanzamiento físico", "Set/carta de origen", "Cardmarket idProduct exacto", "Print ID"],
        "set_bff": ["physical_releases", "physical_release_names", "name: cardName"],
        "federated_bff": ["name: cardName || null", "marketFromSearchItem"],
        "card_prints_backend": ["/api/v1/cards/<int:card_id>/prints", "/api/v1/prints/<int:print_id>/physical-releases", "physical_release_names", "total"],
        "catalog_backend": ["prints_pagination", "prints_total", '"reader": f"/api/v1/cards/{card_id}/prints"'],
    }
    missing = {}
    for name, tokens in required.items():
        miss = [token for token in tokens if token not in content[name]]
        if miss:
            missing[name] = miss
    if missing:
        failures.append(f"static:missing_contracts:{missing}")
    report["static"] = {"fake_name_hits": fake_hits, "missing_contracts": missing}

    # ---------- Production DB identity invariants ----------
    db_url = os.getenv("DATABASE_URL_UNPOOLED") or os.getenv("DATABASE_URL")
    if not db_url:
        failures.append("db:missing_database_url")
    else:
        engine = create_engine(db_url, pool_pre_ping=True)
        with engine.connect() as conn:
            games = {}
            for game in ACTIVE_GAMES:
                row = conn.execute(
                    text(
                        """
                        SELECT COUNT(*) AS prints,
                               COUNT(*) FILTER (WHERE trim(COALESCE(c.name,''))='') AS blank_names,
                               COUNT(*) FILTER (
                                 WHERE lower(trim(COALESCE(c.name,''))) ~ '^(unknown|unnamed|card #[0-9]+|carta #[0-9]+)$'
                               ) AS placeholder_names
                        FROM prints p
                        JOIN cards c ON c.id=p.card_id
                        JOIN games g ON g.id=c.game_id
                        WHERE g.slug=:game
                        """
                    ),
                    {"game": game},
                ).mappings().one()
                games[game] = dict(row)
                if row["blank_names"] or row["placeholder_names"]:
                    failures.append(f"db:{game}:non_real_names")

            accepted = conn.execute(
                text(
                    """
                    WITH accepted AS (
                      SELECT l.print_id,e.id,e.external_id,e.game_id,e.product_group
                      FROM external_catalog_print_links l
                      JOIN external_catalog_products e ON e.id=l.external_product_id
                      WHERE e.source='cardmarket' AND l.link_status IN ('accepted','mapped','exact')
                    ), grouped AS (
                      SELECT print_id,COUNT(DISTINCT id) AS products FROM accepted GROUP BY print_id
                    )
                    SELECT COUNT(*) AS accepted_prints,
                           COUNT(*) FILTER (WHERE products>1) AS ambiguous_accepted_prints
                    FROM grouped
                    """
                )
            ).mappings().one()
            wrong = int(
                conn.execute(
                    text(
                        """
                        SELECT COUNT(*)
                        FROM external_catalog_print_links l
                        JOIN external_catalog_products e ON e.id=l.external_product_id
                        JOIN prints p ON p.id=l.print_id
                        JOIN cards c ON c.id=p.card_id
                        WHERE e.source='cardmarket'
                          AND l.link_status IN ('accepted','mapped','exact')
                          AND (e.product_group<>'single' OR e.game_id<>c.game_id)
                        """
                    )
                ).scalar_one()
            )
            if accepted["ambiguous_accepted_prints"] or wrong:
                failures.append("db:accepted_cardmarket_not_one_exact_product")

            card_counts = {
                "sol_ring": int(conn.execute(text("SELECT COUNT(*) FROM prints WHERE card_id=127206")).scalar_one()),
                "blue_eyes": int(conn.execute(text("SELECT COUNT(*) FROM prints WHERE card_id=72296")).scalar_one()),
            }
            if card_counts["sol_ring"] != 170:
                failures.append(f"db:sol_ring_count:{card_counts['sol_ring']}")
            if card_counts["blue_eyes"] != 78:
                failures.append(f"db:blue_eyes_count:{card_counts['blue_eyes']}")

            luffy_releases = [
                dict(row)
                for row in conn.execute(
                    text(
                        """
                        SELECT cr.id,cr.source,cr.external_id,cr.name,cr.code
                        FROM print_releases pr
                        JOIN catalog_releases cr ON cr.id=pr.release_id
                        WHERE pr.print_id=44257
                        ORDER BY cr.id
                        """
                    )
                ).mappings().all()
            ]
            if not any("PILLARS OF STRENGTH" in str(row.get("name") or "").upper() for row in luffy_releases):
                failures.append("db:luffy_44257_missing_pillars_release")

            luffy_links = [
                dict(row)
                for row in conn.execute(
                    text(
                        """
                        SELECT e.external_id AS id_product,l.link_status,l.mapping_method
                        FROM external_catalog_print_links l
                        JOIN external_catalog_products e ON e.id=l.external_product_id
                        WHERE l.print_id=44257 AND e.source='cardmarket'
                        ORDER BY l.link_status,e.external_id
                        """
                    )
                ).mappings().all()
            ]
            accepted_luffy = [row for row in luffy_links if row["link_status"] in ("accepted", "mapped", "exact")]
            if len(accepted_luffy) != 1 or str(accepted_luffy[0]["id_product"]) != "720061":
                failures.append(f"db:luffy_wrong_accepted:{accepted_luffy}")
            if not any(str(row["id_product"]) == "749435" and row["link_status"] == "quarantined" for row in luffy_links):
                failures.append("db:luffy_wrong_old_product_not_quarantined")

            report["db"] = {
                "games": games,
                "accepted_cardmarket": {**dict(accepted), "wrong_game_or_group": wrong},
                "card_counts": card_counts,
                "luffy_44257_releases": luffy_releases,
                "luffy_44257_links": luffy_links,
            }

    # ---------- Production HTTP/BFF exact-link contract ----------
    session = requests.Session()

    def get(name: str, url: str, *, timeout: int = 25):
        started = time.time()
        try:
            response = session.get(url, timeout=timeout)
        except Exception as exc:  # pragma: no cover - CI diagnostic
            report["http"][name] = {"error": str(exc), "seconds": round(time.time() - started, 3)}
            failures.append(f"http:{name}:{type(exc).__name__}")
            return None
        elapsed = round(time.time() - started, 3)
        report["http"][name] = {"status": response.status_code, "seconds": elapsed}
        if response.status_code != 200:
            report["http"][name]["body"] = response.text[:500]
            failures.append(f"http:{name}:status_{response.status_code}")
            return None
        try:
            return response.json()
        except Exception:
            report["http"][name]["body"] = response.text[:500]
            failures.append(f"http:{name}:invalid_json")
            return None

    legacy = get("sol_ring_legacy_card", "https://dontripit.com/api/catalog/cards/127206")
    sol1 = get("sol_ring_prints_1", "https://dontripit.com/api/catalog/cards/127206/prints?limit=24&offset=0")
    sol2 = get("sol_ring_prints_2", "https://dontripit.com/api/catalog/cards/127206/prints?limit=24&offset=24")
    sollast = get("sol_ring_prints_last", "https://dontripit.com/api/catalog/cards/127206/prints?limit=24&offset=168")
    blue = get("blue_eyes_prints_3", "https://dontripit.com/api/catalog/cards/72296/prints?limit=24&offset=48")
    release = get("luffy_physical_release", "https://dontripit.com/api/catalog/prints/44257/physical-releases")
    op03 = get("op03_luffy_checklist", "https://dontripit.com/api/catalog/set-detail?game=onepiece&set_code=op-03&q=ST01-012&limit=10&offset=0&sort=number_asc")
    search = get("luffy_search_page2", "https://dontripit.com/api/search-v2/federated?q=Luffy&game=onepiece&kind=singles&page=2&limit=5&sort=price_desc&has_price=1")
    price = get("luffy_exact_price", "https://dontripit.com/api/prices/print/44257")

    if legacy:
        pagination = legacy.get("prints_pagination") or {}
        if pagination.get("total") != 170 or pagination.get("complete") is not False or pagination.get("reader") != "/api/v1/cards/127206/prints":
            failures.append(f"http:legacy_card_pagination:{pagination}")
        if len(legacy.get("prints") or []) != 50:
            failures.append("http:legacy_card_first_chunk_not_50")
        if len(legacy.get("sets") or []) <= 50:
            failures.append("http:legacy_card_sets_still_derived_from_first_50")

    if sol1 and sol2 and sollast:
        pages = (sol1, sol2, sollast)
        if any(page.get("total") != 170 for page in pages):
            failures.append("http:sol_ring_total_mismatch")
        ids1 = {int(item["print_id"]) for item in sol1.get("items") or []}
        ids2 = {int(item["print_id"]) for item in sol2.get("items") or []}
        idslast = {int(item["print_id"]) for item in sollast.get("items") or []}
        if ids1 & ids2 or ids1 & idslast or ids2 & idslast:
            failures.append("http:sol_ring_duplicate_across_pages")
        if len(sol1.get("items") or []) != 24 or len(sol2.get("items") or []) != 24 or len(sollast.get("items") or []) != 2:
            failures.append("http:sol_ring_page_sizes")
        if sollast.get("complete") is not True:
            failures.append("http:sol_ring_last_page_not_complete")
        if any(str(item.get("card_name") or "").strip() != "Sol Ring" for page in pages for item in page.get("items") or []):
            failures.append("http:sol_ring_real_name_missing")

    if blue:
        if blue.get("total") != 78 or len(blue.get("items") or []) != 24:
            failures.append("http:blue_eyes_pagination")
        if any(str(item.get("card_name") or "").strip() != "Blue-Eyes White Dragon" for item in blue.get("items") or []):
            failures.append("http:blue_eyes_real_name_missing")
        if not any(item.get("physical_release_names") for item in blue.get("items") or []):
            failures.append("http:blue_eyes_missing_physical_release_evidence")

    if release:
        identity = release.get("print") or {}
        names = release.get("physical_release_names") or []
        if identity.get("print_id") != 44257 or identity.get("card_name") != "Monkey.D.Luffy":
            failures.append("http:luffy_release_wrong_print_identity")
        if identity.get("set_code") != "st-01" or not any("PILLARS OF STRENGTH" in str(name).upper() for name in names):
            failures.append("http:luffy_release_origin_or_physical_release_wrong")

    if op03:
        items = op03.get("cards") or []
        target = [item for item in items if int(item.get("print_id") or 0) == 44257]
        if len(target) != 1:
            failures.append(f"http:op03_target_count:{len(target)}")
        else:
            item = target[0]
            if item.get("name") != "Monkey.D.Luffy":
                failures.append("http:op03_target_real_name")
            if str(item.get("cardmarket_id_product")) != "720061":
                failures.append(f"http:op03_target_cardmarket:{item.get('cardmarket_id_product')}")
            if not any("PILLARS OF STRENGTH" in str(name).upper() for name in item.get("physical_release_names") or []):
                failures.append("http:op03_target_physical_release")
            if item.get("set_code") != "st-01":
                failures.append("http:op03_target_origin_not_preserved")

    if search:
        items = search.get("singles", {}).get("items") or []
        target = [item for item in items if int(item.get("print_id") or 0) == 44257]
        if len(target) != 1:
            failures.append("http:luffy_search_target_missing")
        else:
            ref = target[0].get("market", {}).get("reference", {})
            if str(ref.get("id_product")) != "720061":
                failures.append(f"http:luffy_search_wrong_cardmarket:{ref}")

    if price:
        cm = price.get("cardmarket") or price.get("price", {}).get("cardmarket") or {}
        if str(cm.get("id_product")) != "720061":
            failures.append(f"http:luffy_price_wrong_cardmarket:{cm}")

    for name, meta in report["http"].items():
        if meta.get("seconds", 0) > 20:
            failures.append(f"http:{name}:slow_{meta['seconds']}s")

    report["pass"] = not failures
    REPORT_PATH.write_text(json.dumps(report, indent=2, sort_keys=True, default=str) + "\n")
    print(json.dumps({"pass": report["pass"], "hard_failures": failures, "http": report["http"]}, indent=2, sort_keys=True))
    return 0 if report["pass"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
