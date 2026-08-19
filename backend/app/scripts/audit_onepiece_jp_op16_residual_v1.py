from __future__ import annotations

import json
import os
import re
from collections import defaultdict
from pathlib import Path

import psycopg2
from psycopg2.extras import RealDictCursor

from app.ingest.normalization import normalize_variant
from app.scripts.audit_onepiece_jp_full_surface_v1 import _load_official

SET_TOKEN = "OP16"
EXPECTED_OFFICIAL_FULL = 154
EXPECTED_EXISTING_JA = 149
EXPECTED_RESIDUAL = 5


def _norm_set(value: object) -> str:
    return re.sub(r"[^A-Z0-9]+", "", str(value or "").upper())


def main() -> int:
    official = _load_official()
    source_rows = list(official["sets"].get(SET_TOKEN) or [])
    if len(source_rows) != EXPECTED_OFFICIAL_FULL:
        raise RuntimeError({"OP16_full_source_count_drift": len(source_rows)})
    source_by_key = {
        (row["collector_number"], normalize_variant(row["variant"])): row
        for row in source_rows
    }
    if len(source_by_key) != EXPECTED_OFFICIAL_FULL:
        raise RuntimeError("OP16 official full-source keys are not unique")

    url = os.getenv("DATABASE_URL_UNPOOLED") or os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL_UNPOOLED or DATABASE_URL is required")
    conn = psycopg2.connect(url, connect_timeout=30, application_name="dontripit_op16_jp_residual_v1")
    conn.set_session(readonly=True, autocommit=False)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT id FROM games WHERE slug='onepiece' LIMIT 1")
            game_id = int(cur.fetchone()["id"])
            cur.execute("SELECT id,code FROM sets WHERE game_id=%s", (game_id,))
            sets = [dict(row) for row in cur.fetchall()]
            matches = [row for row in sets if _norm_set(row["code"]) == SET_TOKEN]
            if len(matches) != 1:
                raise RuntimeError({"OP16_set_not_unique": matches})
            set_id = int(matches[0]["id"])
            set_code = str(matches[0]["code"])

            cur.execute(
                """SELECT p.id print_id,p.card_id,p.collector_number,p.language,p.variant,p.rarity,p.print_key,
                          c.name card_name,
                          (SELECT pi.url FROM print_images pi WHERE pi.print_id=p.id ORDER BY pi.is_primary DESC,pi.id ASC LIMIT 1) image_url,
                          (SELECT pi.source FROM print_images pi WHERE pi.print_id=p.id ORDER BY pi.is_primary DESC,pi.id ASC LIMIT 1) image_source,
                          (SELECT ident.external_id FROM print_identifiers ident WHERE ident.print_id=p.id AND ident.source='onepiece_official_jp' LIMIT 1) jp_external_id
                   FROM prints p JOIN cards c ON c.id=p.card_id
                   WHERE p.set_id=%s AND lower(coalesce(p.language,''))='ja'
                   ORDER BY p.collector_number,p.variant,p.id""",
                (set_id,),
            )
            ja = [dict(row) for row in cur.fetchall()]
            if len(ja) != EXPECTED_EXISTING_JA:
                raise RuntimeError({"OP16_existing_JA_count_drift": len(ja)})

            cur.execute(
                """SELECT p.card_id,p.collector_number,c.name card_name,count(*) print_count
                   FROM prints p JOIN cards c ON c.id=p.card_id
                   WHERE p.set_id=%s
                   GROUP BY p.card_id,p.collector_number,c.name""",
                (set_id,),
            )
            cards = [dict(row) for row in cur.fetchall()]
            conn.rollback()
    finally:
        conn.close()

    ja_by_key = {
        (str(row["collector_number"]).upper(), normalize_variant(row["variant"])): row
        for row in ja
    }
    extra_db = sorted(set(ja_by_key) - set(source_by_key))
    residual_keys = sorted(set(source_by_key) - set(ja_by_key))
    if extra_db:
        raise RuntimeError({"JA_rows_not_in_live_official_full_surface": extra_db})
    if len(residual_keys) != EXPECTED_RESIDUAL:
        raise RuntimeError({"OP16_residual_count_drift": {"expected": EXPECTED_RESIDUAL, "actual": len(residual_keys), "keys": residual_keys}})

    card_ids_by_collector = defaultdict(set)
    card_names_by_collector = defaultdict(set)
    for row in cards:
        collector = str(row["collector_number"] or "").upper().strip()
        card_ids_by_collector[collector].add(int(row["card_id"]))
        card_names_by_collector[collector].add(str(row["card_name"]))

    existing_exact = 0
    existing_mismatches = []
    for key, db in ja_by_key.items():
        source = source_by_key[key]
        if (
            str(db.get("rarity") or "") == str(source.get("rarity") or "")
            and str(db.get("image_url") or "") == str(source.get("image_url") or "")
            and str(db.get("image_source") or "") == "onepiece_official_jp"
            and str(db.get("jp_external_id") or "") == str(source.get("source_print_id") or "")
        ):
            existing_exact += 1
        else:
            existing_mismatches.append({"key": key, "db": db, "official": source})
    if existing_mismatches:
        raise RuntimeError({"existing_149_no_longer_exact": existing_mismatches})

    residual = []
    for key in residual_keys:
        source = source_by_key[key]
        collector = source["collector_number"]
        card_ids = sorted(card_ids_by_collector.get(collector, set()))
        if len(card_ids) != 1:
            raise RuntimeError({"residual_collector_card_identity_not_unique": {"key": key, "card_ids": card_ids}})
        if not str(source.get("image_url") or "").startswith("https://www.onepiece-cardgame.com/"):
            raise RuntimeError({"residual_missing_or_nonofficial_image": source})
        residual.append(
            {
                "collector_number": collector,
                "variant": normalize_variant(source["variant"]),
                "source_print_id": source["source_print_id"],
                "rarity": source.get("rarity"),
                "image_url": source["image_url"],
                "series_ids": source.get("series_ids") or [],
                "series_labels": source.get("series_labels") or [],
                "card_id": card_ids[0],
                "card_names": sorted(card_names_by_collector[collector]),
                "print_key": f"onepiece:{set_code.lower()}:{re.sub(r'[^a-z0-9]+','',collector.lower())}:ja:{normalize_variant(source['variant'])}",
            }
        )

    report = {
        "status": "pass",
        "production_writes": 0,
        "official_surface_sha256": official["digest"],
        "official_op16_full_physical": len(source_by_key),
        "existing_ja_physical": len(ja_by_key),
        "existing_ja_exact_live_official_matches": existing_exact,
        "residual_count": len(residual),
        "residual": residual,
    }
    out = Path(os.getenv("ONEPIECE_JP_OP16_RESIDUAL_OUTPUT", "/tmp/onepiece-jp-op16-residual-v1.json"))
    out.parent.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n"
    out.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
