from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import text

from app import db


OUTPUT = Path(os.getenv("ONEPIECE_DON_PRICE_AUDIT_OUTPUT", "artifacts/onepiece-don-price-source-v1.json"))


def main() -> int:
    if not (os.getenv("DATABASE_URL_UNPOOLED") or os.getenv("DATABASE_URL")):
        raise RuntimeError("DATABASE_URL_UNPOOLED or DATABASE_URL is required")

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "running",
        "production_writes": 0,
        "transaction_read_only": False,
    }
    with db.SessionLocal() as session:
        session.execute(text("SET TRANSACTION READ ONLY"))
        report["transaction_read_only"] = session.execute(text("SHOW transaction_read_only")).scalar_one() == "on"
        if not report["transaction_read_only"]:
            raise AssertionError("DON price audit is not read-only")

        coverage = session.execute(
            text(
                """
                WITH latest AS (
                  SELECT max(source_as_of) AS source_as_of
                  FROM onepiece_don_market_items
                  WHERE source='cardmarket'
                ), ids AS MATERIALIZED (
                  SELECT DISTINCT product_id.value AS id_product
                  FROM onepiece_don_market_items m
                  JOIN latest l ON l.source_as_of=m.source_as_of
                  CROSS JOIN LATERAL jsonb_array_elements_text(COALESCE(m.product_ids_json, '[]'::jsonb)) product_id(value)
                  WHERE m.source='cardmarket'
                ), ext AS MATERIALIZED (
                  SELECT e.id, e.external_id
                  FROM ids
                  JOIN games g ON g.slug='onepiece'
                  JOIN external_catalog_products e
                    ON e.source='cardmarket'
                   AND e.game_id=g.id
                   AND e.product_group='single'
                   AND e.external_id=ids.id_product
                )
                SELECT
                  (SELECT count(*) FROM ids)::bigint AS don_product_ids,
                  (SELECT count(*) FROM ext)::bigint AS external_products_resolved,
                  count(DISTINCT ext.id) FILTER (WHERE emp.id IS NOT NULL)::bigint AS products_with_any_external_snapshot,
                  count(DISTINCT ext.id) FILTER (
                    WHERE COALESCE(NULLIF(emp.price_mid,0), NULLIF(emp.price_market,0), NULLIF(emp.price_last,0), NULLIF(emp.price_low,0)) IS NOT NULL
                  )::bigint AS products_with_nonzero_external_price,
                  count(emp.id)::bigint AS external_snapshot_rows,
                  min(emp.as_of) AS external_min_as_of,
                  max(emp.as_of) AS external_max_as_of
                FROM ext
                LEFT JOIN external_market_price_snapshots emp ON emp.external_product_id=ext.id
                """
            )
        ).mappings().one()
        report["coverage"] = dict(coverage)

        variants = session.execute(
            text(
                """
                WITH latest AS (
                  SELECT max(source_as_of) AS source_as_of
                  FROM onepiece_don_market_items
                  WHERE source='cardmarket'
                ), ids AS MATERIALIZED (
                  SELECT DISTINCT product_id.value AS id_product
                  FROM onepiece_don_market_items m
                  JOIN latest l ON l.source_as_of=m.source_as_of
                  CROSS JOIN LATERAL jsonb_array_elements_text(COALESCE(m.product_ids_json, '[]'::jsonb)) product_id(value)
                  WHERE m.source='cardmarket'
                )
                SELECT emp.price_variant, count(*)::bigint AS rows, count(DISTINCT emp.external_product_id)::bigint AS products,
                       min(emp.as_of) AS min_as_of, max(emp.as_of) AS max_as_of
                FROM ids
                JOIN games g ON g.slug='onepiece'
                JOIN external_catalog_products e
                  ON e.source='cardmarket' AND e.game_id=g.id AND e.product_group='single' AND e.external_id=ids.id_product
                JOIN external_market_price_snapshots emp ON emp.external_product_id=e.id
                GROUP BY emp.price_variant
                ORDER BY emp.price_variant
                """
            )
        ).mappings().all()
        report["external_price_variants"] = [dict(row) for row in variants]

        projected = session.execute(
            text(
                """
                WITH latest AS (
                  SELECT max(source_as_of) AS source_as_of
                  FROM onepiece_don_market_items
                  WHERE source='cardmarket'
                ), ids AS MATERIALIZED (
                  SELECT DISTINCT product_id.value AS id_product
                  FROM onepiece_don_market_items m
                  JOIN latest l ON l.source_as_of=m.source_as_of
                  CROSS JOIN LATERAL jsonb_array_elements_text(COALESCE(m.product_ids_json, '[]'::jsonb)) product_id(value)
                  WHERE m.source='cardmarket'
                )
                SELECT ps.entity_type, count(*)::bigint AS rows,
                       count(DISTINCT COALESCE(ps.raw_json->>'idProduct','')) FILTER (WHERE COALESCE(ps.raw_json->>'idProduct','') <> '')::bigint AS id_products
                FROM price_snapshots ps
                JOIN price_sources src ON src.id=ps.source_id AND src.name='cardmarket'
                WHERE COALESCE(ps.raw_json->>'idProduct','') IN (SELECT id_product FROM ids)
                GROUP BY ps.entity_type
                ORDER BY ps.entity_type
                """
            )
        ).mappings().all()
        report["projected_price_snapshots"] = [dict(row) for row in projected]
        session.rollback()

    report["status"] = "pass"
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
