from __future__ import annotations

import json
import math
import os
from collections import Counter, defaultdict
from pathlib import Path

import psycopg2
from psycopg2.extras import RealDictCursor

from app.scripts.audit_cardmarket_regional_expansion_identity_v1 import _download_singles

GAME = "yugioh"
LANGUAGE = "ja"
ACCEPTED = ("accepted", "mapped", "exact")
KNOWN_CERTIFIED = {
    "5421": "AGOV",
    "5044": "POTE",
    "6025": "ALIN",
    "6129": "DUAD",
}


def _connect():
    url = os.getenv("DATABASE_URL_UNPOOLED") or os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL_UNPOOLED or DATABASE_URL is required")
    conn = psycopg2.connect(
        url,
        connect_timeout=30,
        application_name="dontripit_yugioh_ocg_global_surface_v1",
    )
    conn.set_session(readonly=True, autocommit=False)
    return conn


def _score(*, resolved: int, votes: int, set_cards: int, products: int, set_prints: int) -> float:
    if not resolved or not set_cards or not products or not set_prints:
        return 0.0
    expansion_coverage = votes / resolved
    set_card_coverage = votes / set_cards
    count_ratio = min(products, set_prints) / max(products, set_prints)
    return round((0.55 * expansion_coverage) + (0.30 * set_card_coverage) + (0.15 * count_ratio), 6)


