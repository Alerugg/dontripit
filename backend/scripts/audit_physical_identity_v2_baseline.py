#!/usr/bin/env python3
from __future__ import annotations

import json
import os
from collections import Counter
from pathlib import Path

import psycopg2
from psycopg2.extras import RealDictCursor


GAME_SLUGS = ("mtg", "pokemon", "yugioh", "onepiece")
ACCEPTED_STATUSES = ("accepted", "mapped", "exact")


def pct(num: int, den: int) -> float:
    if not den:
        return 0.0
    return round(100.0 * num / den, 6)


def scalar(cur, sql: str, params=()) -> int:
    cur.execute(sql, params)
    row = cur.fetchone()
    if row is None:
        return 0
    if isinstance(row, dict):
        return int(next(iter(row.values())) or 0)
    return int(row[0] or 0)


def value(cur, sql: str, params=()):
    cur.execute(sql, params)
    row = cur.fetchone()
    if row is None:
        return None
    if isinstance(row, dict):
        return next(iter(row.values()))
    return row[0]


def distribution(cur, sql: str, params=(), limit: int = 30) -> list[dict]:
    cur.execute(sql, params)
    rows = [dict(r) for r in cur.fetchall()]
    rows.sort(key=lambda r: (-int(r.get("count") or 0), str(r.get("value") or "")))
    return rows[:limit]


