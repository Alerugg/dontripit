from __future__ import annotations

import difflib
import json
import os
import re
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

import psycopg2
from psycopg2.extras import RealDictCursor


GAME = "yugioh"
EXPANSION_ID = "5421"
SET_CODE = "AGOV"
ACCEPTED = ("accepted", "mapped", "exact")


def norm(value: str) -> str:
    value = unicodedata.normalize("NFKD", str(value or ""))
    value = "".join(ch for ch in value if not unicodedata.combining(ch)).casefold()
    return re.sub(r"[^a-z0-9]+", " ", value).strip()


def _connect():
    url = os.getenv("DATABASE_URL_UNPOOLED") or os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL_UNPOOLED or DATABASE_URL is required")
    conn = psycopg2.connect(url, connect_timeout=30, application_name="dontripit_ygo_agov_jp_name_residual_v1")
    conn.set_session(readonly=True, autocommit=False)
    return conn


def main() -> int:
    conn = _connect()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT id FROM games WHERE slug=%s LIMIT 1", (GAME,))
            game_id = int(cur.fetchone()["id"])
            cur.execute("SELECT max(last_seen_at) AS ts FROM external_catalog_products WHERE source='cardmarket'")
            capture = cur.fetchone()["ts"]

            cur.execute(
                """
                SELECT e.id AS external_product_id,e.external_id AS id_product,e.name,e.metacard_external_id,e.category_id
                FROM external_catalog_products e
                WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single'
                  AND e.expansion_external_id=%s AND e.last_seen_at=%s
                ORDER BY e.external_id::bigint
                """,
                (game_id, EXPANSION_ID, capture),
            )
            products = [dict(r) for r in cur.fetchall()]

            cur.execute(
                """
                SELECT p.id AS print_id,p.card_id,p.collector_number,p.rarity,p.variant,p.language,c.name AS card_name
                FROM prints p JOIN cards c ON c.id=p.card_id JOIN sets s ON s.id=p.set_id
                WHERE c.game_id=%s AND lower(coalesce(p.language,''))='ja' AND upper(coalesce(s.code,''))=%s
                ORDER BY p.id
                """,
                (game_id, SET_CODE),
            )
            prints = [dict(r) for r in cur.fetchall()]

            cur.execute(
                """
                SELECT l.external_product_id,l.print_id,e.external_id AS id_product
                FROM external_catalog_print_links l
                JOIN external_catalog_products e ON e.id=l.external_product_id
                WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single'
                  AND e.expansion_external_id=%s AND l.link_status=ANY(%s)
                """,
                (game_id, EXPANSION_ID, list(ACCEPTED)),
            )
            accepted = [dict(r) for r in cur.fetchall()]
            conn.rollback()
    finally:
        conn.close()

    claimed_products = {int(r["external_product_id"]) for r in accepted}
    claimed_prints = {int(r["print_id"]) for r in accepted}
    residual_products = [r for r in products if int(r["external_product_id"]) not in claimed_products]
    residual_prints = [r for r in prints if int(r["print_id"]) not in claimed_prints]

    card_names: dict[int, str] = {}
    card_prints = defaultdict(list)
    cards_by_norm = defaultdict(set)
    for row in residual_prints:
        card_id = int(row["card_id"])
        card_names[card_id] = str(row["card_name"])
        card_prints[card_id].append(row)
        cards_by_norm[norm(row["card_name"])].add(card_id)

    products_by_norm = defaultdict(list)
    for row in residual_products:
        products_by_norm[norm(row["name"])].append(row)

    all_card_norms = sorted(cards_by_norm)
    classification = Counter()
    exact_balanced_groups = []
    detail = []

    for product_name_norm, group in sorted(products_by_norm.items()):
        canonical_cards = sorted(cards_by_norm.get(product_name_norm, set()))
        group_entry = {
            "normalized_market_name": product_name_norm,
            "market_names": sorted({str(r["name"]) for r in group}),
            "idProducts": [str(r["id_product"]) for r in group],
            "idMetacards": sorted({str(r.get("metacard_external_id") or "") for r in group}),
            "product_count": len(group),
            "exact_canonical_card_ids": canonical_cards,
        }
        if len(canonical_cards) == 1:
            card_id = canonical_cards[0]
            candidate_prints = card_prints[card_id]
            group_entry["canonical_card_name"] = card_names[card_id]
            group_entry["canonical_prints"] = [
                {
                    "print_id": int(r["print_id"]),
                    "collector_number": r["collector_number"],
                    "variant": r["variant"],
                    "rarity": r["rarity"],
                }
                for r in candidate_prints
            ]
            group_entry["print_count"] = len(candidate_prints)
            if len(candidate_prints) == len(group):
                classification["exact_name_unique_card_balanced_surface"] += len(group)
                group_entry["classification"] = "exact_name_unique_card_balanced_surface"
                exact_balanced_groups.append(
                    {
                        "card_name": card_names[card_id],
                        "card_id": card_id,
                        "products": len(group),
                        "prints": len(candidate_prints),
                        "idProducts": [str(r["id_product"]) for r in group],
                    }
                )
            else:
                classification["exact_name_unique_card_unbalanced_surface"] += len(group)
                group_entry["classification"] = "exact_name_unique_card_unbalanced_surface"
        elif len(canonical_cards) > 1:
            classification["exact_name_multiple_canonical_cards"] += len(group)
            group_entry["classification"] = "exact_name_multiple_canonical_cards"
        else:
            classification["no_exact_canonical_name"] += len(group)
            group_entry["classification"] = "no_exact_canonical_name"
            matches = difflib.get_close_matches(product_name_norm, all_card_norms, n=5, cutoff=0.55)
            group_entry["close_canonical_names"] = [
                {
                    "normalized": candidate,
                    "ratio": round(difflib.SequenceMatcher(None, product_name_norm, candidate).ratio(), 4),
                    "cards": [
                        {"card_id": int(cid), "card_name": card_names[int(cid)]}
                        for cid in sorted(cards_by_norm[candidate])
                    ],
                }
                for candidate in matches
            ]
        detail.append(group_entry)

    report = {
        "status": "pass",
        "production_writes": 0,
        "cardmarket_capture": str(capture),
        "idExpansion": EXPANSION_ID,
        "canonical_set": SET_CODE,
        "accepted_before": len(accepted),
        "residual_products": len(residual_products),
        "residual_prints": len(residual_prints),
        "classification_by_product": dict(classification),
        "exact_name_balanced_groups": exact_balanced_groups,
        "exact_name_balanced_products": sum(g["products"] for g in exact_balanced_groups),
        "detail": detail,
    }
    output = os.getenv("YGO_AGOV_JP_NAME_RESIDUAL_OUTPUT", "/tmp/yugioh-agov-jp-name-residual-v1.json")
    Path(output).write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
