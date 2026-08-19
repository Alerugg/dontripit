from __future__ import annotations

import json
import os
from collections import Counter
from decimal import Decimal
from pathlib import Path

import psycopg2
from psycopg2.extras import RealDictCursor

ACCEPTED = ("accepted", "mapped", "exact")
EXPECTED = {
    "DUAD": {
        "exp": "6129",
        "set": "DUAD",
        "count": 116,
        "methods": {
            "cardmarket_ocg_certified_unique_physical_v2": 38,
            "cardmarket_ocg_certified_image_bijection_v2": 76,
            "cardmarket_ocg_certified_version_ordinal_v1": 2,
        },
    },
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


def positive(value):
    try:
        return value is not None and Decimal(str(value)) > 0
    except Exception:
        return False


def meaningful(row):
    return any(positive(row.get(key)) for key in ("price_low", "price_mid", "price_market", "price_last"))


def main() -> int:
    url = os.getenv("DATABASE_URL_UNPOOLED") or os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL_UNPOOLED or DATABASE_URL is required")
    conn = psycopg2.connect(
        url,
        connect_timeout=30,
        application_name="dontripit_ygo_ocg_production_proof_v6",
    )
    conn.set_session(readonly=True, autocommit=False)
    failures = []
    report = {"production_writes": 0, "surfaces": {}}
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
                    """SELECT l.mapping_method,l.confidence,l.reviewed,e.id external_product_id,
                              e.external_id id_product,e.last_seen_at,p.id print_id,p.language,
                              p.collector_number,p.variant,p.rarity,c.name card_name,s.code set_code
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
                links = [dict(row) for row in cur.fetchall()]
                n = len(links)
                products = len({str(row["id_product"]) for row in links})
                prints = len({int(row["print_id"]) for row in links})
                methods = dict(Counter(str(row.get("mapping_method") or "") for row in links))
                wrong_lang = [row for row in links if str(row.get("language") or "").casefold() != "ja"]
                wrong_set = [row for row in links if str(row.get("set_code") or "").upper() != cfg["set"]]
                stale = [row for row in links if capture is not None and row.get("last_seen_at") != capture]
                wrong_conf = [row for row in links if str(row.get("confidence") or "") != "exact"]
                unreviewed = [row for row in links if not bool(row.get("reviewed"))]

                expected_count = cfg["count"]
                if (n, products, prints) != (expected_count, expected_count, expected_count):
                    failures.append(
                        f"{label}_identity_{n}_{products}_{prints}_expected_{expected_count}"
                    )
                if cfg["methods"] is not None and methods != cfg["methods"]:
                    failures.append(f"{label}_methods_{methods}")
                if wrong_lang:
                    failures.append(f"{label}_wrong_language_{len(wrong_lang)}")
                if wrong_set:
                    failures.append(f"{label}_wrong_set_{len(wrong_set)}")
                if stale:
                    failures.append(f"{label}_stale_{len(stale)}")
                if wrong_conf:
                    failures.append(f"{label}_wrong_confidence_{len(wrong_conf)}")
                if unreviewed:
                    failures.append(f"{label}_unreviewed_{len(unreviewed)}")

                ext_priceable = set()
                canonical_priceable = set()
                wrong_product = []
                missing_exact_raw_id_product = []
                ext_ids = [int(row["external_product_id"]) for row in links]
                print_ids = [int(row["print_id"]) for row in links]

                if ext_ids and price_as_of is not None:
                    cur.execute(
                        """SELECT external_product_id,price_low,price_mid,price_market,price_last
                           FROM external_market_price_snapshots
                           WHERE external_product_id=ANY(%s) AND currency='EUR' AND as_of=%s""",
                        (ext_ids, price_as_of),
                    )
                    for row in cur.fetchall():
                        data = dict(row)
                        if meaningful(data):
                            ext_priceable.add(int(data["external_product_id"]))

                if print_ids and price_as_of is not None:
                    link_by_print = {int(row["print_id"]): str(row["id_product"]) for row in links}
                    cur.execute(
                        """SELECT ps.entity_id print_id,ps.price_low,ps.price_mid,ps.price_market,
                                  ps.price_last,ps.raw_json
                           FROM price_snapshots ps
                           JOIN price_sources src ON src.id=ps.source_id
                           WHERE src.name='cardmarket' AND ps.entity_type='print'
                             AND ps.entity_id=ANY(%s) AND ps.currency='EUR' AND ps.as_of=%s""",
                        (print_ids, price_as_of),
                    )
                    for row in cur.fetchall():
                        data = dict(row)
                        print_id = int(data["print_id"])
                        expected_product = link_by_print.get(print_id)
                        actual_product = str((data.get("raw_json") or {}).get("idProduct") or "")
                        if not actual_product:
                            missing_exact_raw_id_product.append(print_id)
                        elif actual_product != expected_product:
                            wrong_product.append(
                                {
                                    "print_id": print_id,
                                    "expected": expected_product,
                                    "actual": actual_product,
                                }
                            )
                        elif meaningful(data):
                            canonical_priceable.add(print_id)

                if wrong_product:
                    failures.append(f"{label}_wrong_canonical_idProduct_{len(wrong_product)}")
                # Only externally-priceable rows must have a meaningful exact canonical projection.
                # A canonical historical row without raw idProduct cannot count as exact priceable.
                if len(canonical_priceable) != len(ext_priceable):
                    failures.append(
                        f"{label}_price_projection_{len(canonical_priceable)}_vs_external_{len(ext_priceable)}"
                    )

                unpriced = [
                    {
                        "idProduct": str(row["id_product"]),
                        "print_id": int(row["print_id"]),
                        "collector_number": row["collector_number"],
                        "card_name": row["card_name"],
                        "variant": row["variant"],
                        "rarity": row["rarity"],
                    }
                    for row in links
                    if int(row["external_product_id"]) not in ext_priceable
                ]
                report["surfaces"][label] = {
                    "accepted_links": n,
                    "unique_products": products,
                    "unique_prints": prints,
                    "method_counts": methods,
                    "wrong_language": len(wrong_lang),
                    "wrong_set": len(wrong_set),
                    "stale_products": len(stale),
                    "wrong_confidence": len(wrong_conf),
                    "unreviewed": len(unreviewed),
                    "externally_priceable": len(ext_priceable),
                    "canonical_exact_idProduct_priceable": len(canonical_priceable),
                    "canonical_wrong_idProduct": len(wrong_product),
                    "canonical_rows_without_raw_idProduct": len(missing_exact_raw_id_product),
                    "unpriced": unpriced,
                }
            conn.rollback()
    finally:
        conn.close()

    report["failures"] = failures
    report["status"] = "pass" if not failures else "fail"
    out = Path(
        os.getenv(
            "YGO_OCG_PRODUCTION_PROOF_V6_OUTPUT",
            "/tmp/yugioh-ocg-production-proof-v6.json",
        )
    )
    text = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n"
    out.write_text(text, encoding="utf-8")
    print(text, end="")
    return 0 if not failures else 2


if __name__ == "__main__":
    raise SystemExit(main())
