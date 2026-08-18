from __future__ import annotations

import argparse
import json
import os
import re
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

import psycopg2
from psycopg2.extras import RealDictCursor


ACCEPTED = ("accepted", "mapped", "exact")
SURFACES = {
    "alin_jp": {"idExpansion": "6025", "expansion_code": "ALIN-JP", "set_code": "ALIN"},
    "pote_jp": {"idExpansion": "5044", "expansion_code": "POTE-JP", "set_code": "POTE"},
}


def norm(value: str) -> str:
    value = unicodedata.normalize("NFKD", str(value or ""))
    value = "".join(ch for ch in value if not unicodedata.combining(ch)).casefold()
    return re.sub(r"[^a-z0-9]+", " ", value).strip()


def _connect():
    url = os.getenv("DATABASE_URL_UNPOOLED") or os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL_UNPOOLED or DATABASE_URL is required")
    conn = psycopg2.connect(url, connect_timeout=30, application_name="dontripit_ygo_ocg_surface_v1")
    conn.set_session(readonly=True, autocommit=False)
    return conn


def main() -> int:
    parser = argparse.ArgumentParser(description="READ ONLY audit a certified Yu-Gi-Oh OCG Cardmarket expansion surface")
    parser.add_argument("--key", required=True, choices=sorted(SURFACES))
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    cfg = SURFACES[args.key]

    conn = _connect()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT id FROM games WHERE slug='yugioh' LIMIT 1")
            game_id = int(cur.fetchone()["id"])
            cur.execute("SELECT max(last_seen_at) capture FROM external_catalog_products WHERE source='cardmarket'")
            capture = cur.fetchone()["capture"]

            cur.execute(
                """SELECT e.id external_product_id,e.external_id id_product,e.name,e.metacard_external_id,
                          e.expansion_external_id,e.last_seen_at
                   FROM external_catalog_products e
                   WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single'
                     AND e.expansion_external_id=%s AND e.last_seen_at=%s
                   ORDER BY e.external_id::bigint""",
                (game_id, cfg["idExpansion"], capture),
            )
            products = [dict(r) for r in cur.fetchall()]

            cur.execute(
                """SELECT p.id print_id,p.card_id,p.collector_number,p.rarity,p.variant,p.language,c.name card_name,
                          s.code set_code
                   FROM prints p JOIN cards c ON c.id=p.card_id JOIN sets s ON s.id=p.set_id
                   WHERE c.game_id=%s AND lower(coalesce(p.language,''))='ja'
                     AND upper(coalesce(s.code,''))=%s
                   ORDER BY p.id""",
                (game_id, cfg["set_code"]),
            )
            prints = [dict(r) for r in cur.fetchall()]

            cur.execute(
                """SELECT e.metacard_external_id,p.card_id,count(*) evidence_links
                   FROM external_catalog_print_links l
                   JOIN external_catalog_products e ON e.id=l.external_product_id
                   JOIN prints p ON p.id=l.print_id
                   WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single'
                     AND l.link_status=ANY(%s) AND e.metacard_external_id IS NOT NULL
                   GROUP BY e.metacard_external_id,p.card_id
                   ORDER BY e.metacard_external_id,p.card_id""",
                (game_id, list(ACCEPTED)),
            )
            meta_rows = [dict(r) for r in cur.fetchall()]

            cur.execute(
                """SELECT l.external_product_id,l.print_id,e.external_id id_product,e.expansion_external_id,
                          p.card_id,l.mapping_method
                   FROM external_catalog_print_links l
                   JOIN external_catalog_products e ON e.id=l.external_product_id
                   JOIN prints p ON p.id=l.print_id
                   WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single'
                     AND l.link_status=ANY(%s)""",
                (game_id, list(ACCEPTED)),
            )
            accepted = [dict(r) for r in cur.fetchall()]
            conn.rollback()
    finally:
        conn.close()

    meta_cards = defaultdict(set)
    meta_evidence = defaultdict(int)
    for row in meta_rows:
        meta = str(row["metacard_external_id"])
        card_id = int(row["card_id"])
        meta_cards[meta].add(card_id)
        meta_evidence[(meta, card_id)] += int(row["evidence_links"] or 0)

    products_by_meta = defaultdict(list)
    for row in products:
        if row.get("metacard_external_id") is not None:
            products_by_meta[str(row["metacard_external_id"])].append(row)

    prints_by_card = defaultdict(list)
    for row in prints:
        prints_by_card[int(row["card_id"])].append(row)

    claimed_products = {int(r["external_product_id"]) for r in accepted}
    claimed_prints = {int(r["print_id"]) for r in accepted}
    already_in_surface = [r for r in accepted if str(r.get("expansion_external_id") or "") == cfg["idExpansion"]]

    rejected = Counter()
    proposal = []
    for product in products:
        pid = int(product["external_product_id"])
        if pid in claimed_products:
            rejected["product_already_claimed"] += 1
            continue
        meta = str(product.get("metacard_external_id") or "")
        cards = meta_cards.get(meta, set())
        if len(cards) != 1:
            rejected["metacard_not_one_canonical_card"] += 1
            continue
        card_id = next(iter(cards))
        market_siblings = products_by_meta.get(meta, [])
        if len(market_siblings) != 1:
            rejected["regional_variant_ambiguity"] += 1
            continue
        canonical = [row for row in prints_by_card.get(card_id, []) if int(row["print_id"]) not in claimed_prints]
        if len(canonical) != 1:
            rejected["canonical_variant_ambiguity"] += 1
            continue
        print_row = canonical[0]
        if norm(product["name"]) != norm(print_row["card_name"]):
            rejected["name_mismatch"] += 1
            continue
        proposal.append(
            {
                "external_product_id": pid,
                "idProduct": str(product["id_product"]),
                "idMetacard": meta,
                "print_id": int(print_row["print_id"]),
                "card_id": card_id,
                "card_name": print_row["card_name"],
                "collector_number": print_row["collector_number"],
                "canonical_variant": print_row["variant"],
                "canonical_rarity": print_row["rarity"],
                "metacard_evidence_links": meta_evidence[(meta, card_id)],
            }
        )

    product_ids = [int(r["external_product_id"]) for r in proposal]
    print_ids = [int(r["print_id"]) for r in proposal]
    one_to_one = len(product_ids) == len(set(product_ids)) and len(print_ids) == len(set(print_ids))
    if not one_to_one:
        raise RuntimeError("proposal is not globally one-to-one")

    report = {
        "status": "pass",
        "production_writes": 0,
        "key": args.key,
        "cardmarket_capture": str(capture),
        "certified_region": cfg,
        "cardmarket_products": len(products),
        "canonical_ja_prints": len(prints),
        "already_accepted_surface_links": len(already_in_surface),
        "metacard_unique_exact_proposals": len(proposal),
        "rejected": dict(rejected),
        "one_to_one": one_to_one,
        "proposal": proposal,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n"
    args.report.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
