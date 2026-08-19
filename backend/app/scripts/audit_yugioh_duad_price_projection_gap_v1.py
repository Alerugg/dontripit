from __future__ import annotations

import json
import os
from collections import Counter
from decimal import Decimal
from pathlib import Path

import psycopg2
from psycopg2.extras import RealDictCursor

ACCEPTED = ("accepted", "mapped", "exact")


def positive(value) -> bool:
    try:
        return value is not None and Decimal(str(value)) > 0
    except Exception:
        return False


def meaningful(row: dict) -> bool:
    return any(positive(row.get(key)) for key in ("price_low", "price_mid", "price_market", "price_last"))


def predicted_variant(*, is_foil: bool, variant: str) -> str | None:
    normalized = str(variant or "").strip().lower()
    if "etched" in normalized or "glossy" in normalized:
        return None
    return "foil" if is_foil else "nonfoil"


def main() -> int:
    url = os.getenv("DATABASE_URL_UNPOOLED") or os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL_UNPOOLED or DATABASE_URL is required")
    conn = psycopg2.connect(url, connect_timeout=30, application_name="dontripit_duad_price_gap_v1")
    conn.set_session(readonly=True, autocommit=False)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT id FROM games WHERE slug='yugioh' LIMIT 1")
            game_id = int(cur.fetchone()["id"])
            cur.execute(
                """SELECT max(mp.as_of) as_of
                   FROM external_market_price_snapshots mp
                   JOIN external_catalog_products e ON e.id=mp.external_product_id
                   WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single'""",
                (game_id,),
            )
            as_of = cur.fetchone()["as_of"]
            cur.execute(
                """SELECT e.id external_product_id,e.external_id id_product,p.id print_id,p.is_foil,
                          p.variant,p.rarity,p.collector_number,c.name card_name,l.mapping_method
                   FROM external_catalog_print_links l
                   JOIN external_catalog_products e ON e.id=l.external_product_id
                   JOIN prints p ON p.id=l.print_id
                   JOIN cards c ON c.id=p.card_id
                   JOIN sets s ON s.id=p.set_id
                   WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single'
                     AND e.expansion_external_id='6129' AND l.link_status=ANY(%s)
                     AND lower(coalesce(p.language,''))='ja' AND upper(coalesce(s.code,''))='DUAD'
                   ORDER BY e.external_id::bigint""",
                (game_id, list(ACCEPTED)),
            )
            links = [dict(row) for row in cur.fetchall()]
            product_ids = [int(row["external_product_id"]) for row in links]
            cur.execute(
                """SELECT external_product_id,price_variant,price_low,price_mid,price_market,price_last
                   FROM external_market_price_snapshots
                   WHERE external_product_id=ANY(%s) AND currency='EUR' AND as_of=%s
                   ORDER BY external_product_id,price_variant""",
                (product_ids, as_of),
            )
            prices = [dict(row) for row in cur.fetchall()]
            conn.rollback()
    finally:
        conn.close()

    price_by_product: dict[int, list[dict]] = {}
    for row in prices:
        price_by_product.setdefault(int(row["external_product_id"]), []).append(row)

    rows = []
    for link in links:
        external_product_id = int(link["external_product_id"])
        expected = predicted_variant(is_foil=bool(link["is_foil"]), variant=str(link.get("variant") or ""))
        source_rows = price_by_product.get(external_product_id, [])
        meaningful_variants = sorted(str(row["price_variant"]) for row in source_rows if meaningful(row))
        expected_meaningful = any(
            str(row["price_variant"]) == expected and meaningful(row)
            for row in source_rows
        ) if expected is not None else False
        rows.append(
            {
                "idProduct": str(link["id_product"]),
                "print_id": int(link["print_id"]),
                "card_name": link["card_name"],
                "collector_number": link["collector_number"],
                "rarity": link["rarity"],
                "variant": link["variant"],
                "is_foil": bool(link["is_foil"]),
                "mapping_method": link["mapping_method"],
                "projector_expected_price_variant": expected,
                "meaningful_source_price_variants": meaningful_variants,
                "expected_variant_has_meaningful_price": expected_meaningful,
                "any_meaningful_source_price": bool(meaningful_variants),
            }
        )

    mismatch = [row for row in rows if row["any_meaningful_source_price"] and not row["expected_variant_has_meaningful_price"]]
    report = {
        "status": "pass",
        "production_writes": 0,
        "price_guide_as_of": str(as_of),
        "accepted_duad_links": len(rows),
        "print_is_foil_counts": dict(Counter(str(row["is_foil"]) for row in rows)),
        "projector_expected_variant_counts": dict(Counter(str(row["projector_expected_price_variant"]) for row in rows)),
        "source_meaningful_variant_counts": dict(
            Counter(variant for row in rows for variant in row["meaningful_source_price_variants"])
        ),
        "any_source_priceable": sum(1 for row in rows if row["any_meaningful_source_price"]),
        "projector_variant_priceable": sum(1 for row in rows if row["expected_variant_has_meaningful_price"]),
        "source_priceable_but_projector_variant_missing": len(mismatch),
        "mismatch_by_rarity": dict(Counter(str(row["rarity"]) for row in mismatch)),
        "mismatch_by_mapping_method": dict(Counter(str(row["mapping_method"]) for row in mismatch)),
        "mismatch_rows": mismatch,
    }
    out = Path(os.getenv("YGO_DUAD_PRICE_GAP_OUTPUT", "/tmp/yugioh-duad-price-projection-gap-v1.json"))
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n"
    out.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
