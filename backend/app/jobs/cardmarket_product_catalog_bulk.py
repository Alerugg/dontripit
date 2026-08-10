from __future__ import annotations

import json
import os
import re

import psycopg2


CATEGORY_TYPES = {
    "MtG Set": "set_product", "Magic Intropack": "intro_pack", "Magic Booster": "booster_pack",
    "Magic Display": "booster_box", "Magic Theme Deck Display": "deck_display",
    "Magic TournamentPack": "tournament_pack", "Magic Fatpack": "fatpack",
    "Magic Starter Deck": "starter_deck", "Magic Event Tickets": "event_ticket",
    "Magic Lot": "lot", "Magic Miscellaneous": "miscellaneous",
    "One Piece Booster": "booster_pack", "One Piece Lots": "lot",
    "One Piece Promo Products": "promo_product", "One Piece Preconstructed Decks": "preconstructed_deck",
    "One Piece Booster Boxes": "booster_box", "Pokémon Box Set": "box_set",
    "Pokémon Booster": "booster_pack", "Pokémon Coins": "coin", "Pokémon Display": "booster_box",
    "Pokémon Tins": "tin", "Pokémon Theme Deck": "theme_deck", "Pokémon Blisters": "blister",
    "Pokémon Elite Trainer Boxes": "elite_trainer_box", "Pokémon Lot": "lot", "PCG Set": "set_product",
    "Pokémon Trainer Kits": "trainer_kit", "Pokémon Pokémon Sets": "set_product",
    "Yugioh Booster": "booster_pack", "Yugioh Display": "booster_box",
    "Yugioh Structure Deck": "structure_deck", "Yugioh Special Edition": "special_edition",
    "Yugioh Promo Products": "promo_product", "Yugioh Collector Tins": "collector_tin",
    "Yugioh Starter Deck": "starter_deck", "Yugioh Lot": "lot", "Yugioh Event Tickets": "event_ticket",
}


def _slug(value: str | None) -> str:
    result = re.sub(r"[^a-z0-9]+", "_", str(value or "").casefold()).strip("_")
    return (result or "market_product")[:100]


def _dimensions(name: str) -> tuple[str, str]:
    folded = str(name or "").casefold()
    if "chinese edition" in folded or "chinese version" in folded:
        return "zh", "cn"
    if "japanese" in folded:
        return "ja", "jp"
    if "asia region legal" in folded or "asian english" in folded:
        return "und", "asia"
    if "us version" in folded or "usa version" in folded:
        return "und", "us"
    if "(ocg)" in folded:
        return "und", "ocg"
    return "und", "global"


def _dsn() -> str:
    return os.getenv("DATABASE_URL_UNPOOLED") or os.getenv("DATABASE_URL") or ""