def audit_game(cur, game_id: int, slug: str) -> dict:
    cards = scalar(cur, "SELECT count(*) FROM cards WHERE game_id=%s", (game_id,))
    sets = scalar(cur, "SELECT count(*) FROM sets WHERE game_id=%s", (game_id,))
    prints = scalar(
        cur,
        "SELECT count(*) FROM prints p JOIN cards c ON c.id=p.card_id WHERE c.game_id=%s",
        (game_id,),
    )

    rarity_missing = scalar(
        cur,
        """
        SELECT count(*)
        FROM prints p JOIN cards c ON c.id=p.card_id
        WHERE c.game_id=%s AND (p.rarity IS NULL OR btrim(p.rarity)='')
        """,
        (game_id,),
    )
    print_key_missing = scalar(
        cur,
        """
        SELECT count(*)
        FROM prints p JOIN cards c ON c.id=p.card_id
        WHERE c.game_id=%s AND (p.print_key IS NULL OR btrim(p.print_key)='')
        """,
        (game_id,),
    )
    default_variant = scalar(
        cur,
        """
        SELECT count(*)
        FROM prints p JOIN cards c ON c.id=p.card_id
        WHERE c.game_id=%s AND lower(coalesce(p.variant,'default'))='default'
        """,
        (game_id,),
    )
    foil_true = scalar(
        cur,
        """
        SELECT count(*)
        FROM prints p JOIN cards c ON c.id=p.card_id
        WHERE c.game_id=%s AND p.is_foil IS TRUE
        """,
        (game_id,),
    )

    source_identity_field = {
        "mtg": "scryfall_id",
        "pokemon": "tcgdex_id",
        "yugioh": "yugioh_id",
        "onepiece": None,
    }[slug]
    source_identity_present = None
    if source_identity_field:
        source_identity_present = scalar(
            cur,
            f"""
            SELECT count(*)
            FROM prints p JOIN cards c ON c.id=p.card_id
            WHERE c.game_id=%s
              AND p.{source_identity_field} IS NOT NULL
              AND btrim(p.{source_identity_field})<>''
            """,
            (game_id,),
        )

    cm_products = scalar(
        cur,
        """
        SELECT count(*)
        FROM external_catalog_products e
        WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single'
        """,
        (game_id,),
    )
    cm_exact_products = scalar(
        cur,
        """
        SELECT count(DISTINCT e.id)
        FROM external_catalog_products e
        JOIN external_catalog_print_links l ON l.external_product_id=e.id
        WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single'
          AND l.confidence='exact'
          AND l.link_status IN ('accepted','mapped','exact')
        """,
        (game_id,),
    )
    cm_exact_link_rows = scalar(
        cur,
        """
        SELECT count(*)
        FROM external_catalog_products e
        JOIN external_catalog_print_links l ON l.external_product_id=e.id
        WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single'
          AND l.confidence='exact'
          AND l.link_status IN ('accepted','mapped','exact')
        """,
        (game_id,),
    )
    cm_exact_prints = scalar(
        cur,
        """
        SELECT count(DISTINCT l.print_id)
        FROM external_catalog_products e
        JOIN external_catalog_print_links l ON l.external_product_id=e.id
        WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single'
          AND l.confidence='exact'
          AND l.link_status IN ('accepted','mapped','exact')
        """,
        (game_id,),
    )
    cm_candidate_products = scalar(
        cur,
        """
        SELECT count(DISTINCT e.id)
        FROM external_catalog_products e
        JOIN external_catalog_print_links l ON l.external_product_id=e.id
        WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single'
          AND NOT (l.confidence='exact' AND l.link_status IN ('accepted','mapped','exact'))
        """,
        (game_id,),
    )
    cm_unlinked_products = max(cm_products - cm_exact_products, 0)

    product_cardinality = distribution(
        cur,
        """
        SELECT exact_targets::text AS value, count(*) AS count
        FROM (
          SELECT e.id, count(DISTINCT l.print_id) FILTER (
            WHERE l.confidence='exact' AND l.link_status IN ('accepted','mapped','exact')
          ) AS exact_targets
          FROM external_catalog_products e
          LEFT JOIN external_catalog_print_links l ON l.external_product_id=e.id
          WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single'
          GROUP BY e.id
        ) q
        GROUP BY exact_targets
        """,
        (game_id,),
        50,
    )
    print_market_cardinality = distribution(
        cur,
        """
        SELECT market_products::text AS value, count(*) AS count
        FROM (
          SELECT p.id, count(DISTINCT e.id) FILTER (
            WHERE l.confidence='exact' AND l.link_status IN ('accepted','mapped','exact')
          ) AS market_products
          FROM prints p
          JOIN cards c ON c.id=p.card_id
          LEFT JOIN external_catalog_print_links l ON l.print_id=p.id
          LEFT JOIN external_catalog_products e ON e.id=l.external_product_id
            AND e.source='cardmarket' AND e.game_id=%s AND e.product_group='single'
          WHERE c.game_id=%s
          GROUP BY p.id
        ) q
        GROUP BY market_products
        """,
        (game_id, game_id),
        50,
    )

    duplicate_metacard_groups = scalar(
        cur,
        """
        SELECT count(*) FROM (
          SELECT e.expansion_external_id, e.metacard_external_id, lower(e.name)
          FROM external_catalog_products e
          WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single'
            AND e.metacard_external_id IS NOT NULL
          GROUP BY e.expansion_external_id, e.metacard_external_id, lower(e.name)
          HAVING count(*) > 1
        ) q
        """,
        (game_id,),
    )

    latest_external_price_asof = value(
        cur,
        """
        SELECT max(s.as_of)
        FROM external_market_price_snapshots s
        JOIN external_catalog_products e ON e.id=s.external_product_id
        WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single'
        """,
        (game_id,),
    )
    current_priced_market_products = 0
    if latest_external_price_asof is not None:
        current_priced_market_products = scalar(
            cur,
            """
            SELECT count(DISTINCT e.id)
            FROM external_market_price_snapshots s
            JOIN external_catalog_products e ON e.id=s.external_product_id
            WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single'
              AND s.as_of=%s
            """,
            (game_id, latest_external_price_asof),
        )

    latest_canonical_price_asof = value(
        cur,
        """
        SELECT max(ps.as_of)
        FROM price_snapshots ps
        JOIN prints p ON p.id=ps.entity_id
        JOIN cards c ON c.id=p.card_id
        JOIN price_sources src ON src.id=ps.source_id
        WHERE ps.entity_type='print' AND src.name='cardmarket' AND c.game_id=%s
        """,
        (game_id,),
    )
    current_priced_prints = 0
    if latest_canonical_price_asof is not None:
        current_priced_prints = scalar(
            cur,
            """
            SELECT count(DISTINCT ps.entity_id)
            FROM price_snapshots ps
            JOIN prints p ON p.id=ps.entity_id
            JOIN cards c ON c.id=p.card_id
            JOIN price_sources src ON src.id=ps.source_id
            WHERE ps.entity_type='print' AND src.name='cardmarket' AND c.game_id=%s
              AND ps.as_of=%s
            """,
            (game_id, latest_canonical_price_asof),
        )

    rarity_distribution = distribution(
        cur,
        """
        SELECT coalesce(nullif(btrim(p.rarity),''),'<missing>') AS value, count(*) AS count
        FROM prints p JOIN cards c ON c.id=p.card_id
        WHERE c.game_id=%s
        GROUP BY 1
        """,
        (game_id,),
    )
    variant_distribution = distribution(
        cur,
        """
        SELECT coalesce(nullif(btrim(p.variant),''),'<missing>') AS value, count(*) AS count
        FROM prints p JOIN cards c ON c.id=p.card_id
        WHERE c.game_id=%s
        GROUP BY 1
        """,
        (game_id,),
    )
    language_distribution = distribution(
        cur,
        """
        SELECT coalesce(nullif(btrim(p.language),''),'<missing>') AS value, count(*) AS count
        FROM prints p JOIN cards c ON c.id=p.card_id
        WHERE c.game_id=%s
        GROUP BY 1
        """,
        (game_id,),
    )

    return {
        "game": slug,
        "canonical": {
            "cards": cards,
            "sets": sets,
            "prints": prints,
            "rarity_missing": rarity_missing,
            "rarity_missing_pct": pct(rarity_missing, prints),
            "print_key_missing": print_key_missing,
            "print_key_missing_pct": pct(print_key_missing, prints),
            "default_variant": default_variant,
            "default_variant_pct": pct(default_variant, prints),
            "foil_true": foil_true,
            "foil_true_pct": pct(foil_true, prints),
            "source_identity_present": source_identity_present,
            "source_identity_present_pct": None if source_identity_present is None else pct(source_identity_present, prints),
            "rarity_distribution": rarity_distribution,
            "variant_distribution": variant_distribution,
            "language_distribution": language_distribution,
        },
        "cardmarket": {
            "single_products": cm_products,
            "exact_linked_products": cm_exact_products,
            "existing_exact_accounted_pct": pct(cm_exact_products, cm_products),
            "unlinked_products": cm_unlinked_products,
            "unlinked_pct": pct(cm_unlinked_products, cm_products),
            "candidate_or_nonexact_products": cm_candidate_products,
            "exact_link_rows": cm_exact_link_rows,
            "exact_linked_prints": cm_exact_prints,
            "product_to_print_cardinality": product_cardinality,
            "print_to_market_product_cardinality": print_market_cardinality,
            "duplicate_metacard_name_expansion_groups": duplicate_metacard_groups,
            "latest_external_price_asof": latest_external_price_asof.isoformat() if latest_external_price_asof else None,
            "current_priced_market_products": current_priced_market_products,
            "current_priced_market_product_pct": pct(current_priced_market_products, cm_products),
            "latest_canonical_price_asof": latest_canonical_price_asof.isoformat() if latest_canonical_price_asof else None,
            "current_priced_prints": current_priced_prints,
            "current_priced_print_pct": pct(current_priced_prints, prints),
        },
        "slo": {
            "target_accounted_cardmarket_pct": 99.0,
            "current_existing_exact_accounted_pct": pct(cm_exact_products, cm_products),
            "gap_products_to_99pct": max(int((0.99 * cm_products) + 0.999999) - cm_exact_products, 0),
            "passes_99pct_existing_exact_only": pct(cm_exact_products, cm_products) >= 99.0,
        },
    }


