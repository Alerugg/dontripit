from __future__ import annotations

import json
import os
import re
from collections import defaultdict
from pathlib import Path

import psycopg2
import requests
from psycopg2.extras import RealDictCursor

from app.ingest.connectors.onepiece_v2 import OnePieceV2Connector
from app.ingest.normalization import normalize_collector_number, normalize_variant
from app.scripts.audit_onepiece_jp_full_surface_v1 import _load_official

JP_BASE = "https://www.onepiece-cardgame.com/cardlist/"
EN_BASE = "https://en.onepiece-cardgame.com/cardlist/"
SET_TOKEN = "P"
EXPECTED_JP_LOGICAL = 127
EXPECTED_NEON_LOGICAL = 105
EXPECTED_RESIDUAL = 22


def norm_set(value: object) -> str:
    return re.sub(r"[^A-Z0-9]+", "", str(value or "").upper())


def load_en_p() -> dict[str, list[dict]]:
    connector = OnePieceV2Connector()
    timeout = float(os.getenv("ONEPIECE_HTTP_TIMEOUT", "30"))
    session = requests.Session()
    session.headers.update({"User-Agent":"TCGCatalogV2/1.0 (+https://github.com/Alerugg/dontripit)"})
    index = session.get(EN_BASE,timeout=timeout); index.raise_for_status()
    options = connector._parse_official_series_options(index.text)
    by_collector=defaultdict(list)
    for series_id,label in options:
        r=session.get(f"{EN_BASE}?series={series_id}",timeout=timeout); r.raise_for_status()
        for row in connector._parse_official_cards_page(r.text,base_url=EN_BASE):
            if norm_set(row.get("set_code")) != SET_TOKEN:
                continue
            by_collector[str(row.get("collector_number") or "").upper().strip()].append({
                "collector_number":str(row.get("collector_number") or "").upper().strip(),
                "variant":normalize_variant(row.get("variant")),
                "source_print_id":str(row.get("print_id") or "").upper().strip(),
                "name":str(row.get("name") or "").strip(),
                "rarity":str(row.get("rarity") or "").strip() or None,
                "image_url":str(row.get("image_url") or "").strip(),
                "series_id":str(series_id),
                "series_label":label,
            })
    return dict(by_collector)


def main() -> int:
    jp=_load_official(); jp_rows=list(jp["sets"].get(SET_TOKEN) or [])
    jp_by_collector=defaultdict(list)
    for row in jp_rows: jp_by_collector[row["collector_number"]].append(row)
    if len(jp_by_collector)!=EXPECTED_JP_LOGICAL:
        raise RuntimeError({"JP_P_logical_drift":len(jp_by_collector)})

    url=os.getenv("DATABASE_URL_UNPOOLED") or os.getenv("DATABASE_URL")
    if not url: raise RuntimeError("DATABASE URL required")
    conn=psycopg2.connect(url,connect_timeout=30,application_name="dontripit_p_promo_residual_v1"); conn.set_session(readonly=True,autocommit=False)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT id FROM games WHERE slug='onepiece' LIMIT 1"); game_id=int(cur.fetchone()["id"])
            cur.execute("SELECT id,code FROM sets WHERE game_id=%s",(game_id,)); sets=[dict(r) for r in cur.fetchall()]; matches=[r for r in sets if norm_set(r["code"])==SET_TOKEN]
            if len(matches)!=1: raise RuntimeError({"P_set_not_unique":matches})
            set_id=int(matches[0]["id"])
            cur.execute("""SELECT p.card_id,p.collector_number,p.language,p.variant,c.name,c.card_key
              FROM prints p JOIN cards c ON c.id=p.card_id WHERE p.set_id=%s ORDER BY p.collector_number,p.language,p.variant""",(set_id,)); db_rows=[dict(r) for r in cur.fetchall()]
            neon_collectors={str(r["collector_number"] or "").upper().strip() for r in db_rows}
            cur.execute("SELECT id,name,card_key FROM cards WHERE game_id=%s",(game_id,)); all_cards=[dict(r) for r in cur.fetchall()]
            conn.rollback()
    finally: conn.close()
    if len(neon_collectors)!=EXPECTED_NEON_LOGICAL: raise RuntimeError({"Neon_P_logical_drift":len(neon_collectors)})
    residual=sorted(set(jp_by_collector)-neon_collectors)
    if len(residual)!=EXPECTED_RESIDUAL: raise RuntimeError({"P_residual_drift":{"count":len(residual),"collectors":residual}})

    en_by_collector=load_en_p()
    card_by_key=defaultdict(list)
    for card in all_cards: card_by_key[str(card.get("card_key") or "")].append(card)
    rows=[]; safe=[]; blocked=[]
    for collector in residual:
        jp_variants=jp_by_collector[collector]
        en_variants=en_by_collector.get(collector,[])
        en_names=sorted({str(r.get("name") or "").strip() for r in en_variants if str(r.get("name") or "").strip()})
        expected_key=f"onepiece:{normalize_collector_number(collector)}"
        existing_logical=card_by_key.get(expected_key,[])
        reason=None
        if existing_logical: reason="card_key_already_exists_outside_P_set"
        elif not en_variants: reason="missing_from_official_EN_surface"
        elif len(en_names)!=1: reason="official_EN_name_not_unique"
        elif any(not str(r.get("image_url") or "").startswith("https://www.onepiece-cardgame.com/") for r in jp_variants): reason="JP_image_missing_or_nonofficial"
        item={
          "collector_number":collector,"expected_card_key":expected_key,"official_en_names":en_names,
          "official_en_physical":len(en_variants),"official_jp_physical":len(jp_variants),
          "official_en_variants":sorted({r["variant"] for r in en_variants}),"official_jp_variants":sorted({normalize_variant(r["variant"]) for r in jp_variants}),
          "existing_card_key_matches":existing_logical,
          "jp": [{"variant":normalize_variant(r["variant"]),"source_print_id":r["source_print_id"],"rarity":r.get("rarity"),"image_url":r["image_url"],"series_ids":r.get("series_ids") or [],"series_labels":r.get("series_labels") or []} for r in jp_variants],
          "en": en_variants,"status":"safe_logical_create" if reason is None else "blocked","blocked_reason":reason,
        }
        rows.append(item)
        (safe if reason is None else blocked).append(collector if reason is None else {"collector_number":collector,"reason":reason})
    report={
      "status":"pass","production_writes":0,"jp_full_surface_sha256":jp["digest"],
      "jp_P_physical":len(jp_rows),"jp_P_logical":len(jp_by_collector),"neon_P_logical":len(neon_collectors),"residual_count":len(residual),
      "official_EN_P_logical":len(en_by_collector),"safe_logical_create_count":len(safe),"safe_logical_create_collectors":safe,"blocked_count":len(blocked),"blocked":blocked,"residual":rows,
    }
    out=Path(os.getenv("ONEPIECE_P_PROMO_RESIDUAL_OUTPUT","/tmp/onepiece-p-promo-residual-v1.json")); out.parent.mkdir(parents=True,exist_ok=True); text=json.dumps(report,ensure_ascii=False,indent=2,sort_keys=True,default=str)+"\n"; out.write_text(text,encoding="utf-8"); print(text,end=""); return 0


if __name__=="__main__": raise SystemExit(main())