def run() -> dict:
    dsn = _dsn()
    if not dsn:
        raise RuntimeError("DATABASE_URL_UNPOOLED/DATABASE_URL is required")
    conn = psycopg2.connect(dsn)
    conn.autocommit = False
    try:
        with conn.cursor() as cur:
            cur.execute("SET LOCAL lock_timeout = '15s'")
            cur.execute("SET LOCAL statement_timeout = '20min'")
            cur.execute("SELECT pg_advisory_xact_lock(hashtext('dontripit-cardmarket-product-catalog-v2'))")
            cur.execute("SELECT max(last_seen_at) FROM external_catalog_products WHERE source='cardmarket'")
            capture = cur.fetchone()[0]
            if capture is None:
                raise RuntimeError("Cardmarket external catalog is empty")

            cur.execute("""
                SELECT e.id,e.external_id,e.game_id,e.name,e.category,e.expansion_external_id,e.website_path
                FROM external_catalog_products e JOIN games g ON g.id=e.game_id
                WHERE e.source='cardmarket' AND e.product_group='non_single' AND e.last_seen_at=%s
                  AND g.slug IN ('pokemon','onepiece','mtg','yugioh')
                ORDER BY e.id
            """, (capture,))
            source_rows = cur.fetchall()
            if not source_rows:
                raise RuntimeError("Current Cardmarket non-single catalog is empty")

            cur.execute("""
                CREATE TEMP TABLE cm_product_stage (
                    external_product_id bigint PRIMARY KEY,
                    external_id varchar(255) NOT NULL,
                    game_id bigint NOT NULL,
                    name varchar(500) NOT NULL,
                    category varchar(255),
                    expansion_external_id varchar(255),
                    website_path text,
                    product_type varchar(100) NOT NULL,
                    language varchar(16) NOT NULL,
                    region varchar(16) NOT NULL,
                    packaging varchar(100),
                    product_id bigint,
                    variant_id bigint
                ) ON COMMIT DROP
            """)
            from psycopg2.extras import execute_values
            staged = []
            for external_product_id, external_id, game_id, name, category, expansion_external_id, website_path in source_rows:
                product_type = CATEGORY_TYPES.get(str(category or ""), _slug(category))
                language, region = _dimensions(str(name or ""))
                staged.append((
                    int(external_product_id), str(external_id), int(game_id), str(name), category,
                    expansion_external_id, website_path, product_type, language, region, product_type,
                ))
            execute_values(cur, """
                INSERT INTO cm_product_stage
                  (external_product_id,external_id,game_id,name,category,expansion_external_id,website_path,
                   product_type,language,region,packaging)
                VALUES %s
            """, staged, page_size=1000)

            cur.execute("""
                SELECT count(*)
                FROM cm_product_stage s
                JOIN product_identifiers pi ON pi.source='cardmarket' AND pi.external_id=s.external_id
                JOIN product_variants pv ON pv.id=pi.product_variant_id
                JOIN products p ON p.id=pv.product_id
                WHERE p.game_id<>s.game_id
            """)
            cross_game_existing = int(cur.fetchone()[0])
            if cross_game_existing:
                raise RuntimeError(f"Existing Cardmarket product identifiers cross games: {cross_game_existing}")

            cur.execute("""
                UPDATE cm_product_stage s
                SET variant_id=pi.product_variant_id, product_id=pv.product_id
                FROM product_identifiers pi JOIN product_variants pv ON pv.id=pi.product_variant_id
                WHERE pi.source='cardmarket' AND pi.external_id=s.external_id
            """)
            reused = cur.rowcount

            cur.execute("SELECT pg_get_serial_sequence('products','id'), pg_get_serial_sequence('product_variants','id')")
            product_seq, variant_seq = cur.fetchone()
            if not product_seq or not variant_seq:
                raise RuntimeError("Could not resolve canonical product sequences")
            cur.execute(f"""
                UPDATE cm_product_stage
                SET product_id=nextval(%s::regclass), variant_id=nextval(%s::regclass)
                WHERE variant_id IS NULL
            """, (product_seq, variant_seq))
            created = cur.rowcount

            cur.execute("""
                INSERT INTO products (id,game_id,set_id,product_type,name,release_date)
                SELECT s.product_id,s.game_id,NULL,s.product_type,s.name,NULL
                FROM cm_product_stage s
                WHERE NOT EXISTS (SELECT 1 FROM products p WHERE p.id=s.product_id)
            """)
            products_inserted = cur.rowcount
            cur.execute("""
                INSERT INTO product_variants (id,product_id,language,region,packaging,sku)
                SELECT s.variant_id,s.product_id,s.language,s.region,s.packaging,
                       left('cardmarket:'||s.external_id,100)
                FROM cm_product_stage s
                WHERE NOT EXISTS (SELECT 1 FROM product_variants pv WHERE pv.id=s.variant_id)
            """)
            variants_inserted = cur.rowcount
            cur.execute("""
                INSERT INTO product_identifiers (product_variant_id,source,external_id)
                SELECT s.variant_id,'cardmarket',s.external_id FROM cm_product_stage s
                ON CONFLICT (source,external_id) DO NOTHING
            """)
            identifiers_inserted = cur.rowcount

            cur.execute("""
                SELECT count(*)
                FROM cm_product_stage s
                JOIN external_catalog_product_variant_links l
                  ON l.external_product_id=s.external_product_id AND l.link_status='accepted'
                WHERE l.product_variant_id<>s.variant_id
            """)
            conflicting_links = int(cur.fetchone()[0])
            if conflicting_links:
                raise RuntimeError(f"Conflicting accepted Cardmarket ProductVariant links: {conflicting_links}")

            cur.execute("""
                INSERT INTO external_catalog_product_variant_links
                  (external_product_id,product_variant_id,mapping_method,confidence,link_status,reviewed,evidence)
                SELECT s.external_product_id,s.variant_id,'cardmarket_product_identity','exact','accepted',true,
                       jsonb_build_object(
                         'source','cardmarket','idProduct',s.external_id,'category',s.category,
                         'product_type',s.product_type,'idExpansion',s.expansion_external_id,
                         'website_path',s.website_path,'identity_policy','1:1 source commercial product; no name inference'
                       )
                FROM cm_product_stage s
                ON CONFLICT (external_product_id,product_variant_id) DO UPDATE SET
                  mapping_method=EXCLUDED.mapping_method,confidence='exact',link_status='accepted',reviewed=true,
                  evidence=EXCLUDED.evidence,updated_at=now()
            """)
            links_upserted = cur.rowcount

            cur.execute("""
                INSERT INTO price_sources (name,currency,description)
                VALUES ('cardmarket','EUR','Cardmarket downloadable market price guide')
                ON CONFLICT (name) DO NOTHING
            """)
            cur.execute("SELECT id,currency FROM price_sources WHERE name='cardmarket'")
            source_id, currency = cur.fetchone()
            if currency != 'EUR':
                raise RuntimeError(f"Unexpected Cardmarket currency: {currency}")

            cur.execute("""
                WITH latest AS (
                  SELECT e.external_product_id,max(e.as_of) AS as_of
                  FROM external_market_price_snapshots e
                  JOIN cm_product_stage s ON s.external_product_id=e.external_product_id
                  GROUP BY e.external_product_id
                ), candidates AS (
                  SELECT e.*,s.variant_id,
                         count(*) OVER (PARTITION BY e.external_product_id) AS total_variants,
                         count(*) FILTER (WHERE e.price_variant IN ('default','nonfoil'))
                           OVER (PARTITION BY e.external_product_id) AS preferred_variants
                  FROM external_market_price_snapshots e
                  JOIN latest l ON l.external_product_id=e.external_product_id AND l.as_of=e.as_of
                  JOIN cm_product_stage s ON s.external_product_id=e.external_product_id
                ), eligible AS (
                  SELECT * FROM candidates
                  WHERE (preferred_variants=1 AND price_variant IN ('default','nonfoil'))
                     OR (preferred_variants=0 AND total_variants=1)
                )
                INSERT INTO price_snapshots
                  (entity_type,entity_id,source_id,currency,price_low,price_mid,price_high,price_market,price_last,
                   quantity,as_of,raw_json)
                SELECT 'product_variant',e.variant_id,%s,e.currency,e.price_low,e.price_mid,NULL,e.price_market,e.price_last,
                       NULL,e.as_of,
                       jsonb_build_object('idProduct',s.external_id,'price_variant',e.price_variant,
                          'avg1',e.avg1,'avg7',e.avg7,'avg30',e.avg30,'projection','exact_cardmarket_product_variant')
                FROM eligible e JOIN cm_product_stage s ON s.external_product_id=e.external_product_id
                WHERE NOT (e.price_low IS NULL AND e.price_mid IS NULL AND e.price_market IS NULL AND e.price_last IS NULL
                           AND e.avg1 IS NULL AND e.avg7 IS NULL AND e.avg30 IS NULL)
                ON CONFLICT ON CONSTRAINT uq_price_snapshot_identity DO UPDATE SET
                  price_low=EXCLUDED.price_low,price_mid=EXCLUDED.price_mid,price_high=EXCLUDED.price_high,
                  price_market=EXCLUDED.price_market,price_last=EXCLUDED.price_last,quantity=EXCLUDED.quantity,
                  raw_json=EXCLUDED.raw_json
            """, (int(source_id),))
            price_snapshots_upserted = cur.rowcount

            cur.execute("""
                WITH latest AS (
                  SELECT e.external_product_id,max(e.as_of) AS as_of
                  FROM external_market_price_snapshots e JOIN cm_product_stage s ON s.external_product_id=e.external_product_id
                  GROUP BY e.external_product_id
                ), stats AS (
                  SELECT e.external_product_id,count(*) total,
                         count(*) FILTER (WHERE e.price_variant IN ('default','nonfoil')) preferred
                  FROM external_market_price_snapshots e
                  JOIN latest l ON l.external_product_id=e.external_product_id AND l.as_of=e.as_of
                  GROUP BY e.external_product_id
                )
                SELECT count(*) FILTER (WHERE preferred>1 OR (preferred=0 AND total>1)) ambiguous,
                       (SELECT count(*) FROM cm_product_stage)-count(*) price_missing
                FROM stats
            """)
            ambiguous_prices, missing_price_products = [int(v or 0) for v in cur.fetchone()]
            if ambiguous_prices:
                raise RuntimeError(f"Ambiguous current Cardmarket non-single price variants: {ambiguous_prices}")

            cur.execute("""
                SELECT count(*) total,
                  count(*) FILTER (WHERE pi.external_id IS NOT NULL) identifiers,
                  count(*) FILTER (WHERE l.product_variant_id IS NOT NULL) accepted_links,
                  count(*) FILTER (WHERE p.game_id IS DISTINCT FROM s.game_id) cross_game
                FROM cm_product_stage s
                LEFT JOIN product_identifiers pi ON pi.source='cardmarket' AND pi.external_id=s.external_id
                LEFT JOIN product_variants pv ON pv.id=pi.product_variant_id
                LEFT JOIN products p ON p.id=pv.product_id
                LEFT JOIN external_catalog_product_variant_links l
                  ON l.external_product_id=s.external_product_id AND l.product_variant_id=pv.id AND l.link_status='accepted'
            """)
            # Correct external link join uses source table PK, re-run proof explicitly.
            total = len(source_rows)
            cur.execute("SELECT count(*) FROM cm_product_stage s JOIN product_identifiers pi ON pi.source='cardmarket' AND pi.external_id=s.external_id")
            identifiers = int(cur.fetchone()[0])
            cur.execute("""
                SELECT count(*) FROM cm_product_stage s
                JOIN external_catalog_product_variant_links l
                  ON l.external_product_id=s.external_product_id AND l.product_variant_id=s.variant_id AND l.link_status='accepted'
            """)
            accepted_links = int(cur.fetchone()[0])
            cur.execute("""
                SELECT count(*) FROM cm_product_stage s JOIN product_variants pv ON pv.id=s.variant_id
                JOIN products p ON p.id=pv.product_id WHERE p.game_id<>s.game_id
            """)
            cross_game = int(cur.fetchone()[0])
            if (identifiers, accepted_links, cross_game) != (total, total, 0):
                raise RuntimeError({
                    'total':total,'identifiers':identifiers,'accepted_links':accepted_links,'cross_game':cross_game
                })

            result = {
                "capture": capture.isoformat(), "current_products": total, "reused": int(reused), "created": int(created),
                "products_inserted": int(products_inserted), "variants_inserted": int(variants_inserted),
                "identifiers_inserted": int(identifiers_inserted), "accepted_links_upserted": int(links_upserted),
                "price_snapshots_upserted": int(price_snapshots_upserted), "missing_price_products": missing_price_products,
                "ambiguous_price_variants": ambiguous_prices, "cross_game": cross_game,
            }
        conn.commit()
        return result
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    print(json.dumps(run(), indent=2, sort_keys=True))
