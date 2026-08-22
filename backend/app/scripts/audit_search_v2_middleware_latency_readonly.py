from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import psycopg2


OUTPUT = Path(os.getenv("SEARCH_V2_MIDDLEWARE_OUTPUT", "artifacts/search-v2-middleware-latency.json"))
CUTOFF = os.getenv("FRA1_READY_AT", "2026-08-15T01:04:33Z")

QUERY = """
SELECT
  endpoint,
  CASE
    WHEN api_key_prefix = 'internal' THEN 'internal-first-party'
    WHEN api_key_prefix IS NULL THEN 'public-ip'
    ELSE 'api-key'
  END AS auth_mode,
  count(*)::int AS samples,
  count(*) FILTER (WHERE status_code >= 400)::int AS errors,
  round(avg(latency_ms)::numeric, 1) AS avg_ms,
  percentile_cont(0.50) WITHIN GROUP (ORDER BY latency_ms) AS p50_ms,
  percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms) AS p95_ms,
  percentile_cont(0.99) WITHIN GROUP (ORDER BY latency_ms) AS p99_ms,
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
"""


def _parse_cutoff(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def _clean(value):
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if value is not None and value.__class__.__name__ == "Decimal":
        return float(value)
    return value


def main() -> int:
    url = (os.getenv("DATABASE_URL_UNPOOLED") or os.getenv("DATABASE_URL") or "").strip()
    if not url:
        raise RuntimeError("DATABASE_URL_UNPOOLED or DATABASE_URL is required")

    now = datetime.now(timezone.utc)
    windows = [
        ("last_6h", now - timedelta(hours=6)),
        ("last_24h", now - timedelta(hours=24)),
        ("since_fra1", _parse_cutoff(CUTOFF)),
    ]

    rows: list[dict] = []
    conn = psycopg2.connect(url)
    conn.autocommit = False
    try:
        with conn.cursor() as cur:
            cur.execute("SHOW transaction_read_only")
            if cur.fetchone()[0] != "on":
                raise RuntimeError("refusing audit without transaction_read_only=on")

            for window_name, window_start in windows:
                cur.execute(QUERY, (window_start,))
                names = [item.name for item in cur.description]
                for raw_row in cur.fetchall():
                    row = dict(zip(names, raw_row))
                    row["window"] = window_name
                    row["window_start"] = window_start
                    rows.append(row)
        conn.rollback()
    finally:
        conn.close()

    normalized = [
        {key: _clean(value) for key, value in row.items()}
        for row in rows
    ]

    window_summaries = {}
    for window_name, _ in windows:
        window_rows = [row for row in normalized if row["window"] == window_name]
        search_rows = [row for row in window_rows if row["endpoint"] == "/api/v2/search"]
        public_search = [row for row in search_rows if row["auth_mode"] == "public-ip"]
        keyed_search = [row for row in search_rows if row["auth_mode"] == "api-key"]
        internal_search = [row for row in search_rows if row["auth_mode"] == "internal-first-party"]
        window_summaries[window_name] = {
            "groups": len(window_rows),
            "search_samples": sum(row["samples"] for row in search_rows),
            "public_search_samples": sum(row["samples"] for row in public_search),
            "api_key_search_samples": sum(row["samples"] for row in keyed_search),
            "internal_search_samples": sum(row["samples"] for row in internal_search),
        }

    report = {
        "status": "pass",
        "generated_at": now.isoformat(),
        "cutoff": CUTOFF,
        "transaction_read_only": True,
        "production_writes": 0,
        "interpretation_contract": {
            "health_skips_catalog_auth_and_rate_limit": True,
            "latency_ms_is_computed_before_metric_insert": True,
            "catalog_latency_includes_auth_rate_limit_and_route": True,
            "internal_first_party_prefix": "internal",
            "p95_p99_are_accumulated_production_percentiles_per_window": True,
            "recent_windows_may_be_sparse_and_must_be_interpreted_with_sample_count": True,
        },
        "windows": window_summaries,
        "groups": normalized,
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