def main() -> int:
    url = os.getenv("DATABASE_URL_UNPOOLED") or os.getenv("DATABASE_URL")
    if not url:
        raise SystemExit("DATABASE_URL_UNPOOLED or DATABASE_URL is required")

    report_path = Path(os.getenv("REPORT_PATH", "/tmp/physical-identity-v2-baseline.json"))
    conn = psycopg2.connect(url)
    conn.autocommit = False
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT id, slug, name FROM games WHERE slug = ANY(%s) ORDER BY slug",
                (list(GAME_SLUGS),),
            )
            games = {str(r["slug"]): dict(r) for r in cur.fetchall()}
            missing_games = [slug for slug in GAME_SLUGS if slug not in games]
            if missing_games:
                raise RuntimeError(f"missing expected games: {missing_games}")

            results = [audit_game(cur, int(games[slug]["id"]), slug) for slug in GAME_SLUGS]

        total_cm = sum(int(r["cardmarket"]["single_products"]) for r in results)
        total_exact = sum(int(r["cardmarket"]["exact_linked_products"]) for r in results)
        total_unlinked = sum(int(r["cardmarket"]["unlinked_products"]) for r in results)
        summary = {
            "mode": "read_only",
            "audit": "physical_identity_v2_baseline",
            "games": list(GAME_SLUGS),
            "cardmarket_single_products": total_cm,
            "existing_exact_linked_products": total_exact,
            "existing_exact_accounted_pct": pct(total_exact, total_cm),
            "unlinked_products": total_unlinked,
            "unlinked_pct": pct(total_unlinked, total_cm),
            "production_writes": 0,
            "target_accounted_pct_per_game": 99.0,
        }
        payload = {"summary": summary, "games": results}
        print("PHYSICAL_IDENTITY_V2_BASELINE=" + json.dumps(summary, separators=(",", ":")))
        report_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        conn.rollback()
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
