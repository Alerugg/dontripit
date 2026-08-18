from __future__ import annotations

import json
import os
from collections import Counter, defaultdict
from pathlib import Path

import psycopg2
from psycopg2.extras import RealDictCursor


GAME = "yugioh"
EXPANSION_ID = "5044"
EXPANSION_CODE = "POTE-JP"
SET_CODE = "POTE"
ACCEPTED = ("accepted", "mapped", "exact")


def _connect():
    url = os.getenv("DATABASE_URL_UNPOOLED") or os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL_UNPOOLED or DATABASE_URL is required")
    conn = psycopg2.connect(url, connect_timeout=30, application_name="dontripit_ygo_pote_jp_residual_groups_v1")
    conn.set_session(readonly=True, autocommit=False)
    return conn


def main() -> int:
    conn = _connect()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT id FROM games WHERE slug=%s LIMIT 1", (GAME,))
            game_id = int(cur.fetchone()["id"])
            cur.execute("SELECT max(last_seen_at) capture FROM external_catalog_products WHERE source='cardmarket'")
            capture = cur.fetchone()["capture"]

            cur.execute(
                """SELECT e.id external_product_id,e.external_id id_product,e.name,e.metacard_external_id,e.category_id
                   FROM external_catalog_products e
                   WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single'
                     AND e.expansion_external_id=%s AND e.last_seen_at=%s
                   ORDER BY e.external_id::bigint""",
                (game_id, EXPANSION_ID, capture),
            )
            products = [dict(r) for r in cur.fetchall()]

            cur.execute(
                """SELECT p.id print_id,p.card_id,p.collector_number,p.rarity,p.variant,p.language,p.is_foil,
                          c.name card_name,
                          EXISTS(SELECT 1 FROM print_images pi WHERE pi.print_id=p.id) has_image,
                          (SELECT pi.url FROM print_images pi WHERE pi.print_id=p.id ORDER BY pi.is_primary DESC,pi.id ASC LIMIT 1) image_url,
                          (SELECT pi.source FROM print_images pi WHERE pi.print_id=p.id ORDER BY pi.is_primary DESC,pi.id ASC LIMIT 1) image_source
                   FROM prints p JOIN cards c ON c.id=p.card_id JOIN sets s ON s.id=p.set_id
                   WHERE c.game_id=%s AND lower(coalesce(p.language,''))='ja' AND upper(coalesce(s.code,''))=%s
                   ORDER BY p.id""",
                (game_id, SET_CODE),
            )
            prints = [dict(r) for r in cur.fetchall()]

            cur.execute(
                """SELECT l.external_product_id,l.print_id,e.external_id id_product,e.metacard_external_id,
                          e.expansion_external_id,p.card_id
                   FROM external_catalog_print_links l
                   JOIN external_catalog_products e ON e.id=l.external_product_id
                   JOIN prints p ON p.id=l.print_id
                   WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single'
                     AND l.link_status=ANY(%s)""",
                (game_id, list(ACCEPTED)),
            )
            accepted = [dict(r) for r in cur.fetchall()]

            cur.execute(
                """SELECT e.metacard_external_id,p.card_id,count(*) evidence_links
                   FROM external_catalog_print_links l
                   JOIN external_catalog_products e ON e.id=l.external_product_id
                   JOIN prints p ON p.id=l.print_id
                   WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single'
                     AND l.link_status=ANY(%s) AND e.metacard_external_id IS NOT NULL
                   GROUP BY e.metacard_external_id,p.card_id""",
                (game_id, list(ACCEPTED)),
            )
            meta_rows = [dict(r) for r in cur.fetchall()]
            conn.rollback()
    finally:
        conn.close()

    claimed_products = {int(r["external_product_id"]) for r in accepted}
    claimed_prints = {int(r["print_id"]) for r in accepted}
    residual_products = [r for r in products if int(r["external_product_id"]) not in claimed_products]
    residual_prints = [r for r in prints if int(r["print_id"]) not in claimed_prints]

    meta_cards = defaultdict(set)
    meta_evidence = defaultdict(int)
    for row in meta_rows:
        meta = str(row["metacard_external_id"])
        card_id = int(row["card_id"])
        meta_cards[meta].add(card_id)
        meta_evidence[(meta, card_id)] += int(row["evidence_links"] or 0)

    products_by_meta = defaultdict(list)
    for row in residual_products:
        products_by_meta[str(row.get("metacard_external_id") or "")].append(row)
    prints_by_card = defaultdict(list)
    for row in residual_prints:
        prints_by_card[int(row["card_id"])].append(row)

    groups = []
    classification = Counter()
    seen_products = set()
    seen_prints = set()

    for meta, product_group in sorted(products_by_meta.items(), key=lambda kv: min(int(r["id_product"]) for r in kv[1])):
        cards = sorted(meta_cards.get(meta, set()))
        group = {
            "idMetacard": meta or None,
            "product_count": len(product_group),
            "products": [
                {"external_product_id": int(r["external_product_id"]), "idProduct": str(r["id_product"]), "name": r["name"]}
                for r in product_group
            ],
            "canonical_card_ids": cards,
        }
        seen_products.update(int(r["external_product_id"]) for r in product_group)
        if len(cards) != 1:
            classification["metacard_not_one_card"] += len(product_group)
            group["classification"] = "metacard_not_one_card"
            groups.append(group)
            continue
        card_id = cards[0]
        print_group = prints_by_card.get(card_id, [])
        seen_prints.update(int(r["print_id"]) for r in print_group)
        group["card_id"] = card_id
        group["card_name"] = print_group[0]["card_name"] if print_group else None
        group["metacard_evidence_links"] = meta_evidence[(meta, card_id)]
        group["print_count"] = len(print_group)
        group["prints"] = [
            {
                "print_id": int(r["print_id"]),
                "collector_number": r["collector_number"],
                "rarity": r["rarity"],
                "variant": r["variant"],
                "is_foil": bool(r["is_foil"]),
                "has_image": bool(r["has_image"]),
                "image_source": r["image_source"],
            }
            for r in print_group
        ]
        if len(product_group) == len(print_group) and len(product_group) >= 2:
            if all(bool(r["has_image"]) for r in print_group):
                classification["balanced_image_ready"] += len(product_group)
                group["classification"] = "balanced_image_ready"
            else:
                classification["balanced_missing_canonical_images"] += len(product_group)
                group["classification"] = "balanced_missing_canonical_images"
        elif len(product_group) == len(print_group) == 1:
            classification["singleton_residual"] += 1
            group["classification"] = "singleton_residual"
        else:
            classification["unbalanced_surface"] += len(product_group)
            group["classification"] = "unbalanced_surface"
        groups.append(group)

    ungrouped_prints = [
        {
            "print_id": int(r["print_id"]),
            "card_id": int(r["card_id"]),
            "card_name": r["card_name"],
            "collector_number": r["collector_number"],
            "variant": r["variant"],
            "rarity": r["rarity"],
        }
        for r in residual_prints if int(r["print_id"]) not in seen_prints
    ]

    size_hist = Counter()
    for group in groups:
        if group.get("classification") == "balanced_image_ready":
            size_hist[f"{group['product_count']}x{group['print_count']}"] += 1

    report = {
        "status": "pass",
        "production_writes": 0,
        "cardmarket_capture": str(capture),
        "certified_region": {"idExpansion": EXPANSION_ID, "expansion_code": EXPANSION_CODE, "canonical_set": SET_CODE},
        "regional_products_total": len(products),
        "canonical_ja_prints_total": len(prints),
        "accepted_surface_links": sum(1 for r in accepted if str(r.get("expansion_external_id") or "") == EXPANSION_ID),
        "residual_products": len(residual_products),
        "residual_prints": len(residual_prints),
        "classification_by_product": dict(classification),
        "balanced_image_ready_groups": sum(1 for g in groups if g.get("classification") == "balanced_image_ready"),
        "balanced_image_ready_products": sum(g["product_count"] for g in groups if g.get("classification") == "balanced_image_ready"),
        "balanced_group_size_histogram": dict(size_hist),
        "groups": groups,
        "ungrouped_prints": ungrouped_prints,
    }
    output = os.getenv("YGO_POTE_JP_RESIDUAL_GROUPS_OUTPUT", "/tmp/yugioh-pote-jp-residual-groups-v1.json")
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n"
    Path(output).write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
