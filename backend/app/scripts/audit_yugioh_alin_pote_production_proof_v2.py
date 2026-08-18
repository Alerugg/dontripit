from __future__ import annotations

import json
import os
from collections import Counter
from decimal import Decimal
from pathlib import Path

import psycopg2
from psycopg2.extras import RealDictCursor


ACCEPTED = ("accepted", "mapped", "exact")
SURFACES = {
    "ALIN": {
        "idExpansion": "6025",
        "set_code": "ALIN",
        "expected": 55,
        "methods": {"cardmarket_ocg_certified_unique_physical_v2": 55},
    },
    "POTE": {
        "idExpansion": "5044",
        "set_code": "POTE",
        "expected": 87,
        "methods": {
            "cardmarket_ocg_certified_unique_physical_v2": 63,
            "cardmarket_ocg_certified_image_bijection_v2": 24,
        },
    },
}
AGOV_EXPECTED = 98


def _connect():
    url = os.getenv("DATABASE_URL_UNPOOLED") or os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL_UNPOOLED or DATABASE_URL is required")
    conn = psycopg2.connect(
        url,
        connect_timeout=30,
        application_name="dontripit_ygo_alin_pote_production_proof_v2",
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


def main() -> int:
    conn = _connect()
    failures: list[str] = []
    report: dict = {"status": "pass", "production_writes": 0, "surfaces": {}}
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT id FROM games WHERE slug='yugioh' LIMIT 1")
            game_id = int(cur.fetchone()["id"])
            cur.execute("SELECT max(last_seen_at) capture FROM external_catalog_products WHERE source='cardmarket'")
            catalog_capture = cur.fetchone()["capture"]
            cur.execute(
                """SELECT max(mp.as_of) ts FROM external_market_price_snapshots mp
                   JOIN external_catalog_products e ON e.id=mp.external_product_id
                   WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single'""",
                (game_id,),
            )
            price_as_of = cur.fetchone()["ts"]
            report["cardmarket_capture"] = str(catalog_capture)
            report["price_guide_as_of"] = str(price_as_of)

            for label, cfg in SURFACES.items():
                cur.execute(
                    """SELECT l.id link_id,l.mapping_method,l.confidence,l.link_status,l.reviewed,
                              e.id external_product_id,e.external_id id_product,e.last_seen_at,
                              p.id print_id,p.language,p.collector_number,p.variant,p.rarity,p.is_foil,
                              c.name card_name,s.code set_code
                       FROM external_catalog_print_links l
                       JOIN external_catalog_products e ON e.id=l.external_product_id
                       JOIN prints p ON p.id=l.print_id
                       JOIN cards c ON c.id=p.card_id
                       JOIN sets s ON s.id=p.set_id
                       WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single'
                         AND e.expansion_external_id=%s AND l.link_status=ANY(%s)
                       ORDER BY e.external_id::bigint""",
                    (game_id, cfg["idExpansion"], list(ACCEPTED)),
                )
                links = [dict(r) for r in cur.fetchall()]
                expected = int(cfg["expected"])
                unique_products = len({str(r["id_product"]) for r in links})
                unique_prints = len({int(r["print_id"]) for r in links})
                method_counts = dict(Counter(str(r.get("mapping_method") or "") for r in links))
                wrong_language = [r for r in links if str(r.get("language") or "").casefold() != "ja"]
                wrong_set = [r for r in links if str(r.get("set_code") or "").upper() != cfg["set_code"]]
                stale = [r for r in links if catalog_capture is not None and r.get("last_seen_at") != catalog_capture]
                wrong_confidence = [r for r in links if str(r.get("confidence") or "") != "exact"]
                unreviewed = [r for r in links if not bool(r.get("reviewed"))]

                external_product_ids = [int(r["external_product_id"]) for r in links]
                print_ids = [int(r["print_id"]) for r in links]
                external_priceable: set[int] = set()
                if external_product_ids and price_as_of is not None:
                    cur.execute(
                        """SELECT external_product_id,price_low,price_mid,price_market,price_last
                           FROM external_market_price_snapshots
                           WHERE external_product_id=ANY(%s) AND currency='EUR' AND as_of=%s""",
                        (external_product_ids, price_as_of),
                    )
                    for row in cur.fetchall():
                        d = dict(row)
                        if _meaningful(d):
                            external_priceable.add(int(d["external_product_id"]))

                canonical_exact_priceable: set[int] = set()
                canonical_wrong_product: list[dict] = []
                if print_ids and price_as_of is not None:
                    cur.execute(
                        """SELECT ps.entity_id print_id,ps.price_low,ps.price_mid,ps.price_market,ps.price_last,ps.raw_json
                           FROM price_snapshots ps
                           JOIN price_sources src ON src.id=ps.source_id
                           WHERE src.name='cardmarket' AND ps.entity_type='print' AND ps.entity_id=ANY(%s)
                             AND ps.currency='EUR' AND ps.as_of=%s""",
                        (print_ids, price_as_of),
                    )
                    link_by_print = {int(r["print_id"]): str(r["id_product"]) for r in links}
                    for row in cur.fetchall():
                        d = dict(row)
                        print_id = int(d["print_id"])
                        source_id = str((d.get("raw_json") or {}).get("idProduct") or "")
                        expected_id = link_by_print.get(print_id)
                        if source_id and source_id != expected_id:
                            canonical_wrong_product.append(
                                {"print_id": print_id, "expected": expected_id, "actual": source_id}
                            )
                        elif source_id == expected_id and _meaningful(d):
                            canonical_exact_priceable.add(print_id)

                if len(links) != expected:
                    failures.append(f"{label}_accepted_expected_{expected}_got_{len(links)}")
                if unique_products != expected or unique_prints != expected:
                    failures.append(f"{label}_not_one_to_one_products_{unique_products}_prints_{unique_prints}")
                if method_counts != cfg["methods"]:
                    failures.append(f"{label}_method_counts_expected_{cfg['methods']}_got_{method_counts}")
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
                if canonical_wrong_product:
                    failures.append(f"{label}_canonical_wrong_idProduct_{len(canonical_wrong_product)}")
                if len(canonical_exact_priceable) != len(external_priceable):
                    failures.append(
                        f"{label}_canonical_priceable_{len(canonical_exact_priceable)}_does_not_match_external_{len(external_priceable)}"
                    )

                unpriced = [
                    {
                        "idProduct": str(r["id_product"]),
                        "print_id": int(r["print_id"]),
                        "collector_number": r["collector_number"],
                        "card_name": r["card_name"],
                        "variant": r["variant"],
                        "mapping_method": r["mapping_method"],
                    }
                    for r in links
                    if int(r["external_product_id"]) not in external_priceable
                ]
                report["surfaces"][label] = {
                    "idExpansion": cfg["idExpansion"],
                    "accepted_links": len(links),
                    "unique_products": unique_products,
                    "unique_prints": unique_prints,
                    "method_counts": method_counts,
                    "wrong_language": len(wrong_language),
                    "wrong_set": len(wrong_set),
                    "stale_products": len(stale),
                    "wrong_confidence": len(wrong_confidence),
                    "unreviewed": len(unreviewed),
                    "externally_priceable": len(external_priceable),
                    "canonical_exact_idProduct_priceable": len(canonical_exact_priceable),
                    "canonical_wrong_idProduct": len(canonical_wrong_product),
                    "unpriced": unpriced,
                }

            cur.execute(
                """SELECT count(*) n FROM external_catalog_print_links l
                   JOIN external_catalog_products e ON e.id=l.external_product_id
                   JOIN prints p ON p.id=l.print_id
                   JOIN sets s ON s.id=p.set_id
                   WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single'
                     AND e.expansion_external_id='5421' AND l.link_status=ANY(%s)
                     AND lower(coalesce(p.language,''))='ja' AND upper(coalesce(s.code,''))='AGOV'""",
                (game_id, list(ACCEPTED)),
            )
            agov = int(cur.fetchone()["n"])
            report["agov_preserved_accepted_links"] = agov
            if agov != AGOV_EXPECTED:
                failures.append(f"AGOV_expected_{AGOV_EXPECTED}_got_{agov}")
            conn.rollback()
    finally:
        conn.close()

    report["failures"] = failures
    report["status"] = "pass" if not failures else "fail"
    output = os.getenv(
        "YGO_ALIN_POTE_PRODUCTION_PROOF_OUTPUT",
        "/tmp/yugioh-alin-pote-production-proof-v2.json",
    )
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n"
    Path(output).write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if not failures else 2


if __name__ == "__main__":
    raise SystemExit(main())
