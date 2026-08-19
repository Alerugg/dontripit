from __future__ import annotations

import json
import os
from collections import Counter
from decimal import Decimal
from pathlib import Path

import psycopg2
from psycopg2.extras import RealDictCursor

GAME = "yugioh"
ACCEPTED = ("accepted", "mapped", "exact")
METHOD = "cardmarket_ocg_certified_unique_physical_v3"
EXPECTED_TOTAL = 738
SURFACES = {
    "5840": ("ROTA", 62),
    "5929": ("SUDA", 58),
    "5753": ("INFO", 61),
    "5608": ("LEDE", 62),
    "5533": ("PHNI", 62),
    "5326": ("DUNE", 60),
    "5242": ("CYAC", 63),
    "5166": ("PHHY", 61),
    "5107": ("DABL", 63),
    "4519": ("DIFO", 62),
    "4524": ("BACH", 62),
    "4528": ("BODE", 62),
}


def _connect():
    url = os.getenv("DATABASE_URL_UNPOOLED") or os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL_UNPOOLED or DATABASE_URL is required")
    conn = psycopg2.connect(url, connect_timeout=30, application_name="dontripit_ygo_ocg_certified_singletons_proof_v1")
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
            game_id = int(cur.fetchone()["id"])
            cur.execute("SELECT max(last_seen_at) ts FROM external_catalog_products WHERE source='cardmarket' AND game_id=%s", (game_id,))
            catalog_capture = cur.fetchone()["ts"]
            cur.execute(
                """SELECT max(mp.as_of) ts FROM external_market_price_snapshots mp
                   JOIN external_catalog_products e ON e.id=mp.external_product_id
                   WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single'""",
                (game_id,),
            )
            price_as_of = cur.fetchone()["ts"]

            cur.execute(
                """SELECT l.id link_id,l.mapping_method,l.confidence,l.link_status,l.reviewed,
                          e.id external_product_id,e.external_id id_product,e.name market_name,
                          e.metacard_external_id,e.expansion_external_id,e.last_seen_at,
                          p.id print_id,p.language,p.collector_number,p.variant,p.rarity,p.is_foil,
                          s.code set_code,c.name card_name
                   FROM external_catalog_print_links l
                   JOIN external_catalog_products e ON e.id=l.external_product_id
                   JOIN prints p ON p.id=l.print_id JOIN cards c ON c.id=p.card_id JOIN sets s ON s.id=p.set_id
                   WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single'
                     AND e.expansion_external_id=ANY(%s) AND l.link_status=ANY(%s)
                   ORDER BY e.expansion_external_id,e.external_id::bigint,p.id""",
                (game_id, list(SURFACES), list(ACCEPTED)),
            )
            links = [dict(r) for r in cur.fetchall()]
            product_ids = [int(r["external_product_id"]) for r in links]
            print_ids = [int(r["print_id"]) for r in links]

            external_prices: dict[tuple[int, str], dict] = {}
            if price_as_of is not None and product_ids:
                cur.execute(
                    """SELECT external_product_id,price_variant,as_of,price_low,price_mid,price_market,price_last,avg1,avg7,avg30
                       FROM external_market_price_snapshots
                       WHERE external_product_id=ANY(%s) AND currency='EUR' AND as_of=%s""",
                    (product_ids, price_as_of),
                )
                for row in cur.fetchall():
                    d = dict(row)
                    external_prices[(int(d["external_product_id"]), str(d["price_variant"]))] = d

            canonical_prices: dict[int, list[dict]] = {}
            if price_as_of is not None and print_ids:
                cur.execute(
                    """SELECT ps.entity_id print_id,ps.as_of,ps.price_low,ps.price_mid,ps.price_market,ps.price_last,ps.raw_json
                       FROM price_snapshots ps JOIN price_sources src ON src.id=ps.source_id
                       WHERE src.name='cardmarket' AND ps.entity_type='print' AND ps.entity_id=ANY(%s)
                         AND ps.currency='EUR' AND ps.as_of=%s ORDER BY ps.entity_id""",
                    (print_ids, price_as_of),
                )
                for row in cur.fetchall():
                    d = dict(row)
                    canonical_prices.setdefault(int(d["print_id"]), []).append(d)
            conn.rollback()
    finally:
        conn.close()

    failures = []
    per_set = {}
    expansion_hist = Counter(str(r.get("expansion_external_id") or "") for r in links)
    for expansion_id, (set_code, expected) in SURFACES.items():
        rows = [r for r in links if str(r.get("expansion_external_id") or "") == expansion_id]
        unique_products = len({int(r["external_product_id"]) for r in rows})
        unique_prints = len({int(r["print_id"]) for r in rows})
        wrong_language = sum(str(r.get("language") or "").lower() != "ja" for r in rows)
        wrong_set = sum(str(r.get("set_code") or "").upper() != set_code for r in rows)
        wrong_method = sum(str(r.get("mapping_method") or "") != METHOD for r in rows)
        wrong_confidence = sum(str(r.get("confidence") or "") != "exact" or not bool(r.get("reviewed")) for r in rows)
        stale = sum(catalog_capture is not None and r.get("last_seen_at") != catalog_capture for r in rows)
        if len(rows) != expected: failures.append(f"{set_code}_links_expected_{expected}_got_{len(rows)}")
        if unique_products != expected: failures.append(f"{set_code}_products_expected_{expected}_got_{unique_products}")
        if unique_prints != expected: failures.append(f"{set_code}_prints_expected_{expected}_got_{unique_prints}")
        if wrong_language: failures.append(f"{set_code}_wrong_language_{wrong_language}")
        if wrong_set: failures.append(f"{set_code}_wrong_set_{wrong_set}")
        if wrong_method: failures.append(f"{set_code}_wrong_method_{wrong_method}")
        if wrong_confidence: failures.append(f"{set_code}_wrong_confidence_{wrong_confidence}")
        if stale: failures.append(f"{set_code}_stale_products_{stale}")
        per_set[set_code] = {"idExpansion": expansion_id, "accepted_links": len(rows), "unique_products": unique_products, "unique_prints": unique_prints, "wrong_language": wrong_language, "wrong_set": wrong_set, "wrong_method": wrong_method, "stale_products": stale}

    if len(links) != EXPECTED_TOTAL: failures.append(f"total_links_expected_{EXPECTED_TOTAL}_got_{len(links)}")
    if len({int(r["external_product_id"]) for r in links}) != EXPECTED_TOTAL: failures.append("global_product_identity_not_one_to_one")
    if len({int(r["print_id"]) for r in links}) != EXPECTED_TOTAL: failures.append("global_print_identity_not_one_to_one")
    if set(expansion_hist) != set(SURFACES): failures.append("unexpected_or_missing_expansion_ids")

    externally_priceable = 0
    missing_external = 0
    unsupported_finish = 0
    canonical_exact_current = 0
    missing_canonical = 0
    canonical_wrong_product = 0
    nonmeaningful_canonical = 0
    unpriced_samples = []
    for link in links:
        variant = _price_variant(link)
        external = None if variant is None else external_prices.get((int(link["external_product_id"]), variant))
        if variant is None:
            unsupported_finish += 1
        elif external is None or not _meaningful(external):
            missing_external += 1
        else:
            externally_priceable += 1
        canon_rows = canonical_prices.get(int(link["print_id"]), [])
        exact = [r for r in canon_rows if str((r.get("raw_json") or {}).get("idProduct") or "") == str(link["id_product"])]
        mismatch = [r for r in canon_rows if str((r.get("raw_json") or {}).get("idProduct") or "") not in ("", str(link["id_product"]))]
        if mismatch:
            canonical_wrong_product += 1
        if not exact:
            missing_canonical += 1
        elif any(_meaningful(r) for r in exact):
            canonical_exact_current += 1
        else:
            nonmeaningful_canonical += 1
        if not exact or not any(_meaningful(r) for r in exact):
            if len(unpriced_samples) < 30:
                unpriced_samples.append({"set_code": link["set_code"], "print_id": int(link["print_id"]), "idProduct": str(link["id_product"]), "card_name": link["card_name"], "collector_number": link["collector_number"], "variant": link["variant"], "external_current_meaningful": bool(external and _meaningful(external)), "canonical_exact_idProduct": bool(exact)})

    if canonical_wrong_product: failures.append(f"canonical_price_wrong_idProduct_{canonical_wrong_product}")
    if canonical_exact_current != externally_priceable:
        failures.append(f"canonical_exact_current_{canonical_exact_current}_does_not_match_external_priceable_{externally_priceable}")

    report = {
        "status": "pass" if not failures else "fail",
        "production_writes": 0,
        "game": GAME,
        "mapping_method": METHOD,
        "catalog_capture": str(catalog_capture),
        "price_guide_as_of": str(price_as_of),
        "accepted_links": len(links),
        "unique_products": len({int(r["external_product_id"]) for r in links}),
        "unique_prints": len({int(r["print_id"]) for r in links}),
        "sets": per_set,
        "pricing": {
            "externally_priceable_links": externally_priceable,
            "missing_external_current_price": missing_external,
            "unsupported_finish": unsupported_finish,
            "canonical_current_exact_idProduct_prices": canonical_exact_current,
            "missing_canonical_current_price": missing_canonical,
            "nonmeaningful_canonical_current_price": nonmeaningful_canonical,
            "canonical_current_wrong_idProduct": canonical_wrong_product,
        },
        "failures": failures,
        "unpriced_samples": unpriced_samples,
    }
    return report, (0 if not failures else 2)


def main() -> int:
    report, code = run()
    output = Path(os.getenv("YGO_OCG_CERTIFIED_SINGLETONS_PROOF_OUTPUT", "/tmp/yugioh-ocg-certified-singletons-production-v1.json"))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True, default=str))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
