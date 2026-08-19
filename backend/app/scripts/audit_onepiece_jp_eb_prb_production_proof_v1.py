from __future__ import annotations

import json
import os
import re
from pathlib import Path

import psycopg2
from psycopg2.extras import RealDictCursor

from app.ingest.normalization import normalize_variant
from app.scripts.audit_onepiece_jp_full_surface_v1 import _load_official

EXPECTED = {"EB01":118,"EB02":93,"EB03":94,"EB04":88,"PRB01":2,"PRB02":39}
TOTAL = 434
DIGEST = "772684372981c8004acc0b17598f2853118b2ea0c375e5654631b2cfacdf2008"


def norm_set(value: object) -> str:
    return re.sub(r"[^A-Z0-9]+", "", str(value or "").upper())


def main() -> int:
    official = _load_official()
    if official["digest"] != DIGEST:
        raise RuntimeError({"official_digest_drift": official["digest"]})
    url = os.getenv("DATABASE_URL_UNPOOLED") or os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE URL required")
    conn = psycopg2.connect(url,connect_timeout=30,application_name="dontripit_opjp_eb_prb_proof_v1")
    conn.set_session(readonly=True,autocommit=False)
    failures=[]; report={"production_writes":0,"sets":{}}
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT id FROM games WHERE slug='onepiece' LIMIT 1"); game_id=int(cur.fetchone()["id"])
            cur.execute("SELECT count(*) n FROM cards WHERE game_id=%s",(game_id,)); cards=int(cur.fetchone()["n"])
            cur.execute("SELECT count(*) n FROM sets WHERE game_id=%s",(game_id,)); set_count=int(cur.fetchone()["n"])
            if cards != 2665: failures.append(f"cards_{cards}")
            if set_count != 59: failures.append(f"sets_{set_count}")
            cur.execute("SELECT id,code FROM sets WHERE game_id=%s",(game_id,)); db_sets=[dict(r) for r in cur.fetchall()]
            set_by_token={norm_set(r["code"]):r for r in db_sets}
            all_ids=[]
            for token, expected_count in EXPECTED.items():
                source_rows=list(official["sets"][token]); source={(r["collector_number"],normalize_variant(r["variant"])):r for r in source_rows}
                db_set=set_by_token[token]; set_id=int(db_set["id"])
                cur.execute("""SELECT p.id print_id,p.card_id,p.collector_number,p.variant,p.rarity,
                  (SELECT pi.url FROM print_images pi WHERE pi.print_id=p.id ORDER BY pi.is_primary DESC,pi.id ASC LIMIT 1) image_url,
                  (SELECT pi.source FROM print_images pi WHERE pi.print_id=p.id ORDER BY pi.is_primary DESC,pi.id ASC LIMIT 1) image_source,
                  (SELECT ident.external_id FROM print_identifiers ident WHERE ident.print_id=p.id AND ident.source='onepiece_official_jp' LIMIT 1) external_id
                  FROM prints p WHERE p.set_id=%s AND lower(coalesce(p.language,''))='ja' ORDER BY p.collector_number,p.variant,p.id""",(set_id,))
                rows=[dict(r) for r in cur.fetchall()]
                if len(rows)!=expected_count: failures.append(f"{token}_count_{len(rows)}")
                db={(str(r["collector_number"]).upper(),normalize_variant(r["variant"])):r for r in rows}
                if set(db)!=set(source): failures.append(f"{token}_keys")
                exact=0; mismatch=[]
                for key,s in source.items():
                    d=db.get(key)
                    if d and str(d.get("rarity") or "")==str(s.get("rarity") or "") and str(d.get("image_url") or "")==str(s.get("image_url") or "") and str(d.get("image_source") or "")=="onepiece_official_jp" and str(d.get("external_id") or "")==str(s.get("source_print_id") or ""):
                        exact+=1
                    else: mismatch.append(key)
                if mismatch: failures.append(f"{token}_mismatch_{len(mismatch)}")
                ids=[int(r["print_id"]) for r in rows]; all_ids.extend(ids)
                report["sets"][token]={"ja_prints":len(rows),"exact_live_official":exact}
            if len(all_ids)!=TOTAL: failures.append(f"total_{len(all_ids)}")
            cur.execute("SELECT count(*) n FROM search_documents WHERE doc_type='print' AND object_id=ANY(%s)",(all_ids,)); search_docs=int(cur.fetchone()["n"])
            if search_docs!=TOTAL: failures.append(f"search_{search_docs}")
            cur.execute("""SELECT count(*) n FROM external_catalog_print_links l JOIN external_catalog_products e ON e.id=l.external_product_id WHERE e.source='cardmarket' AND l.print_id=ANY(%s) AND l.link_status IN ('accepted','mapped','exact')""",(all_ids,)); cm_links=int(cur.fetchone()["n"])
            if cm_links: failures.append(f"cm_links_{cm_links}")
            cur.execute("""SELECT count(*) n FROM price_snapshots ps JOIN price_sources src ON src.id=ps.source_id WHERE src.name='cardmarket' AND ps.entity_type='print' AND ps.entity_id=ANY(%s)""",(all_ids,)); cm_prices=int(cur.fetchone()["n"])
            if cm_prices: failures.append(f"cm_prices_{cm_prices}")
            report.update({"status":"pass" if not failures else "fail","failures":failures,"official_surface_sha256":DIGEST,"onepiece_cards":cards,"onepiece_sets":set_count,"ja_prints":len(all_ids),"search_docs":search_docs,"cardmarket_links":cm_links,"cardmarket_prices":cm_prices})
            conn.rollback()
    finally: conn.close()
    out=Path(os.getenv("ONEPIECE_JP_EB_PRB_PROOF_OUTPUT","/tmp/onepiece-jp-eb-prb-production-proof-v1.json")); out.parent.mkdir(parents=True,exist_ok=True); text=json.dumps(report,ensure_ascii=False,indent=2,sort_keys=True,default=str)+"\n"; out.write_text(text,encoding="utf-8"); print(text,end=""); return 0 if report["status"]=="pass" else 2


if __name__=="__main__": raise SystemExit(main())