def main() -> int:
    official_rows = _download_singles(GAME)
    feed_by_expansion: dict[str, list] = defaultdict(list)
    for row in official_rows:
        expansion_id = str(row.expansion_id or "").strip()
        if expansion_id:
            feed_by_expansion[expansion_id].append(row)

    conn = _connect()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT id FROM games WHERE slug=%s LIMIT 1", (GAME,))
            game_id = int(cur.fetchone()["id"])
            cur.execute(
                "SELECT max(last_seen_at) capture FROM external_catalog_products WHERE source='cardmarket' AND game_id=%s",
                (game_id,),
            )
            capture = cur.fetchone()["capture"]

            cur.execute(
                """SELECT e.metacard_external_id,
                          array_agg(DISTINCT p.card_id ORDER BY p.card_id) card_ids
                   FROM external_catalog_print_links l
                   JOIN external_catalog_products e ON e.id=l.external_product_id
                   JOIN prints p ON p.id=l.print_id
                   WHERE e.source='cardmarket' AND e.game_id=%s
                     AND e.metacard_external_id IS NOT NULL
                     AND l.link_status=ANY(%s)
                   GROUP BY e.metacard_external_id""",
                (game_id, list(ACCEPTED)),
            )
            metacard_to_card: dict[str, int] = {}
            ambiguous_metacards = 0
            for row in cur.fetchall():
                ids = [int(v) for v in (row.get("card_ids") or [])]
                if len(ids) == 1:
                    metacard_to_card[str(row["metacard_external_id"])] = ids[0]
                else:
                    ambiguous_metacards += 1

            cur.execute(
                """SELECT s.id set_id,s.code,s.name,s.region,
                          count(*) print_count,count(DISTINCT p.card_id) card_count
                   FROM prints p
                   JOIN cards c ON c.id=p.card_id
                   JOIN sets s ON s.id=p.set_id
                   WHERE c.game_id=%s AND lower(coalesce(p.language,''))=%s
                   GROUP BY s.id,s.code,s.name,s.region
                   ORDER BY s.code,s.region,s.id""",
                (game_id, LANGUAGE),
            )
            set_stats = {
                int(r["set_id"]): {
                    "set_id": int(r["set_id"]),
                    "code": str(r.get("code") or ""),
                    "name": str(r.get("name") or ""),
                    "region": str(r.get("region") or ""),
                    "print_count": int(r["print_count"]),
                    "card_count": int(r["card_count"]),
                }
                for r in cur.fetchall()
            }

            cur.execute(
                """SELECT DISTINCT p.card_id,s.id set_id
                   FROM prints p
                   JOIN cards c ON c.id=p.card_id
                   JOIN sets s ON s.id=p.set_id
                   WHERE c.game_id=%s AND lower(coalesce(p.language,''))=%s""",
                (game_id, LANGUAGE),
            )
            card_to_sets: dict[int, set[int]] = defaultdict(set)
            for row in cur.fetchall():
                card_to_sets[int(row["card_id"])].add(int(row["set_id"]))

            cur.execute(
                """SELECT e.expansion_external_id,count(*) accepted_links,
                          count(DISTINCT e.external_id) unique_products,
                          count(DISTINCT l.print_id) unique_prints,
                          count(*) FILTER (WHERE lower(coalesce(p.language,''))='ja') ja_links,
                          array_agg(DISTINCT s.code) FILTER (WHERE lower(coalesce(p.language,''))='ja') ja_set_codes
                   FROM external_catalog_print_links l
                   JOIN external_catalog_products e ON e.id=l.external_product_id
                   JOIN prints p ON p.id=l.print_id
                   JOIN sets s ON s.id=p.set_id
                   WHERE e.source='cardmarket' AND e.game_id=%s
                     AND l.link_status=ANY(%s)
                     AND e.expansion_external_id IS NOT NULL
                   GROUP BY e.expansion_external_id""",
                (game_id, list(ACCEPTED)),
            )
            accepted_by_expansion = {str(r["expansion_external_id"]): dict(r) for r in cur.fetchall()}

            cur.execute(
                """SELECT count(*) n FROM external_catalog_products
                   WHERE source='cardmarket' AND game_id=%s AND product_group='single' AND last_seen_at=%s""",
                (game_id, capture),
            )
            current_db_products = int(cur.fetchone()["n"])
            conn.rollback()
    finally:
        conn.close()

    expansion_reports = []
    strong_candidates = []
    for expansion_id, products in feed_by_expansion.items():
        resolved_by_meta: dict[str, int] = {}
        unique_feed_metacards = {
            str(r.metacard_id or "").strip()
            for r in products
            if str(r.metacard_id or "").strip()
        }
        for row in products:
            meta = str(row.metacard_id or "").strip()
            card_id = metacard_to_card.get(meta)
            if meta and card_id is not None:
                resolved_by_meta[meta] = card_id

        votes = Counter()
        for card_id in set(resolved_by_meta.values()):
            for set_id in card_to_sets.get(card_id, set()):
                votes[set_id] += 1

        ranked = []
        for set_id, vote_count in votes.most_common(8):
            st = set_stats[set_id]
            score = _score(
                resolved=len(resolved_by_meta),
                votes=vote_count,
                set_cards=st["card_count"],
                products=len(products),
                set_prints=st["print_count"],
            )
            ranked.append(
                {
                    **st,
                    "resolved_metacard_votes": vote_count,
                    "expansion_resolved_coverage": round(vote_count / max(1, len(resolved_by_meta)), 6),
                    "set_card_coverage": round(vote_count / max(1, st["card_count"]), 6),
                    "product_print_ratio": round(min(len(products), st["print_count"]) / max(len(products), st["print_count"]), 6),
                    "score": score,
                }
            )

        best = ranked[0] if ranked else None
        accepted = accepted_by_expansion.get(expansion_id, {})
        item = {
            "idExpansion": expansion_id,
            "products": len(products),
            "unique_metacards": len(unique_feed_metacards),
            "resolved_unique_metacards": len(resolved_by_meta),
            "resolved_fraction": round(len(resolved_by_meta) / max(1, len(unique_feed_metacards)), 6),
            "accepted_links": int(accepted.get("accepted_links") or 0),
            "accepted_ja_links": int(accepted.get("ja_links") or 0),
            "accepted_ja_set_codes": sorted(str(v) for v in (accepted.get("ja_set_codes") or [])),
            "known_certified_set": KNOWN_CERTIFIED.get(expansion_id),
            "best_candidate": best,
            "top_candidates": ranked,
        }
        expansion_reports.append(item)

        if best:
            strong = (
                len(products) >= 20
                and len(resolved_by_meta) >= 10
                and best["expansion_resolved_coverage"] >= 0.60
                and best["set_card_coverage"] >= 0.50
                and best["product_print_ratio"] >= 0.55
                and best["score"] >= 0.68
            )
            if strong:
                strong_candidates.append(item)

    strong_candidates.sort(
        key=lambda r: (
            1 if r.get("known_certified_set") else 0,
            -(r.get("best_candidate") or {}).get("score", 0),
            -r.get("resolved_unique_metacards", 0),
            int(r["idExpansion"]) if str(r["idExpansion"]).isdigit() else math.inf,
        )
    )
    expansion_reports.sort(
        key=lambda r: int(r["idExpansion"]) if str(r["idExpansion"]).isdigit() else math.inf
    )

    payload = {
        "status": "pass",
        "production_writes": 0,
        "source": "cardmarket_public_product_catalog_plus_neon_exact_metacard_bridge",
        "official_feed_products": len(official_rows),
        "neon_current_capture": str(capture),
        "neon_current_single_products": current_db_products,
        "feed_vs_neon_count_delta": len(official_rows) - current_db_products,
        "unambiguous_metacard_card_bridges": len(metacard_to_card),
        "ambiguous_accepted_metacards": ambiguous_metacards,
        "ja_sets": len(set_stats),
        "ja_physical_prints": sum(v["print_count"] for v in set_stats.values()),
        "cardmarket_expansions": len(feed_by_expansion),
        "known_certified": KNOWN_CERTIFIED,
        "strong_candidate_count": len(strong_candidates),
        "strong_candidates": strong_candidates,
        "all_expansions": expansion_reports,
    }
    output = Path(os.getenv("YGO_OCG_GLOBAL_SURFACE_OUTPUT", "/tmp/yugioh-ocg-global-surface-v1.json"))
    output.parent.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n"
    output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
