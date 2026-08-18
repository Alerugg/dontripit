from __future__ import annotations

import json
import os
from collections import Counter
from decimal import Decimal
from pathlib import Path

import psycopg2
from psycopg2.extras import RealDictCursor


GAME = "yugioh"
EXPANSION_ID = "5421"
SET_CODE = "AGOV"
EXPECTED_LINKS = 59
ACCEPTED = ("accepted", "mapped", "exact")


def _connect():
    url = os.getenv("DATABASE_URL_UNPOOLED") or os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL_UNPOOLED or DATABASE_URL is required")
    conn = psycopg2.connect(
        url,
        connect_timeout=30,
        application_name="dontripit_ygo_agov_jp_production_proof_v1",
    )
    conn.set_session(readonly=True, autocommit=False)
    return conn


def _positive(value) -> bool:
    if value is None:
        return False
    try:
        return Decimal(str(value)) > 0
    except Exception:
        return False


def _meaningful(row: dict) -> bool:
    return any(_positive(row.get(k)) for k in ("price_low", "price_mid", "price_market", "price_last"))


def _price_variant(row: dict) -> str | None:
    variant = str(row.get("variant") or "").strip().lower()
    if "etched" in variant or "glossy" in variant:
        return None
    return "foil" if bool(row.get("is_foil")) else "nonfoil"


