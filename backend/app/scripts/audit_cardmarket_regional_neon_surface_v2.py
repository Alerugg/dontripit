from __future__ import annotations

import json
import os
import re
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

import psycopg2
from psycopg2.extras import RealDictCursor


ACCEPTED = ("accepted", "mapped", "exact")
CERTIFIED_EXPANSIONS = {
    "yugioh": {"5421": {"code": "AGOV-JP", "region": "ocg_japan"}},
    "onepiece": {"6606": {"code": "OP16-JP", "region": "asia_region_legal"}},
}


def norm(value: str) -> str:
    value = unicodedata.normalize("NFKD", str(value or ""))
    value = "".join(ch for ch in value if not unicodedata.combining(ch)).casefold()
    return re.sub(r"[^a-z0-9]+", " ", value).strip()


def market_base_name(value: str) -> str:
    raw = str(value or "").strip()
    raw = re.sub(r"\s*\(V\.\d+[^)]*\)\s*$", "", raw, flags=re.I)
    return norm(raw)


def _connect():
    url = os.getenv("DATABASE_URL_UNPOOLED") or os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL_UNPOOLED or DATABASE_URL is required")
    conn = psycopg2.connect(
        url,
        connect_timeout=30,
        application_name="dontripit_cardmarket_regional_neon_surface_v2_readonly",
    )
    conn.set_session(readonly=True, autocommit=False)
    return conn


def _game_id(cur, slug: str) -> int:
    cur.execute("SELECT id FROM games WHERE slug=%s LIMIT 1", (slug,))
    row = cur.fetchone()
    if not row:
        raise RuntimeError(f"Missing game {slug}")
    return int(row["id"])


def _current_capture(cur):
    cur.execute("SELECT max(last_seen_at) AS capture FROM external_catalog_products WHERE source='cardmarket'")
    return cur.fetchone()["capture"]


def _capture_counts(cur, capture):
    cur.execute(
        """
        SELECT g.slug,e.product_group,count(*) AS products
        FROM external_catalog_products e JOIN games g ON g.id=e.game_id
        WHERE e.source='cardmarket' AND e.last_seen_at=%s
        GROUP BY g.slug,e.product_group ORDER BY g.slug,e.product_group
        """,
        (capture,),
    )
    return [dict(row) for row in cur.fetchall()]


def _regional_products(cur, game_id: int, expansion_id: str, capture):
    cur.execute(
        """
        SELECT e.id AS market_row_id,e.external_id,e.name,e.category,e.website_path,
               e.metacard_external_id,e.expansion_external_id,e.raw_json,e.last_seen_at
        FROM external_catalog_products e
        WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single'
          AND e.last_seen_at=%s AND e.expansion_external_id=%s
        ORDER BY e.id
        """,
        (game_id, capture, expansion_id),
    )
    return [dict(row) for row in cur.fetchall()]


def _ygo_agov_prints(cur, game_id: int):
    cur.execute(
        """
        SELECT p.id AS print_id,p.card_id,p.language,p.collector_number,p.rarity,p.variant,p.is_foil,p.print_key,
               c.name AS card_name,s.id AS set_id,s.code AS set_code,s.name AS set_name,s.region AS set_region
        FROM prints p JOIN cards c ON c.id=p.card_id JOIN sets s ON s.id=p.set_id
        WHERE c.game_id=%s AND lower(coalesce(p.language,''))='ja'
          AND (lower(coalesce(s.code,'')) LIKE '%%agov%%' OR lower(coalesce(s.name,'')) LIKE '%%age of overlord%%')
        ORDER BY p.id
        """,
        (game_id,),
    )
    return [dict(row) for row in cur.fetchall()]


def _links_for_products(cur, product_row_ids: list[int]):
    if not product_row_ids:
        return []
    cur.execute(
        """
        SELECT l.id AS link_id,l.external_product_id,l.print_id,l.mapping_method,l.confidence,l.link_status,l.reviewed,
               p.language,p.collector_number,p.rarity,p.variant,c.name AS card_name,s.code AS set_code,s.name AS set_name
        FROM external_catalog_print_links l
        JOIN prints p ON p.id=l.print_id JOIN cards c ON c.id=p.card_id JOIN sets s ON s.id=p.set_id
        WHERE l.external_product_id=ANY(%s)
        ORDER BY l.external_product_id,l.id
        """,
        (product_row_ids,),
    )
    return [dict(row) for row in cur.fetchall()]


