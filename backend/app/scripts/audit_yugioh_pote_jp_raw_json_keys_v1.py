from __future__ import annotations

import json
import os
from collections import Counter
from pathlib import Path

import psycopg2
from psycopg2.extras import RealDictCursor


RESIDUAL_IDS = {
    "657441","657442","657443","657445","657446","657447","657453","657454","657455","657456",
    "657458","657459","657460","657463","657464","657465","657467","657468","657469","657484",
    "657485","657486","657487","657489","657490","657491","657492","657493","657511","657512",
    "657513","657514","657515","657516","657517","657562","657563","657564","657565",
}


def main() -> int:
    url = os.getenv("DATABASE_URL_UNPOOLED") or os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL_UNPOOLED or DATABASE_URL is required")
    conn = psycopg2.connect(url, connect_timeout=30, application_name="dontripit_pote_jp_raw_json_keys_v1")
    conn.set_session(readonly=True, autocommit=False)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT id FROM games WHERE slug='yugioh' LIMIT 1")
            game_id = int(cur.fetchone()["id"])
            cur.execute("SELECT max(last_seen_at) capture FROM external_catalog_products WHERE source='cardmarket'")
            capture = cur.fetchone()["capture"]
            cur.execute(
                """SELECT external_id id_product,name,website_path,raw_json
                   FROM external_catalog_products
                   WHERE source='cardmarket' AND game_id=%s AND product_group='single'
                     AND expansion_external_id='5044' AND last_seen_at=%s
                   ORDER BY external_id::bigint""",
                (game_id, capture),
            )
            rows = [dict(r) for r in cur.fetchall()]
            conn.rollback()
    finally:
        conn.close()

    key_counts = Counter()
    nested_key_counts = Counter()
    residual = []
    accepted_samples = []
    for row in rows:
        raw = row.get("raw_json") or {}
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except Exception:
                raw = {"_raw_string": raw}
        if isinstance(raw, dict):
            key_counts.update(raw.keys())
            for k, v in raw.items():
                if isinstance(v, dict):
                    nested_key_counts.update(f"{k}.{nk}" for nk in v.keys())
        rendered = {
            "idProduct": str(row["id_product"]),
            "name": row.get("name"),
            "website_path": row.get("website_path"),
            "raw_json": raw,
        }
        if str(row["id_product"]) in RESIDUAL_IDS:
            residual.append(rendered)
        elif len(accepted_samples) < 12:
            accepted_samples.append(rendered)

    report = {
        "production_writes": 0,
        "cardmarket_capture": str(capture),
        "regional_products": len(rows),
        "raw_json_top_level_key_counts": dict(sorted(key_counts.items())),
        "raw_json_nested_key_counts": dict(sorted(nested_key_counts.items())),
        "accepted_samples": accepted_samples,
        "residual_products": residual,
        "residual_products_count": len(residual),
    }
    out = os.getenv("YGO_POTE_JP_RAW_JSON_OUTPUT", "/tmp/yugioh-pote-jp-raw-json-keys-v1.json")
    text = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n"
    Path(out).write_text(text, encoding="utf-8")
    print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
