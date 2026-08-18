from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from decimal import Decimal
from pathlib import Path

import psycopg2
from psycopg2.extras import RealDictCursor

ACCEPTED = ("accepted", "mapped", "exact")
EXPECTED = {
    "ALIN": {
        "exp": "6025",
        "set": "ALIN",
        "count": 132,
        "methods": {
            "cardmarket_ocg_certified_unique_physical_v2": 55,
            "cardmarket_ocg_certified_version_ordinal_v1": 77,
        },
    },
    "POTE": {
        "exp": "5044",
        "set": "POTE",
        "count": 126,
        "methods": {
            "cardmarket_ocg_certified_unique_physical_v2": 63,
            "cardmarket_ocg_certified_image_bijection_v2": 24,
            "cardmarket_ocg_certified_version_ordinal_v1": 39,
        },
    },
    "AGOV": {"exp": "5421", "set": "AGOV", "count": 98, "methods": None},
}


def _positive(value) -> bool:
    if value is None:
        return False
    try:
        return Decimal(str(value)) > 0
    except Exception:
        return False


def _meaningful(row: dict) -> bool:
    return any(_positive(row.get(key)) for key in ("price_low", "price_mid", "price_market", "price_last"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--require-complete", action="store_true")
    parser.add_argument(
        "--output",
        default=os.getenv(
            "YGO_OCG_PRICE_REPAIR_AUDIT_OUTPUT",
            "/tmp/yugioh-ocg-price-projection-repair-v1.json",
        ),
    )
    args = parser.parse_args()

    url = os.getenv("DATABASE_URL_UNPOOLED") or os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL_UNPOOLED or DATABASE_URL is required")
    conn = psycopg2.connect(
        url,
        connect_timeout=30,
        application_name="dontripit_ygo_ocg_price_projection_repair_v1",
    )
    conn.set_session(readonly=True, autocommit=False)

    failures: list[str] = []
    report: dict = {
        "mode": "final" if args.require_complete else "preflight",
        "production_writes": 0,
        "surfaces": {},
    }
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT id FROM games WHERE slug='yugioh' LIMIT 1")
            game_id = int(cur.fetchone()["id"])
            cur.execute("SELECT max(last_seen_at) capture FROM external_catalog_products WHERE source='cardmarket'")
            capture = cur.fetchone()["capture"]
            cur.execute(
                """SELECT max(mp.as_of) ts
                   FROM external_market_price_snapshots mp
                   JOIN external_catalog_products e ON e.id=mp.external_product_id
                   WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single'""",
                (game_id,),
            )
            price_as_of = cur.fetchone()["ts"]
            report["cardmarket_capture"] = str(capture)
            report["price_guide_as_of"] = str(price_as_of)

            for label, cfg in EXPECTED.items():
                cur.execute(
                    """SELECT l.mapping_method,l.confidence,l.reviewed,
                              e.id external_product_id,e.external_id id_product,e.last_seen_at,
                              p.id print_id,p.language,p.collector_number,p.variant,p.rarity,
                              c.name card_name,s.code set_code
                       FROM external_catalog_print_links l
                       JOIN external_catalog_products e ON e.id=l.external_product_id
                       JOIN prints p ON p.id=l.print_id
                       JOIN cards c ON c.id=p.card_id
                       JOIN sets s ON s.id=p.set_id
                       WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single'
                         AND e.expansion_external_id=%s AND l.link_status=ANY(%s)
                       ORDER BY e.external_id::bigint""",
                    (game_id, cfg["exp"], list(ACCEPTED)),
                )
                links = [dict(r) for r in cur.fetchall()]
                n = len(links)
                products = len({str(r["id_product"]) for r in links})
                prints = len({int(r["print_id"]) for r in links})
                methods = dict(Counter(str(r.get("mapping_method") or "") for r in links))
                wrong_language = [r for r in links if str(r.get("language") or "").casefold() != "ja"]
                wrong_set = [r for r in links if str(r.get("set_code") or "").upper() != cfg["set"]]
                stale = [r for r in links if capture is not None and r.get("last_seen_at") != capture]
                wrong_confidence = [r for r in links if str(r.get("confidence") or "") != "exact"]
                unreviewed = [r for r in links if not bool(r.get("reviewed"))]

                if (n, products, prints) != (cfg["count"], cfg["count"], cfg["count"]):
                    failures.append(
                        f"{label}_identity_{n}_{products}_{prints}_expected_{cfg['count']}"
                    )
                if cfg["methods"] is not None and methods != cfg["methods"]:
                    failures.append(f"{label}_method_counts_{methods}")
                if wrong_language:
                    failures.append(f"{label}_wrong_language_{len(wrong_language)}")
                if wrong_set:
                    failures.append(f"{label}_wrong_set_{len(wrong_set)}")
                if stale:
                    failures.append(f"{label}_stale_products_{len(stale)}")
                if wrong_confidence:
                    failures.append(f"{label}_wrong_confidence_{len(wrong_confidence)}")
                if unreviewed:
                    failures.append(f"{label}_unreviewed_{len(unreviewed)}")

                external_ids = [int(r["external_product_id"]) for r in links]
                print_ids = [int(r["print_id"]) for r in links]
                external_priceable: set[int] = set()
                if external_ids and price_as_of is not None:
                    cur.execute(
                        """SELECT external_product_id,price_low,price_mid,price_market,price_last
                           FROM external_market_price_snapshots
                           WHERE external_product_id=ANY(%s) AND currency='EUR' AND as_of=%s""",
                        (external_ids, price_as_of),
                    )
                    for row in cur.fetchall():
                        item = dict(row)
                        if _meaningful(item):
                            external_priceable.add(int(item["external_product_id"]))

                link_by_print = {int(r["print_id"]): str(r["id_product"]) for r in links}
                external_by_print = {
                    int(r["print_id"]): int(r["external_product_id"]) for r in links
                }
                canonical_priceable: set[int] = set()
                canonical_current_rows: set[int] = set()
                canonical_wrong_product: list[dict] = []
                if print_ids and price_as_of is not None:
                    cur.execute(
                        """SELECT ps.entity_id print_id,ps.price_low,ps.price_mid,ps.price_market,ps.price_last,ps.raw_json
                           FROM price_snapshots ps
                           JOIN price_sources src ON src.id=ps.source_id
                           WHERE src.name='cardmarket' AND ps.entity_type='print'
                             AND ps.entity_id=ANY(%s) AND ps.currency='EUR' AND ps.as_of=%s""",
                        (print_ids, price_as_of),
                    )
                    for row in cur.fetchall():
                        item = dict(row)
                        print_id = int(item["print_id"])
                        canonical_current_rows.add(print_id)
                        expected_product = link_by_print.get(print_id)
                        actual_product = str((item.get("raw_json") or {}).get("idProduct") or "")
                        if actual_product and actual_product != expected_product:
                            canonical_wrong_product.append(
                                {
                                    "print_id": print_id,
                                    "expected": expected_product,
                                    "actual": actual_product,
                                }
                            )
                        elif actual_product == expected_product and _meaningful(item):
                            canonical_priceable.add(print_id)

                expected_priceable_prints = {
                    print_id
                    for print_id, external_id in external_by_print.items()
                    if external_id in external_priceable
                }
                unpriceable_prints = set(print_ids) - expected_priceable_prints
                current_rows_for_unpriceable = canonical_current_rows & unpriceable_prints

                if canonical_wrong_product:
                    failures.append(
                        f"{label}_canonical_wrong_idProduct_{len(canonical_wrong_product)}"
                    )
                if args.require_complete:
                    if canonical_priceable != expected_priceable_prints:
                        missing = sorted(expected_priceable_prints - canonical_priceable)
                        extra = sorted(canonical_priceable - expected_priceable_prints)
                        failures.append(
                            f"{label}_projection_incomplete_expected_{len(expected_priceable_prints)}_actual_{len(canonical_priceable)}_missing_{len(missing)}_extra_{len(extra)}"
                        )
                    if current_rows_for_unpriceable:
                        failures.append(
                            f"{label}_current_rows_for_unpriceable_{len(current_rows_for_unpriceable)}"
                        )

                report["surfaces"][label] = {
                    "accepted_links": n,
                    "unique_products": products,
                    "unique_prints": prints,
                    "method_counts": methods,
                    "wrong_language": len(wrong_language),
                    "wrong_set": len(wrong_set),
                    "stale_products": len(stale),
                    "wrong_confidence": len(wrong_confidence),
                    "unreviewed": len(unreviewed),
                    "externally_priceable": len(expected_priceable_prints),
                    "canonical_exact_idProduct_priceable": len(canonical_priceable),
                    "canonical_wrong_idProduct": len(canonical_wrong_product),
                    "current_rows_for_unpriceable": len(current_rows_for_unpriceable),
                    "projection_gap": len(expected_priceable_prints - canonical_priceable),
                    "unpriced_external_products": cfg["count"] - len(expected_priceable_prints),
                }
            conn.rollback()
    finally:
        conn.close()

    report["failures"] = failures
    report["status"] = "pass" if not failures else "fail"
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n"
    output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if not failures else 2


if __name__ == "__main__":
    raise SystemExit(main())
