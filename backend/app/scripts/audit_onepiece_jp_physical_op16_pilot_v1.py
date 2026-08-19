from __future__ import annotations

import json
import os
import re
from collections import Counter
from pathlib import Path
from urllib.parse import urlparse

import psycopg2
import requests
from psycopg2.extras import RealDictCursor

from app.ingest.connectors.onepiece_v2 import OnePieceV2Connector

JP_BASE = "https://www.onepiece-cardgame.com/cardlist/"
TARGET_SET = "OP-16"
TARGET_COLLECTOR = "OP16-119"


def _norm_label(value: str) -> str:
    return re.sub(r"[^A-Z0-9]+", "", str(value or "").upper())


def _connect():
    url = os.getenv("DATABASE_URL_UNPOOLED") or os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL_UNPOOLED or DATABASE_URL is required")
    conn = psycopg2.connect(url, connect_timeout=30, application_name="dontripit_onepiece_jp_op16_pilot_v1")
    conn.set_session(readonly=True, autocommit=False)
    return conn


def main() -> int:
    headers = {"User-Agent": "TCGCatalogV2/1.0 (+https://github.com/Alerugg/dontripit)"}
    timeout = float(os.getenv("ONEPIECE_HTTP_TIMEOUT", "30"))
    connector = OnePieceV2Connector()

    index = requests.get(JP_BASE, timeout=timeout, headers=headers)
    index.raise_for_status()
    series_options = connector._parse_official_series_options(index.text)
    if not series_options:
        raise RuntimeError("Japanese official cardlist returned zero series options")

    direct_candidates = [
        (series_id, label)
        for series_id, label in series_options
        if "OP16" in _norm_label(label)
    ]
    candidates = direct_candidates or series_options

    selected_series = []
    entries = []
    scanned = 0
    for series_id, label in candidates:
        scanned += 1
        response = requests.get(f"{JP_BASE}?series={series_id}", timeout=timeout, headers=headers)
        response.raise_for_status()
        parsed = connector._parse_official_cards_page(response.text, base_url=JP_BASE)
        target_entries = [row for row in parsed if str(row.get("set_code") or "").upper() == TARGET_SET]
        if target_entries:
            selected_series.append(
                {
                    "series_id": str(series_id),
                    "label": label,
                    "parsed_entries": len(parsed),
                    "op16_entries": len(target_entries),
                }
            )
            entries.extend({**row, "series_id": str(series_id), "series_label": label} for row in target_entries)
        # If the index label explicitly identified OP16, inspect every matching
        # candidate because multiple official releases can expose variants.
        # On fallback crawling, stop after finding the first OP16 release to keep
        # this pilot conservative and low-impact.
        if target_entries and not direct_candidates:
            break

    if not entries:
        raise RuntimeError(
            json.dumps(
                {
                    "official_jp_op16_not_found": True,
                    "series_option_count": len(series_options),
                    "direct_candidate_count": len(direct_candidates),
                    "series_scanned": scanned,
                },
                ensure_ascii=False,
            )
        )

    identity_rows = {}
    duplicate_identity_drift = []
    for row in entries:
        key = (
            str(row.get("collector_number") or "").upper(),
            str(row.get("variant") or "default").lower(),
        )
        current = identity_rows.get(key)
        normalized = {
            "collector_number": key[0],
            "variant": key[1],
            "print_id": str(row.get("print_id") or ""),
            "name": str(row.get("name") or ""),
            "rarity": row.get("rarity"),
            "image_url": str(row.get("image_url") or ""),
            "series_id": str(row.get("series_id") or ""),
            "series_label": str(row.get("series_label") or ""),
            "details": row.get("details") or {},
        }
        if current is None:
            identity_rows[key] = normalized
        elif (
            current["image_url"] != normalized["image_url"]
            or current["rarity"] != normalized["rarity"]
            or current["name"] != normalized["name"]
        ):
            duplicate_identity_drift.append({"identity": key, "first": current, "other": normalized})

    physical = list(identity_rows.values())
    target_119 = [row for row in physical if row["collector_number"] == TARGET_COLLECTOR]
    if not target_119:
        raise RuntimeError("Official Japanese OP16 surface does not contain OP16-119")

    image_hosts = Counter(urlparse(row["image_url"]).netloc for row in physical if row["image_url"])
    parsed_effects = sum(1 for row in physical if str((row.get("details") or {}).get("effect") or "").strip())
    parsed_triggers = sum(1 for row in physical if str((row.get("details") or {}).get("trigger") or "").strip())

    conn = _connect()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT id FROM games WHERE slug='onepiece' LIMIT 1")
            game_id = int(cur.fetchone()["id"])
            cur.execute(
                """SELECT p.id print_id,p.card_id,p.collector_number,p.variant,p.rarity,p.language,
                          p.image_url,c.name card_name,s.code set_code
                   FROM prints p
                   JOIN cards c ON c.id=p.card_id
                   JOIN sets s ON s.id=p.set_id
                   WHERE c.game_id=%s AND upper(replace(coalesce(s.code,''),'_','-')) IN ('OP16','OP-16')
                   ORDER BY p.collector_number,p.variant,p.id""",
                (game_id,),
            )
            neon_op16 = [dict(row) for row in cur.fetchall()]
            cur.execute(
                """SELECT p.id print_id,p.card_id,p.collector_number,p.variant,p.rarity,p.language,
                          p.image_url,c.name card_name,s.code set_code
                   FROM prints p
                   JOIN cards c ON c.id=p.card_id
                   JOIN sets s ON s.id=p.set_id
                   WHERE c.game_id=%s AND upper(replace(coalesce(p.collector_number,''),'_','-'))=%s
                   ORDER BY p.language,p.variant,p.id""",
                (game_id, TARGET_COLLECTOR),
            )
            neon_119 = [dict(row) for row in cur.fetchall()]
            conn.rollback()
    finally:
        conn.close()

    current_keys = {
        (
            str(row.get("collector_number") or "").upper(),
            str(row.get("language") or "").lower(),
            str(row.get("variant") or "default").lower(),
        )
        for row in neon_op16
    }
    proposed_ja_keys = {
        (row["collector_number"], "ja", row["variant"])
        for row in physical
    }
    logical_collectors_neon = {str(row.get("collector_number") or "").upper() for row in neon_op16}
    logical_collectors_jp = {row["collector_number"] for row in physical}

    report = {
        "status": "pass",
        "production_writes": 0,
        "source": "onepiece_official_jp",
        "base_url": JP_BASE,
        "target_set": TARGET_SET,
        "series_option_count": len(series_options),
        "direct_op16_series_candidates": len(direct_candidates),
        "series_scanned": scanned,
        "selected_series": selected_series,
        "official_op16_raw_entries": len(entries),
        "official_op16_unique_physical_identities": len(physical),
        "official_op16_logical_collectors": len(logical_collectors_jp),
        "official_op16_duplicate_identity_drift": duplicate_identity_drift,
        "official_op16_missing_image_count": sum(1 for row in physical if not row["image_url"]),
        "official_image_hosts": dict(image_hosts),
        "english_detail_parser_effect_count_on_jp_html": parsed_effects,
        "english_detail_parser_trigger_count_on_jp_html": parsed_triggers,
        "neon_op16_prints": len(neon_op16),
        "neon_op16_language_counts": dict(Counter(str(row.get("language") or "") for row in neon_op16)),
        "neon_op16_logical_collectors": len(logical_collectors_neon),
        "logical_collector_overlap": len(logical_collectors_jp & logical_collectors_neon),
        "logical_collectors_only_official_jp": sorted(logical_collectors_jp - logical_collectors_neon),
        "logical_collectors_only_neon": sorted(logical_collectors_neon - logical_collectors_jp),
        "proposed_ja_physical_keys": len(proposed_ja_keys),
        "proposed_ja_keys_already_present": len(proposed_ja_keys & current_keys),
        "proposed_ja_keys_new": len(proposed_ja_keys - current_keys),
        "op16_119_official_jp": target_119,
        "op16_119_neon_before": neon_119,
    }

    out = Path(os.getenv("ONEPIECE_JP_OP16_PILOT_OUTPUT", "/tmp/onepiece-jp-physical-op16-pilot-v1.json"))
    out.parent.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n"
    out.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
