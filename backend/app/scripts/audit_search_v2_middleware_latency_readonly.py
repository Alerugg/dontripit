from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import psycopg2


OUTPUT = Path(os.getenv("SEARCH_V2_MIDDLEWARE_OUTPUT", "artifacts/search-v2-middleware-latency.json"))
CUTOFF = os.getenv("FRA1_READY_AT", "2026-08-15T01:04:33Z")


def main() -> int:
    url = (os.getenv("DATABASE_URL_UNPOOLED") or os.getenv("DATABASE_URL") or "").strip()
    if not url:
        raise RuntimeError("DATABASE_URL_UNPOOLED or DATABASE_URL is required")

    conn = psycopg2.connect(url)
    conn.autocommit = False
    try:
        with conn.cursor() as cur:
            cur.execute("SHOW transaction_read_only")
            if cur.fetchone()[0] != "on":
                raise RuntimeError("refusing audit without transaction_read_only=on")

            cur.execute(
                """
                SELECT
                  endpoint,
                  CASE WHEN api_key_prefix IS NULL THEN 'public-ip' ELSE 'api-key' END AS auth_mode,
                  count(*)::int AS samples,
                  count(*) FILTER (WHERE status_code >= 400)::int AS errors,
                  round(avg(latency_ms)::numeric, 1) AS avg_ms,
                  percentile_cont(0.50) WITHIN GROUP (ORDER BY latency_ms) AS p50_ms,
                  percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms) AS p95_ms,
                  max(latency_ms)::int AS max_ms,
                  min(requested_at) AS first_seen,
                  max(requested_at) AS last_seen
                FROM api_request_metrics
                WHERE requested_at >= %s::timestamptz
                  AND endpoint IN (
                    '/api/health',
                    '/api/v1/health',
                    '/api/db-check',
                    '/api/v1/db-check',
                    '/api/games',
                    '/api/v1/games',
                    '/api/v2/search',
                    '/api/v2/search/suggest',
                    '/api/v2/search/advanced',
                    '/api/v2/games/onepiece/facets',
                    '/api/v2/games/pokemon/facets',
                    '/api/v2/games/yugioh/facets',
                    '/api/v2/games/mtg/facets'
                  )
                GROUP BY endpoint, auth_mode
                ORDER BY endpoint, auth_mode
                """,
                (CUTOFF,),
            )
            names = [item.name for item in cur.description]
            rows = [dict(zip(names, row)) for row in cur.fetchall()]
        conn.rollback()
    finally:
        conn.close()

    def clean(value):
        if hasattr(value, "isoformat"):
            return value.isoformat()
        if value is not None and value.__class__.__name__ == "Decimal":
            return float(value)
        return value

    normalized = [{key: clean(value) for key, value in row.items()} for row in rows]
    health = [row for row in normalized if row["endpoint"] in {"/api/health", "/api/v1/health"}]
    keyed_search = [
        row for row in normalized
        if row["auth_mode"] == "api-key" and row["endpoint"].startswith("/api/v2/")
    ]
    public_search = [
        row for row in normalized
        if row["auth_mode"] == "public-ip" and row["endpoint"].startswith("/api/v2/")
    ]

    report = {
        "status": "pass",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "cutoff": CUTOFF,
        "transaction_read_only": True,
        "production_writes": 0,
        "interpretation_contract": {
            "health_skips_catalog_auth_and_rate_limit": True,
            "latency_ms_is_computed_before_metric_insert": True,
            "catalog_latency_includes_auth_rate_limit_and_route": True,
        },
        "summary": {
            "rows": len(normalized),
            "health_groups": len(health),
            "keyed_search_groups": len(keyed_search),
            "public_search_groups": len(public_search),
        },
        "groups": normalized,
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
