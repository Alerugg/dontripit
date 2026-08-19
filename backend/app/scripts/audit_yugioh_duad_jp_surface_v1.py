from __future__ import annotations

import json
import os
from collections import Counter
from pathlib import Path

import psycopg2
from psycopg2.extras import RealDictCursor

GAME = "yugioh"
SET_CODE = "DUAD"
LANGUAGE = "ja"
ANCHORS = (
    "First of the Dragonlords",
    "Number F0: Utopic Future Zexal",
    "Super Quantum Black Layer",
    "Lunalight Liger Dancer",
)
ACCEPTED = ("accepted", "mapped", "exact")


def main() -> int:
    url = os.getenv("DATABASE_URL_UNPOOLED") or os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL_UNPOOLED or DATABASE_URL is required")
    conn = psycopg2.connect(
        url,
        connect_timeout=30,
        application_name="dontripit_ygo_duad_jp_surface_v1",
    )
    conn.set_session(readonly=True, autocommit=False)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT id FROM games WHERE slug=%s LIMIT 1", (GAME,))
            game_id = int(cur.fetchone()["id"])
            cur.execute("SELECT max(last_seen_at) capture FROM external_catalog_products WHERE source='cardmarket'")
            capture = cur.fetchone()["capture"]

            cur.execute(
                """SELECT p.id print_id,p.card_id,p.collector_number,p.rarity,p.variant,p.is_foil,
                          p.language,c.name card_name,s.code set_code
                   FROM prints p
                   JOIN cards c ON c.id=p.card_id
                   JOIN sets s ON s.id=p.set_id
                   WHERE c.game_id=%s AND upper(coalesce(s.code,''))=%s
                     AND lower(coalesce(p.language,''))=%s
                   ORDER BY p.collector_number,p.id""",
                (game_id, SET_CODE, LANGUAGE),
            )
            canonical = [dict(r) for r in cur.fetchall()]

            cur.execute(
                """SELECT e.id external_product_id,e.external_id id_product,e.name,e.metacard_external_id,
                          e.expansion_external_id,e.website_path,e.last_seen_at
                   FROM external_catalog_products e
                   WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single'
                     AND e.last_seen_at=%s AND lower(e.name)=ANY(%s)
                   ORDER BY e.expansion_external_id,e.external_id::bigint""",
                (game_id, capture, [a.casefold() for a in ANCHORS]),
            )
            anchor_rows = [dict(r) for r in cur.fetchall()]

            expansion_ids = sorted({str(r["expansion_external_id"]) for r in anchor_rows if r.get("expansion_external_id")})
            candidates = []
            for expansion_id in expansion_ids:
                cur.execute(
                    """SELECT e.id external_product_id,e.external_id id_product,e.name,e.metacard_external_id,
                              e.website_path
                       FROM external_catalog_products e
                       WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single'
                         AND e.last_seen_at=%s AND e.expansion_external_id=%s
                       ORDER BY e.external_id::bigint""",
                    (game_id, capture, expansion_id),
                )
                products = [dict(r) for r in cur.fetchall()]
                product_row_ids = [int(r["external_product_id"]) for r in products]
                accepted = []
                if product_row_ids:
                    cur.execute(
                        """SELECT e.external_id id_product,l.mapping_method,l.confidence,l.reviewed,
                                  p.id print_id,p.language,p.collector_number,p.rarity,p.variant,s.code set_code
                           FROM external_catalog_print_links l
                           JOIN external_catalog_products e ON e.id=l.external_product_id
                           JOIN prints p ON p.id=l.print_id
                           JOIN sets s ON s.id=p.set_id
                           WHERE l.external_product_id=ANY(%s) AND l.link_status=ANY(%s)
                           ORDER BY e.external_id::bigint""",
                        (product_row_ids, list(ACCEPTED)),
                    )
                    accepted = [dict(r) for r in cur.fetchall()]

                language_hist = Counter(str(r.get("language") or "") for r in accepted)
                set_hist = Counter(str(r.get("set_code") or "") for r in accepted)
                candidates.append(
                    {
                        "idExpansion": expansion_id,
                        "products": len(products),
                        "unique_metacards": len({str(r.get("metacard_external_id") or "") for r in products}),
                        "accepted_links": len(accepted),
                        "accepted_language_histogram": dict(sorted(language_hist.items())),
                        "accepted_set_histogram": dict(sorted(set_hist.items())),
                        "anchors_present": sorted({str(r["name"]) for r in products if str(r["name"]) in ANCHORS}),
                        "product_count_delta_vs_duad_ja_prints": len(products) - len(canonical),
                        "exact_surface_count_match": len(products) == len(canonical),
                        "sample_products": [
                            {
                                "idProduct": str(r["id_product"]),
                                "name": r["name"],
                                "idMetacard": str(r.get("metacard_external_id") or ""),
                                "website_path": r.get("website_path"),
                            }
                            for r in products[:12]
                        ],
                    }
                )
            conn.rollback()
    finally:
        conn.close()

    canonical_rarities = Counter(str(r.get("rarity") or "") for r in canonical)
    collectors = Counter(str(r.get("collector_number") or "") for r in canonical)
    duplicate_collectors = {k: v for k, v in collectors.items() if v > 1}
    report = {
        "status": "pass",
        "production_writes": 0,
        "cardmarket_capture": str(capture),
        "target": {"set_code": SET_CODE, "language": LANGUAGE, "anchors": list(ANCHORS)},
        "canonical_duad_ja": {
            "prints": len(canonical),
            "unique_cards": len({int(r["card_id"]) for r in canonical}),
            "unique_collectors": len(collectors),
            "duplicate_collector_groups": len(duplicate_collectors),
            "duplicate_collector_prints": sum(duplicate_collectors.values()),
            "rarity_histogram": dict(sorted(canonical_rarities.items())),
            "sample": [
                {
                    "print_id": int(r["print_id"]),
                    "card_name": r["card_name"],
                    "collector_number": r["collector_number"],
                    "rarity": r["rarity"],
                    "variant": r["variant"],
                }
                for r in canonical[:16]
            ],
        },
        "anchor_rows": [
            {
                "idExpansion": str(r.get("expansion_external_id") or ""),
                "idProduct": str(r["id_product"]),
                "name": r["name"],
                "idMetacard": str(r.get("metacard_external_id") or ""),
            }
            for r in anchor_rows
        ],
        "candidate_expansions": candidates,
    }
    output = Path(os.getenv("YGO_DUAD_JP_SURFACE_OUTPUT", "/tmp/yugioh-duad-jp-surface-v1.json"))
    output.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n"
    output.write_text(text, encoding="utf-8")
    print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
