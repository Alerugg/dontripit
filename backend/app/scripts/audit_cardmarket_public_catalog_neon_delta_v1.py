from __future__ import annotations

import json
import os
from pathlib import Path

import psycopg2
from psycopg2.extras import RealDictCursor

from app.jobs.cardmarket_public_catalog_sync_v1 import download_catalog_feeds


SURFACES = (
    ("mtg", "single"), ("mtg", "non_single"),
    ("pokemon", "single"), ("pokemon", "non_single"),
    ("yugioh", "single"), ("yugioh", "non_single"),
    ("onepiece", "single"), ("onepiece", "non_single"),
    ("riftbound", "single"), ("riftbound", "non_single"),
)


def _connect():
    url = os.getenv("DATABASE_URL_UNPOOLED") or os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL_UNPOOLED or DATABASE_URL is required")
    conn = psycopg2.connect(url, connect_timeout=30, application_name="dontripit_cardmarket_public_catalog_neon_delta_v1")
    conn.set_session(readonly=True, autocommit=False)
    return conn


def _current_capture(cur):
    cur.execute("SELECT max(last_seen_at) AS capture FROM external_catalog_products WHERE source='cardmarket'")
    return cur.fetchone()["capture"]


def _current_rows(cur, capture, game: str, group: str):
    cur.execute(
        """
        SELECT e.external_id,e.name,e.expansion_external_id,e.metacard_external_id,e.category,e.website_path
        FROM external_catalog_products e JOIN games g ON g.id=e.game_id
        WHERE e.source='cardmarket' AND e.last_seen_at=%s AND g.slug=%s AND e.product_group=%s
        ORDER BY e.external_id
        """,
        (capture, game, group),
    )
    return [dict(row) for row in cur.fetchall()]


def _regional_mismatches(cur):
    cur.execute(
        """
        SELECT g.slug,e.external_id AS id_product,e.expansion_external_id,e.name,l.print_id,p.language,p.collector_number,l.link_status
        FROM external_catalog_print_links l
        JOIN external_catalog_products e ON e.id=l.external_product_id
        JOIN games g ON g.id=e.game_id JOIN prints p ON p.id=l.print_id
        WHERE e.source='cardmarket' AND e.product_group='single'
          AND l.link_status IN ('accepted','mapped','exact')
          AND ((g.slug='yugioh' AND e.expansion_external_id='5421' AND lower(coalesce(p.language,''))<>'ja')
            OR (g.slug='onepiece' AND e.expansion_external_id='6606' AND lower(coalesce(p.language,''))<>'ja'))
        ORDER BY g.slug,e.external_id
        """
    )
    return [dict(row) for row in cur.fetchall()]


def main() -> int:
    feeds, source_files = download_catalog_feeds(game_slugs=("mtg", "pokemon", "yugioh", "onepiece", "riftbound"))
    feed_map = {(feed.game_slug, feed.product_group): feed for feed in feeds}

    conn = _connect()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            capture = _current_capture(cur)
            if capture is None:
                raise RuntimeError("No current Cardmarket capture in Neon")
            current = {surface: _current_rows(cur, capture, *surface) for surface in SURFACES}
            mismatches = _regional_mismatches(cur)
            conn.rollback()
    finally:
        conn.close()

    surfaces = {}
    totals = {"official": 0, "neon_current": 0, "overlap": 0, "official_only": 0, "neon_only": 0}
    for game, group in SURFACES:
        feed = feed_map[(game, group)]
        official_by_id = {str(row.product_id): row for row in feed.rows}
        neon_by_id = {str(row["external_id"]): row for row in current[(game, group)]}
        official_ids = set(official_by_id)
        neon_ids = set(neon_by_id)
        overlap = official_ids & neon_ids
        official_only = official_ids - neon_ids
        neon_only = neon_ids - official_ids
        totals["official"] += len(official_ids)
        totals["neon_current"] += len(neon_ids)
        totals["overlap"] += len(overlap)
        totals["official_only"] += len(official_only)
        totals["neon_only"] += len(neon_only)
        surfaces[f"{game}:{group}"] = {
            "official": len(official_ids),
            "neon_current": len(neon_ids),
            "overlap": len(overlap),
            "official_only": len(official_only),
            "neon_only": len(neon_only),
            "official_coverage_of_neon": round(len(overlap) / len(neon_ids), 6) if neon_ids else None,
            "neon_coverage_of_official": round(len(overlap) / len(official_ids), 6) if official_ids else None,
            "official_only_samples": [
                {
                    "idProduct": product_id,
                    "name": official_by_id[product_id].name,
                    "idExpansion": official_by_id[product_id].expansion_id,
                    "idMetacard": official_by_id[product_id].metacard_id,
                }
                for product_id in sorted(official_only, key=lambda x: int(x) if x.isdigit() else x)[:40]
            ],
            "neon_only_samples": [neon_by_id[product_id] for product_id in sorted(neon_only, key=lambda x: int(x) if x.isdigit() else x)[:40]],
        }

    report = {
        "status": "pass",
        "production_writes": 0,
        "current_neon_capture": str(capture),
        "totals": totals,
        "surfaces": surfaces,
        "certified_regional_language_mismatches_current": len(mismatches),
        "regional_mismatch_samples": mismatches[:40],
        "source_files": source_files,
    }
    output = Path(os.getenv("CARDMARKET_PUBLIC_CATALOG_NEON_DELTA_OUTPUT", "/tmp/cardmarket-public-catalog-neon-delta-v1.json"))
    output.parent.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n"
    output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
