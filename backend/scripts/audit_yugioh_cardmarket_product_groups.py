#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from collections import Counter, defaultdict
from pathlib import Path

import psycopg2
from psycopg2.extras import RealDictCursor


def main() -> int:
    ap = argparse.ArgumentParser(description="Read-only YGO Cardmarket multi-product group diagnostic.")
    ap.add_argument("--report", type=Path, required=True)
    ap.add_argument("--sample-limit", type=int, default=100)
    args = ap.parse_args()
    url = os.getenv("DATABASE_URL_UNPOOLED") or os.getenv("DATABASE_URL")
    if not url:
        raise SystemExit("DATABASE_URL_UNPOOLED or DATABASE_URL is required")

    conn = psycopg2.connect(url)
    conn.autocommit = False
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT id FROM games WHERE slug='yugioh'")
            game_id = int(cur.fetchone()["id"])
            cur.execute(
                """
                SELECT ecp.id AS external_product_id, ecp.external_id, ecp.expansion_external_id,
                       ecp.name, ecp.metacard_external_id, ecp.category_id, ecp.category, ecp.date_added,
                       l.print_id, l.confidence, l.link_status, l.mapping_method,
                       p.collector_number, p.rarity, p.variant, p.language, s.code AS set_code
                FROM external_catalog_products ecp
                LEFT JOIN external_catalog_print_links l
                  ON l.external_product_id=ecp.id
                 AND l.confidence='exact' AND l.link_status IN ('accepted','mapped')
                LEFT JOIN prints p ON p.id=l.print_id
                LEFT JOIN sets s ON s.id=p.set_id
                WHERE ecp.source='cardmarket' AND ecp.game_id=%s AND ecp.product_group='single'
                ORDER BY ecp.expansion_external_id, lower(ecp.name), ecp.external_id, l.print_id
                """,
                (game_id,),
            )
            rows = [dict(r) for r in cur.fetchall()]

        groups: dict[tuple[str,str], list[dict]] = defaultdict(list)
        products_seen: dict[tuple[str,str], set[int]] = defaultdict(set)
        for row in rows:
            key = (str(row.get("expansion_external_id") or ""), str(row.get("name") or "").casefold())
            groups[key].append(row)
            products_seen[key].add(int(row["external_product_id"]))

        multi = {k:v for k,v in groups.items() if len(products_seen[k]) > 1}
        stats = Counter()
        metacard_patterns = Counter()
        rarity_patterns = Counter()
        samples = []
        for (expansion, name_key), group_rows in multi.items():
            product_map: dict[int, dict] = {}
            mapped_rarities = set()
            mapped_variants = set()
            metacards = set()
            for row in group_rows:
                pk = int(row["external_product_id"])
                entry = product_map.setdefault(pk, {
                    "external_product_id": pk,
                    "idProduct": str(row["external_id"]),
                    "idMetacard": row.get("metacard_external_id"),
                    "category_id": row.get("category_id"),
                    "category": row.get("category"),
                    "date_added": row.get("date_added").isoformat() if row.get("date_added") else None,
                    "exact_links": [],
                })
                if row.get("metacard_external_id"):
                    metacards.add(str(row["metacard_external_id"]))
                if row.get("print_id") is not None:
                    rarity = str(row.get("rarity") or "")
                    variant = str(row.get("variant") or "")
                    mapped_rarities.add(rarity)
                    mapped_variants.add(variant)
                    entry["exact_links"].append({
                        "print_id": int(row["print_id"]),
                        "collector_number": row.get("collector_number"),
                        "rarity": row.get("rarity"),
                        "variant": row.get("variant"),
                        "language": row.get("language"),
                        "set_code": row.get("set_code"),
                        "mapping_method": row.get("mapping_method"),
                    })
            product_count = len(product_map)
            mapped_products = sum(1 for p in product_map.values() if p["exact_links"])
            stats["multi_name_expansion_groups"] += 1
            stats["products_in_multi_groups"] += product_count
            stats["mapped_products_in_multi_groups"] += mapped_products
            stats["unmapped_products_in_multi_groups"] += product_count - mapped_products
            if len(metacards) == 1:
                metacard_patterns["one_metacard_for_group"] += 1
            elif len(metacards) == product_count:
                metacard_patterns["distinct_metacard_per_product"] += 1
            else:
                metacard_patterns["mixed_metacards"] += 1
            if len(mapped_rarities) > 1:
                rarity_patterns["multiple_mapped_rarities"] += 1
            elif len(mapped_rarities) == 1:
                rarity_patterns["single_mapped_rarity"] += 1
            else:
                rarity_patterns["no_mapped_rarity"] += 1
            if len(samples) < args.sample_limit and (product_count - mapped_products > 0):
                samples.append({
                    "expansion_external_id": expansion,
                    "name": group_rows[0].get("name"),
                    "product_count": product_count,
                    "mapped_products": mapped_products,
                    "metacard_count": len(metacards),
                    "mapped_rarities": sorted(mapped_rarities),
                    "mapped_variants": sorted(mapped_variants),
                    "products": sorted(product_map.values(), key=lambda x: int(x["idProduct"])),
                })

        payload = {
            "mode":"read_only",
            "game":"yugioh",
            "summary": dict(stats),
            "metacard_patterns": dict(metacard_patterns),
            "rarity_patterns": dict(rarity_patterns),
            "samples": samples,
        }
        args.report.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)+"\n", encoding="utf-8")
        print("YGO_PRODUCT_GROUP_DIAGNOSTIC=" + json.dumps({**payload["summary"], "metacard_patterns": payload["metacard_patterns"], "rarity_patterns": payload["rarity_patterns"]}, separators=(",",":")))
        conn.rollback()
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
