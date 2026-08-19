from __future__ import annotations

import json
import os
import re
from pathlib import Path

import psycopg2
from psycopg2.extras import RealDictCursor

from app.ingest.normalization import normalize_variant
from app.scripts.audit_onepiece_jp_full_surface_v1 import _load_official

DIGEST="772684372981c8004acc0b17598f2853118b2ea0c375e5654631b2cfacdf2008"
EXPECTED=154


def norm_set(value: object) -> str:
    return re.sub(r"[^A-Z0-9]+","",str(value or "").upper())


def main()->int:
    official=_load_official()
    if official["digest"]!=DIGEST: raise RuntimeError({"official_digest_drift":official["digest"]})
    source_rows=list(official["sets"].get("OP16") or [])
    source={(r["collector_number"],normalize_variant(r["variant"])):r for r in source_rows}
    if len(source)!=EXPECTED: raise RuntimeError({"official_OP16_full":len(source)})
    url=os.getenv("DATABASE_URL_UNPOOLED") or os.getenv("DATABASE_URL")
    if not url: raise RuntimeError("DATABASE URL required")
    conn=psycopg2.connect(url,connect_timeout=30,application_name="dontripit_op16_jp_full_proof_v2"); conn.set_session(readonly=True,autocommit=False)
    failures=[]; report={"production_writes":0}
    try:
      with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT id FROM games WHERE slug='onepiece' LIMIT 1"); game_id=int(cur.fetchone()["id"])
        cur.execute("SELECT count(*) n FROM cards WHERE game_id=%s",(game_id,)); cards=int(cur.fetchone()["n"])
        cur.execute("SELECT count(*) n FROM sets WHERE game_id=%s",(game_id,)); sets_count=int(cur.fetchone()["n"])
        if cards!=2665: failures.append(f"cards_{cards}")
        if sets_count!=59: failures.append(f"sets_{sets_count}")
        cur.execute("SELECT id,code FROM sets WHERE game_id=%s",(game_id,)); sets=[dict(r) for r in cur.fetchall()]; matches=[r for r in sets if norm_set(r["code"])=="OP16"]
        if len(matches)!=1: raise RuntimeError({"OP16_set":matches})
        set_id=int(matches[0]["id"])
        cur.execute("""SELECT p.id print_id,p.card_id,p.collector_number,p.variant,p.rarity,
          (SELECT pi.url FROM print_images pi WHERE pi.print_id=p.id ORDER BY pi.is_primary DESC,pi.id ASC LIMIT 1) image_url,
          (SELECT pi.source FROM print_images pi WHERE pi.print_id=p.id ORDER BY pi.is_primary DESC,pi.id ASC LIMIT 1) image_source,
          (SELECT ident.external_id FROM print_identifiers ident WHERE ident.print_id=p.id AND ident.source='onepiece_official_jp' LIMIT 1) external_id
          FROM prints p WHERE p.set_id=%s AND lower(coalesce(p.language,''))='ja' ORDER BY p.collector_number,p.variant,p.id""",(set_id,)); rows=[dict(r) for r in cur.fetchall()]
        db={(str(r["collector_number"]).upper(),normalize_variant(r["variant"])):r for r in rows}
        if len(rows)!=EXPECTED: failures.append(f"ja_count_{len(rows)}")
        if set(db)!=set(source): failures.append("physical_keys")
        mismatch=[]
        for key,s in source.items():
          d=db.get(key)
          if not d or str(d.get("rarity") or "")!=str(s.get("rarity") or "") or str(d.get("image_url") or "")!=str(s.get("image_url") or "") or str(d.get("image_source") or "")!='onepiece_official_jp' or str(d.get("external_id") or "")!=str(s.get("source_print_id") or ""):
            mismatch.append(key)
        if mismatch: failures.append(f"live_official_mismatch_{len(mismatch)}")
        ids=[int(r["print_id"]) for r in rows]
        cur.execute("SELECT count(*) n FROM search_documents WHERE doc_type='print' AND object_id=ANY(%s)",(ids,)); search_docs=int(cur.fetchone()["n"])
        if search_docs!=EXPECTED: failures.append(f"search_{search_docs}")
        cur.execute("SELECT count(*) n FROM external_catalog_print_links l JOIN external_catalog_products e ON e.id=l.external_product_id WHERE e.source='cardmarket' AND l.print_id=ANY(%s) AND l.link_status IN ('accepted','mapped','exact')",(ids,)); cm_links=int(cur.fetchone()["n"])
        if cm_links: failures.append(f"cm_links_{cm_links}")
        cur.execute("SELECT count(*) n FROM price_snapshots ps JOIN price_sources src ON src.id=ps.source_id WHERE src.name='cardmarket' AND ps.entity_type='print' AND ps.entity_id=ANY(%s)",(ids,)); cm_prices=int(cur.fetchone()["n"])
        if cm_prices: failures.append(f"cm_prices_{cm_prices}")
        target=[r for r in rows if str(r["collector_number"]).upper()=="OP16-119"]
        target_proof=[{"print_id":int(r["print_id"]),"card_id":int(r["card_id"]),"variant":r["variant"],"rarity":r["rarity"],"image_url":r["image_url"],"external_id":r["external_id"]} for r in sorted(target,key=lambda x:str(x["variant"]))]
        if len(target_proof)!=2 or {r["variant"] for r in target_proof}!={"default","p1"}: failures.append("OP16_119")
        report.update({"status":"pass" if not failures else "fail","failures":failures,"official_surface_sha256":DIGEST,"onepiece_cards":cards,"onepiece_sets":sets_count,"op16_ja_physical":len(rows),"exact_live_official":EXPECTED-len(mismatch),"search_docs":search_docs,"cardmarket_links":cm_links,"cardmarket_prices":cm_prices,"op16_119":target_proof,"mismatch_keys":mismatch}); conn.rollback()
    finally: conn.close()
    out=Path(os.getenv("ONEPIECE_JP_OP16_FULL_PROOF_V2_OUTPUT","/tmp/onepiece-jp-op16-full-production-proof-v2.json")); out.parent.mkdir(parents=True,exist_ok=True); text=json.dumps(report,ensure_ascii=False,indent=2,sort_keys=True,default=str)+"\n"; out.write_text(text,encoding="utf-8"); print(text,end=""); return 0 if report["status"]=="pass" else 2


if __name__=="__main__": raise SystemExit(main())