def run() -> tuple[dict, int]:
    conn = _connect()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT id FROM games WHERE slug=%s LIMIT 1", (GAME,))
            game = cur.fetchone()
            if not game:
                raise RuntimeError("Yu-Gi-Oh game row not found")
            game_id = int(game["id"])

            cur.execute("SELECT max(last_seen_at) AS ts FROM external_catalog_products WHERE source='cardmarket'")
            catalog_capture = cur.fetchone()["ts"]

            cur.execute(
                """
                SELECT max(mp.as_of) AS ts
                FROM external_market_price_snapshots mp
                JOIN external_catalog_products e ON e.id=mp.external_product_id
                WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single'
                """,
                (game_id,),
            )
            price_as_of = cur.fetchone()["ts"]

            cur.execute(
                """
                SELECT l.id AS link_id,l.mapping_method,l.confidence,l.link_status,l.reviewed,
                       e.id AS external_product_id,e.external_id AS id_product,e.name AS market_name,
                       e.metacard_external_id,e.expansion_external_id,e.last_seen_at,
                       p.id AS print_id,p.language,p.collector_number,p.variant,p.rarity,p.is_foil,
                       s.code AS set_code,c.name AS card_name
                FROM external_catalog_print_links l
                JOIN external_catalog_products e ON e.id=l.external_product_id
                JOIN prints p ON p.id=l.print_id
                JOIN cards c ON c.id=p.card_id
                JOIN sets s ON s.id=p.set_id
                WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single'
                  AND e.expansion_external_id=%s
                  AND l.link_status = ANY(%s)
                ORDER BY e.external_id,p.id
                """,
                (game_id, EXPANSION_ID, list(ACCEPTED)),
            )
            links = [dict(r) for r in cur.fetchall()]

            product_ids = [int(r["external_product_id"]) for r in links]
            print_ids = [int(r["print_id"]) for r in links]

            external_prices: dict[tuple[int, str], dict] = {}
            if price_as_of is not None and product_ids:
                cur.execute(
                    """
                    SELECT external_product_id,price_variant,as_of,price_low,price_mid,price_market,price_last,
                           avg1,avg7,avg30
                    FROM external_market_price_snapshots
                    WHERE external_product_id = ANY(%s) AND currency='EUR' AND as_of=%s
                    """,
                    (product_ids, price_as_of),
                )
                for row in cur.fetchall():
                    d = dict(row)
                    external_prices[(int(d["external_product_id"]), str(d["price_variant"]))] = d

            canonical_prices: dict[int, list[dict]] = {}
            if price_as_of is not None and print_ids:
                cur.execute(
                    """
                    SELECT ps.entity_id AS print_id,ps.as_of,ps.price_low,ps.price_mid,ps.price_market,ps.price_last,
                           ps.raw_json
                    FROM price_snapshots ps
                    JOIN price_sources src ON src.id=ps.source_id
                    WHERE src.name='cardmarket' AND ps.entity_type='print'
                      AND ps.entity_id = ANY(%s) AND ps.currency='EUR' AND ps.as_of=%s
                    ORDER BY ps.entity_id
                    """,
                    (print_ids, price_as_of),
                )
                for row in cur.fetchall():
                    d = dict(row)
                    canonical_prices.setdefault(int(d["print_id"]), []).append(d)

            conn.rollback()
    finally:
        conn.close()

    unique_products = len({str(r["id_product"]) for r in links})
    unique_prints = len({int(r["print_id"]) for r in links})
    wrong_language = [r for r in links if str(r.get("language") or "").lower() != "ja"]
    wrong_set = [r for r in links if str(r.get("set_code") or "").upper() != SET_CODE]
    stale_products = [r for r in links if catalog_capture is not None and r.get("last_seen_at") != catalog_capture]

    price_rows = []
    externally_priceable = 0
    missing_external_price = 0
    unsupported_finish = 0
    exact_canonical_current = 0
    missing_canonical_current = 0
    mismatched_canonical_product = 0
    nonmeaningful_canonical = 0

    for link in links:
        variant = _price_variant(link)
        external = None if variant is None else external_prices.get((int(link["external_product_id"]), variant))
        if variant is None:
            unsupported_finish += 1
        elif external is None or not _meaningful(external):
            missing_external_price += 1
        else:
            externally_priceable += 1

        canon_rows = canonical_prices.get(int(link["print_id"]), [])
        exact = [
            r for r in canon_rows
            if str((r.get("raw_json") or {}).get("idProduct") or "") == str(link["id_product"])
        ]
        mismatch = [
            r for r in canon_rows
            if str((r.get("raw_json") or {}).get("idProduct") or "") not in ("", str(link["id_product"]))
        ]
        if mismatch:
            mismatched_canonical_product += 1
        if not exact:
            missing_canonical_current += 1
        elif any(_meaningful(r) for r in exact):
            exact_canonical_current += 1
        else:
            nonmeaningful_canonical += 1

        price_rows.append(
            {
                "print_id": int(link["print_id"]),
                "idProduct": str(link["id_product"]),
                "collector_number": link.get("collector_number"),
                "card_name": link.get("card_name"),
                "variant": link.get("variant"),
                "is_foil": bool(link.get("is_foil")),
                "expected_price_variant": variant,
                "external_current_meaningful": bool(external and _meaningful(external)),
                "canonical_current_exact_idProduct": bool(exact),
                "canonical_current_meaningful": bool(exact and any(_meaningful(r) for r in exact)),
                "canonical_current_mismatched_idProduct": bool(mismatch),
            }
        )

    method_hist = Counter(str(r.get("mapping_method") or "") for r in links)
    confidence_hist = Counter(str(r.get("confidence") or "") for r in links)

    failures = []
    if len(links) != EXPECTED_LINKS:
        failures.append(f"accepted_links_expected_{EXPECTED_LINKS}_got_{len(links)}")
    if unique_products != EXPECTED_LINKS:
        failures.append(f"unique_products_expected_{EXPECTED_LINKS}_got_{unique_products}")
    if unique_prints != EXPECTED_LINKS:
        failures.append(f"unique_prints_expected_{EXPECTED_LINKS}_got_{unique_prints}")
    if wrong_language:
        failures.append(f"wrong_language_links_{len(wrong_language)}")
    if wrong_set:
        failures.append(f"wrong_set_links_{len(wrong_set)}")
    if stale_products:
        failures.append(f"links_to_noncurrent_catalog_products_{len(stale_products)}")
    if mismatched_canonical_product:
        failures.append(f"canonical_price_wrong_idProduct_{mismatched_canonical_product}")
    if exact_canonical_current != externally_priceable:
        failures.append(
            f"canonical_exact_current_price_count_{exact_canonical_current}_does_not_match_external_priceable_{externally_priceable}"
        )

    report = {
        "status": "pass" if not failures else "fail",
        "production_writes": 0,
        "game": GAME,
        "certified_region": {"code": "AGOV-JP", "idExpansion": EXPANSION_ID, "canonical_set": SET_CODE, "language": "ja"},
        "catalog_capture": str(catalog_capture),
        "price_guide_as_of": str(price_as_of),
        "accepted_links": len(links),
        "unique_products": unique_products,
        "unique_prints": unique_prints,
        "wrong_language_links": len(wrong_language),
        "wrong_set_links": len(wrong_set),
        "links_to_noncurrent_catalog_products": len(stale_products),
        "mapping_methods": dict(method_hist),
        "confidence": dict(confidence_hist),
        "pricing": {
            "externally_priceable_links": externally_priceable,
            "missing_external_current_price": missing_external_price,
            "unsupported_finish": unsupported_finish,
            "canonical_current_exact_idProduct_prices": exact_canonical_current,
            "missing_canonical_current_price": missing_canonical_current,
            "nonmeaningful_canonical_current_price": nonmeaningful_canonical,
            "canonical_current_wrong_idProduct": mismatched_canonical_product,
        },
        "failures": failures,
        "samples": price_rows[:20],
        "unpriced_samples": [r for r in price_rows if not r["canonical_current_meaningful"]][:20],
    }
    return report, (0 if not failures else 2)


def main() -> int:
    report, code = run()
    output = os.getenv("YGO_AGOV_JP_PRODUCTION_PROOF_OUTPUT", "/tmp/yugioh-agov-jp-production-proof-v1.json")
    Path(output).write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True, default=str))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
