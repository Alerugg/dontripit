from __future__ import annotations

import json
import os
import unicodedata
from collections import defaultdict
from pathlib import Path

import psycopg2
from psycopg2.extras import RealDictCursor

GAME = "yugioh"
LANGUAGE = "ja"
ACCEPTED = ("accepted", "mapped", "exact")
CERTIFIED = {
    "ROTA": "5840",
    "SUDA": "5929",
    "INFO": "5753",
    "LEDE": "5608",
    "PHNI": "5533",
    "DUNE": "5326",
    "CYAC": "5242",
    "PHHY": "5166",
    "DABL": "5107",
    "DIFO": "4519",
    "BACH": "4524",
    "BODE": "4528",
}


def _norm(value: str | None) -> str:
    text = unicodedata.normalize("NFKD", str(value or "")).casefold()
    return "".join(ch for ch in text if ch.isalnum())


def _connect():
    url = os.getenv("DATABASE_URL_UNPOOLED") or os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL_UNPOOLED or DATABASE_URL is required")
    conn = psycopg2.connect(url, connect_timeout=30, application_name="dontripit_ygo_ocg_certified_singletons_v1")
    conn.set_session(readonly=True, autocommit=False)
    return conn


def main() -> int:
    conn = _connect()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT id FROM games WHERE slug=%s LIMIT 1", (GAME,))
            game_id = int(cur.fetchone()["id"])
            cur.execute("SELECT max(last_seen_at) capture FROM external_catalog_products WHERE source='cardmarket' AND game_id=%s", (game_id,))
            capture = cur.fetchone()["capture"]
            if capture is None:
                raise RuntimeError("Cardmarket capture missing")

            cur.execute(
                """SELECT e.metacard_external_id,p.card_id,count(*) accepted_links
                   FROM external_catalog_print_links l
                   JOIN external_catalog_products e ON e.id=l.external_product_id
                   JOIN prints p ON p.id=l.print_id
                   WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single'
                     AND l.link_status=ANY(%s) AND e.metacard_external_id IS NOT NULL
                   GROUP BY e.metacard_external_id,p.card_id""",
                (game_id, list(ACCEPTED)),
            )
            meta_to_cards: dict[str, set[int]] = defaultdict(set)
            meta_evidence: dict[tuple[str, int], int] = defaultdict(int)
            for row in cur.fetchall():
                meta = str(row.get("metacard_external_id") or "")
                cid = int(row["card_id"])
                meta_to_cards[meta].add(cid)
                meta_evidence[(meta, cid)] += int(row["accepted_links"] or 0)

            cur.execute(
                """SELECT l.external_product_id,l.print_id,e.external_id id_product,p.language,s.code set_code
                   FROM external_catalog_print_links l
                   JOIN external_catalog_products e ON e.id=l.external_product_id
                   JOIN prints p ON p.id=l.print_id
                   JOIN sets s ON s.id=p.set_id
                   WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single'
                     AND l.link_status=ANY(%s)""",
                (game_id, list(ACCEPTED)),
            )
            existing_by_product: dict[int, list[dict]] = defaultdict(list)
            existing_by_print: dict[int, list[dict]] = defaultdict(list)
            for row in cur.fetchall():
                item = dict(row)
                existing_by_product[int(row["external_product_id"])].append(item)
                existing_by_print[int(row["print_id"])].append(item)

            cur.execute(
                """SELECT count(*) n FROM prints p JOIN cards c ON c.id=p.card_id
                   WHERE c.game_id=%s AND lower(coalesce(p.language,''))=%s""",
                (game_id, LANGUAGE),
            )
            ja_baseline = int(cur.fetchone()["n"])

            set_reports = []
            all_pairs = []
            for set_code, expansion_id in CERTIFIED.items():
                cur.execute(
                    """SELECT e.id external_product_id,e.external_id id_product,e.name,e.metacard_external_id
                       FROM external_catalog_products e
                       WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single'
                         AND e.expansion_external_id=%s AND e.last_seen_at=%s
                       ORDER BY e.metacard_external_id,e.external_id::bigint""",
                    (game_id, expansion_id, capture),
                )
                products = [dict(r) for r in cur.fetchall()]

                cur.execute(
                    """SELECT p.id print_id,p.card_id,p.collector_number,p.rarity,p.variant,c.name card_name,s.id set_id
                       FROM prints p JOIN cards c ON c.id=p.card_id JOIN sets s ON s.id=p.set_id
                       WHERE c.game_id=%s AND upper(coalesce(s.code,''))=%s
                         AND lower(coalesce(p.language,''))=%s
                       ORDER BY p.card_id,p.collector_number,p.id""",
                    (game_id, set_code, LANGUAGE),
                )
                canonical = [dict(r) for r in cur.fetchall()]
                if not products or not canonical:
                    raise RuntimeError({"missing_certified_surface": {"set": set_code, "idExpansion": expansion_id, "products": len(products), "ja": len(canonical)}})

                canonical_cards = {int(r["card_id"]) for r in canonical}
                prints_by_card: dict[int, list[dict]] = defaultdict(list)
                name_to_cards: dict[str, set[int]] = defaultdict(set)
                for row in canonical:
                    cid = int(row["card_id"])
                    prints_by_card[cid].append(row)
                    name_to_cards[_norm(row["card_name"])].add(cid)

                products_by_meta: dict[str, list[dict]] = defaultdict(list)
                for row in products:
                    products_by_meta[str(row.get("metacard_external_id") or "")].append(row)

                pairs = []
                stats = defaultdict(int)
                for meta, group in products_by_meta.items():
                    if not meta or len(group) != 1:
                        stats["non_singleton_product_groups"] += 1
                        continue
                    product = group[0]
                    global_cards = sorted(meta_to_cards.get(meta, set()))
                    name_cards = name_to_cards.get(_norm(product.get("name")), set())
                    card_id = None
                    method = None
                    if len(global_cards) == 1 and global_cards[0] in canonical_cards:
                        card_id = global_cards[0]
                        method = "accepted_global_metacard_to_certified_set_card"
                    elif len(global_cards) > 1:
                        inter = sorted(set(global_cards) & canonical_cards & set(name_cards))
                        if len(inter) == 1:
                            card_id = inter[0]
                            method = "ambiguous_metacard_resolved_by_certified_set_and_name"
                    if card_id is None:
                        stats["unresolved_metacards"] += 1
                        continue
                    card_prints = prints_by_card.get(int(card_id), [])
                    if len(card_prints) != 1:
                        stats["multi_physical_card_groups"] += 1
                        continue
                    print_row = card_prints[0]
                    if _norm(product.get("name")) != _norm(print_row.get("card_name")):
                        stats["name_mismatch"] += 1
                        continue

                    external_product_id = int(product["external_product_id"])
                    print_id = int(print_row["print_id"])
                    product_claims = existing_by_product.get(external_product_id, [])
                    print_claims = existing_by_print.get(print_id, [])
                    same = [r for r in product_claims if int(r["print_id"]) == print_id]
                    product_conflicts = [r for r in product_claims if int(r["print_id"]) != print_id]
                    print_conflicts = [r for r in print_claims if int(r["external_product_id"]) != external_product_id]
                    if product_conflicts or print_conflicts:
                        stats["accepted_claim_conflicts"] += 1
                        continue
                    if len(same) > 1:
                        raise RuntimeError({"duplicate_same_pair": {"set": set_code, "idProduct": product["id_product"], "print_id": print_id}})
                    pair = {
                        "set_code": set_code,
                        "idExpansion": expansion_id,
                        "external_product_id": external_product_id,
                        "idProduct": str(product["id_product"]),
                        "idMetacard": meta,
                        "print_id": print_id,
                        "card_id": int(card_id),
                        "card_name": str(print_row["card_name"]),
                        "collector_number": str(print_row["collector_number"]),
                        "canonical_rarity": print_row.get("rarity"),
                        "canonical_variant": print_row.get("variant"),
                        "resolution_method": method,
                        "metacard_evidence_links": int(meta_evidence.get((meta, int(card_id)), 0)),
                        "already_accepted_same_pair": bool(same),
                    }
                    pairs.append(pair)

                if len({r["external_product_id"] for r in pairs}) != len(pairs) or len({r["print_id"] for r in pairs}) != len(pairs):
                    raise RuntimeError({"singleton_not_one_to_one": set_code})
                all_pairs.extend(pairs)
                set_reports.append({
                    "set_code": set_code,
                    "idExpansion": expansion_id,
                    "products": len(products),
                    "unique_metacards": len(products_by_meta),
                    "canonical_ja_prints": len(canonical),
                    "canonical_cards": len(canonical_cards),
                    "certified_singleton_pairs": len(pairs),
                    "already_accepted_same_pair": sum(bool(r["already_accepted_same_pair"]) for r in pairs),
                    "new_links_ready": sum(not r["already_accepted_same_pair"] for r in pairs),
                    "blocked_breakdown": dict(sorted(stats.items())),
                    "pairs": pairs,
                })
            conn.rollback()
    finally:
        conn.close()

    payload = {
        "status": "pass",
        "mode": "read_only",
        "production_writes": 0,
        "game": GAME,
        "language": LANGUAGE,
        "certified_expansions": CERTIFIED,
        "cardmarket_capture": str(capture),
        "ja_baseline": ja_baseline,
        "sets": set_reports,
        "total_certified_singleton_pairs": len(all_pairs),
        "total_already_accepted_same_pair": sum(bool(r["already_accepted_same_pair"]) for r in all_pairs),
        "total_new_links_ready": sum(not r["already_accepted_same_pair"] for r in all_pairs),
    }
    output = Path(os.getenv("YGO_OCG_CERTIFIED_SINGLETONS_OUTPUT", "/tmp/yugioh-ocg-certified-singletons-v1.json"))
    output.parent.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n"
    output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