def _name_bijection(products: list[dict], prints: list[dict]):
    products_by_name = defaultdict(list)
    prints_by_name = defaultdict(list)
    for row in products:
        products_by_name[market_base_name(row["name"])].append(row)
    for row in prints:
        prints_by_name[norm(row["card_name"])].append(row)

    hist = Counter()
    samples = []
    unique_pairs = []
    for product in products:
        key = market_base_name(product["name"])
        candidates = prints_by_name.get(key, [])
        hist[len(candidates)] += 1
        if len(candidates) == 1:
            unique_pairs.append(
                {
                    "idProduct": str(product["external_id"]),
                    "market_name": product["name"],
                    "idExpansion": str(product["expansion_external_id"]),
                    "print_id": int(candidates[0]["print_id"]),
                    "card_name": candidates[0]["card_name"],
                    "collector_number": candidates[0]["collector_number"],
                    "rarity": candidates[0]["rarity"],
                    "variant": candidates[0]["variant"],
                }
            )
        elif candidates and len(samples) < 60:
            samples.append(
                {
                    "idProduct": str(product["external_id"]),
                    "market_name": product["name"],
                    "candidate_count": len(candidates),
                    "candidate_prints": [
                        {
                            "print_id": int(candidate["print_id"]),
                            "collector_number": candidate["collector_number"],
                            "rarity": candidate["rarity"],
                            "variant": candidate["variant"],
                        }
                        for candidate in candidates[:10]
                    ],
                }
            )
    return {
        "product_candidate_count_histogram": {str(k): v for k, v in sorted(hist.items())},
        "unique_name_pairs": unique_pairs,
        "ambiguous_samples": samples,
        "canonical_name_multiplicity": {
            key: len(rows) for key, rows in sorted(prints_by_name.items()) if len(rows) > 1
        },
        "market_name_multiplicity": {
            key: len(rows) for key, rows in sorted(products_by_name.items()) if len(rows) > 1
        },
    }


def _onepiece_physical_surface(cur, game_id: int):
    cur.execute(
        """
        SELECT lower(coalesce(p.language,'')) AS language,count(*) AS prints,
               count(*) FILTER (WHERE upper(coalesce(p.collector_number,'')) LIKE 'OP16-%%') AS op16_prints
        FROM prints p JOIN cards c ON c.id=p.card_id
        WHERE c.game_id=%s GROUP BY lower(coalesce(p.language,'')) ORDER BY 1
        """,
        (game_id,),
    )
    return [dict(row) for row in cur.fetchall()]


def main() -> int:
    conn = _connect()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            capture = _current_capture(cur)
            if capture is None:
                raise RuntimeError("No Cardmarket capture in Neon")
            capture_counts = _capture_counts(cur, capture)

            ygo_id = _game_id(cur, "yugioh")
            op_id = _game_id(cur, "onepiece")
            ygo_products = _regional_products(cur, ygo_id, "5421", capture)
            op_products = _regional_products(cur, op_id, "6606", capture)
            ygo_prints = _ygo_agov_prints(cur, ygo_id)
            links = _links_for_products(cur, [int(row["market_row_id"]) for row in ygo_products + op_products])
            onepiece_surface = _onepiece_physical_surface(cur, op_id)
            conn.rollback()
    finally:
        conn.close()

    accepted_links = [row for row in links if row["link_status"] in ACCEPTED]
    report = {
        "status": "pass",
        "production_writes": 0,
        "cardmarket_current_capture": str(capture),
        "current_capture_counts": capture_counts,
        "certified_expansions": CERTIFIED_EXPANSIONS,
        "yugioh_agov_jp": {
            "idExpansion": "5421",
            "current_products": len(ygo_products),
            "products_with_metacard": sum(row.get("metacard_external_id") is not None for row in ygo_products),
            "canonical_ja_agov_prints": len(ygo_prints),
            "accepted_existing_links_from_expansion": sum(
                row["link_status"] in ACCEPTED and str(row.get("language") or "").lower() == "ja" for row in links
                if int(row["external_product_id"]) in {int(product["market_row_id"]) for product in ygo_products}
            ),
            "name_bijection_diagnostic": _name_bijection(ygo_products, ygo_prints),
            "product_samples": ygo_products[:40],
            "canonical_print_samples": ygo_prints[:40],
        },
        "onepiece_op16_jp": {
            "idExpansion": "6606",
            "current_products": len(op_products),
            "products_with_metacard": sum(row.get("metacard_external_id") is not None for row in op_products),
            "canonical_physical_surface": onepiece_surface,
            "accepted_existing_links_from_expansion": sum(
                row["link_status"] in ACCEPTED for row in links
                if int(row["external_product_id"]) in {int(product["market_row_id"]) for product in op_products}
            ),
            "product_samples": op_products[:40],
        },
        "regional_link_samples": accepted_links[:80],
    }
    output = Path(os.getenv("CARDMARKET_REGIONAL_NEON_SURFACE_OUTPUT", "/tmp/cardmarket-regional-neon-surface-v2.json"))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    print(output.read_text(encoding="utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
