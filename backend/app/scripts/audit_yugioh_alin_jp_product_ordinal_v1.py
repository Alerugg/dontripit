from __future__ import annotations

import json
import os
from collections import Counter, defaultdict
from pathlib import Path

import psycopg2
from psycopg2.extras import RealDictCursor


GAME = "yugioh"
EXPANSION_ID = "6025"
SET_CODE = "ALIN"
LANGUAGE = "ja"
ACCEPTED = ("accepted", "mapped", "exact")


def main() -> int:
    url = os.getenv("DATABASE_URL_UNPOOLED") or os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL_UNPOOLED or DATABASE_URL is required")
    conn = psycopg2.connect(url, connect_timeout=30, application_name="dontripit_ygo_alin_jp_product_ordinal_v1")
    conn.set_session(readonly=True, autocommit=False)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT id FROM games WHERE slug=%s LIMIT 1", (GAME,))
            game_id = int(cur.fetchone()["id"])
            cur.execute("SELECT max(last_seen_at) capture FROM external_catalog_products WHERE source='cardmarket'")
            capture = cur.fetchone()["capture"]

            cur.execute(
                """SELECT e.id external_product_id,e.external_id id_product,e.name,e.metacard_external_id,
                          l.print_id,l.mapping_method,l.confidence,l.reviewed,p.rarity,p.variant,
                          p.collector_number,p.language,s.code set_code,p.card_id
                   FROM external_catalog_products e
                   LEFT JOIN external_catalog_print_links l
                     ON l.external_product_id=e.id AND l.link_status=ANY(%s)
                   LEFT JOIN prints p ON p.id=l.print_id
                   LEFT JOIN sets s ON s.id=p.set_id
                   WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single'
                     AND e.expansion_external_id=%s AND e.last_seen_at=%s
                   ORDER BY e.metacard_external_id,e.external_id::bigint""",
                (list(ACCEPTED), game_id, EXPANSION_ID, capture),
            )
            products = [dict(r) for r in cur.fetchall()]

            cur.execute(
                """SELECT p.id print_id,p.card_id,p.collector_number,p.rarity,p.variant,p.language,c.name card_name
                   FROM prints p JOIN cards c ON c.id=p.card_id JOIN sets s ON s.id=p.set_id
                   WHERE c.game_id=%s AND lower(coalesce(p.language,''))=%s AND upper(coalesce(s.code,''))=%s
                   ORDER BY p.id""",
                (game_id, LANGUAGE, SET_CODE),
            )
            canonical_prints = [dict(r) for r in cur.fetchall()]

            cur.execute(
                """SELECT l.external_product_id,l.print_id,e.external_id id_product
                   FROM external_catalog_print_links l
                   JOIN external_catalog_products e ON e.id=l.external_product_id
                   WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single'
                     AND l.link_status=ANY(%s)""",
                (game_id, list(ACCEPTED)),
            )
            accepted_global = [dict(r) for r in cur.fetchall()]
            conn.rollback()
    finally:
        conn.close()

    claimed_product_rows = {int(r["external_product_id"]) for r in accepted_global}
    claimed_print_ids = {int(r["print_id"]) for r in accepted_global}
    residual_prints = [r for r in canonical_prints if int(r["print_id"]) not in claimed_print_ids]
    residual_prints_by_name: dict[str, list[dict]] = defaultdict(list)
    for row in residual_prints:
        residual_prints_by_name[str(row["card_name"])].append(row)

    by_meta: dict[str, list[dict]] = defaultdict(list)
    for row in products:
        by_meta[str(row.get("metacard_external_id") or "")].append(row)

    fully_mapped_sequences = Counter()
    fully_mapped_multi = []
    residual_groups = []
    residual_products_total = 0
    balanced_residual_products = 0
    for meta, items in sorted(by_meta.items(), key=lambda kv: min(int(x["id_product"]) for x in kv[1])):
        items = sorted(items, key=lambda r: int(r["id_product"]))
        mapped = [r for r in items if r.get("print_id") is not None]
        if len(mapped) == len(items):
            sequence = tuple(str(r.get("rarity") or "").casefold() for r in items)
            fully_mapped_sequences[sequence] += 1
            if len(items) > 1:
                fully_mapped_multi.append(
                    {
                        "idMetacard": meta,
                        "name": items[0].get("name"),
                        "product_count": len(items),
                        "ordinal_sequence": list(sequence),
                        "products": [
                            {"ordinal": i + 1, "idProduct": str(r["id_product"]), "print_id": int(r["print_id"]), "rarity": r.get("rarity"), "collector_number": r.get("collector_number"), "mapping_method": r.get("mapping_method")}
                            for i, r in enumerate(items)
                        ],
                    }
                )
            continue

        residual_products = [r for r in items if int(r["external_product_id"]) not in claimed_product_rows]
        residual_products_total += len(residual_products)
        names = sorted({str(r.get("name") or "") for r in residual_products})
        candidate_prints = residual_prints_by_name.get(names[0], []) if len(names) == 1 else []
        balanced = len(residual_products) == len(candidate_prints) and len(residual_products) > 0
        if balanced:
            balanced_residual_products += len(residual_products)
        residual_groups.append(
            {
                "idMetacard": meta,
                "names": names,
                "total_products_in_metacard": len(items),
                "already_mapped_products": len(mapped),
                "residual_products": [str(r["id_product"]) for r in residual_products],
                "residual_product_count": len(residual_products),
                "candidate_residual_print_count": len(candidate_prints),
                "balanced": balanced,
                "candidate_prints": [
                    {"print_id": int(r["print_id"]), "collector_number": r.get("collector_number"), "rarity": r.get("rarity"), "variant": r.get("variant"), "card_name": r.get("card_name")}
                    for r in candidate_prints
                ],
            }
        )

    accepted_alin = sum(1 for r in products if r.get("print_id") is not None)
    balanced_groups = [g for g in residual_groups if g["balanced"]]
    unbalanced_groups = [g for g in residual_groups if not g["balanced"]]
    report = {
        "status": "pass",
        "production_writes": 0,
        "cardmarket_capture": str(capture),
        "surface": {
            "regional_products": len(products),
            "canonical_ja_prints": len(canonical_prints),
            "accepted_alin_links": accepted_alin,
            "residual_products": residual_products_total,
            "residual_canonical_prints": len(residual_prints),
            "balanced_residual_groups": len(balanced_groups),
            "balanced_residual_products": balanced_residual_products,
            "unbalanced_residual_groups": len(unbalanced_groups),
        },
        "fully_mapped_ordinal_sequences": {
            " | ".join(k): v for k, v in sorted(fully_mapped_sequences.items(), key=lambda kv: (-kv[1], kv[0]))
        },
        "fully_mapped_multi_version_controls": fully_mapped_multi,
        "residual_group_size_histogram": dict(sorted(Counter(g["residual_product_count"] for g in balanced_groups).items())),
        "balanced_residual_groups_detail": balanced_groups,
        "unbalanced_residual_groups_detail": unbalanced_groups,
    }
    out = os.getenv("YGO_ALIN_JP_PRODUCT_ORDINAL_OUTPUT", "/tmp/yugioh-alin-jp-product-ordinal-v1.json")
    text = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n"
    Path(out).write_text(text, encoding="utf-8")
    print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
