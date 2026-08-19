from __future__ import annotations

import argparse
import json
import os
import re
from collections import defaultdict
from pathlib import Path

import psycopg2
from psycopg2.extras import RealDictCursor

from app.ingest.normalization import normalize_collector_number, normalize_variant
from app.scripts.audit_onepiece_jp_full_surface_v1 import _load_official
from app.scripts.audit_onepiece_p_promo_residual_v1 import load_asia_en_p

GAME="onepiece"
SET_TOKEN="P"
LANGUAGE="ja"
IMAGE_SOURCE="onepiece_official_jp"
IDENTIFIER_SOURCE="onepiece_official_jp"
FULL_SURFACE_SHA256="772684372981c8004acc0b17598f2853118b2ea0c375e5654631b2cfacdf2008"
INITIAL_LOGICAL=105
TARGET_LOGICAL=121
OFFICIAL_LOGICAL=127
OFFICIAL_PHYSICAL=232
TARGET_PHYSICAL=225
INITIAL_CARDS=2665
TARGET_CARDS=2681
EXPECTED_SETS=59
CONFIRM="APPLY_ONEPIECE_JP_P_PROMO_PARTIAL_V1"
SAFE_NEW_NAMES={
 "P-038":"Trafalgar Law","P-040":"Kaido","P-064":"Kouzuki Momonosuke","P-066":"Boa Hancock",
 "P-067":"Eustass\"Captain\"Kid","P-080":"Monkey.D.Luffy","P-086":"Trafalgar Law","P-087":"Nico Robin",
 "P-094":"Roronoa Zoro","P-095":"Sanji","P-108":"Monkey.D.Luffy","P-109":"Portgas.D.Ace",
 "P-114":"Roronoa Zoro","P-116":"Nico Robin","P-118":"Lilith","P-121":"Brook",
}
BLOCKED={"P-110","P-120","P-150","P-151","P-157","P-159"}


def norm_set(v: object)->str: return re.sub(r"[^A-Z0-9]+","",str(v or "").upper())

def connect(*,readonly:bool):
 url=os.getenv("DATABASE_URL_UNPOOLED") or os.getenv("DATABASE_URL")
 if not url: raise RuntimeError("DATABASE URL required")
 c=psycopg2.connect(url,connect_timeout=30,application_name="dontripit_onepiece_p_promo_partial_v1"); c.set_session(readonly=readonly,autocommit=False); return c


def load_source()->tuple[list[dict],dict[str,str]]:
 jp=_load_official()
 if jp["digest"]!=FULL_SURFACE_SHA256: raise RuntimeError({"JP_digest_drift":jp["digest"]})
 rows=list(jp["sets"].get(SET_TOKEN) or []); collectors={r["collector_number"] for r in rows}
 if len(rows)!=OFFICIAL_PHYSICAL or len(collectors)!=OFFICIAL_LOGICAL: raise RuntimeError({"JP_P_surface_drift":{"physical":len(rows),"logical":len(collectors)}})
 asia=load_asia_en_p(); names={}
 for collector,expected_name in SAFE_NEW_NAMES.items():
  candidates=asia.get(collector,[]); actual=sorted({str(r.get("name") or "").strip() for r in candidates if str(r.get("name") or "").strip()})
  if actual!=[expected_name]: raise RuntimeError({"Asia_EN_name_drift":{"collector":collector,"expected":expected_name,"actual":actual}})
  names[collector]=expected_name
 target=[r for r in rows if r["collector_number"] not in BLOCKED]
 if len(target)!=TARGET_PHYSICAL or len({r["collector_number"] for r in target})!=TARGET_LOGICAL: raise RuntimeError("P target surface drift")
 if len({r["source_print_id"] for r in target})!=TARGET_PHYSICAL: raise RuntimeError("P target source-id collision")
 if any(not str(r.get("image_url") or "").startswith("https://www.onepiece-cardgame.com/") for r in target): raise RuntimeError("P target JP image drift")
 return target,names


def build(cur,target:list[dict],names:dict[str,str])->dict:
 cur.execute("SELECT id FROM games WHERE slug=%s LIMIT 1",(GAME,)); game_id=int(cur.fetchone()["id"])
 cur.execute("SELECT count(*) n FROM cards WHERE game_id=%s",(game_id,)); cards_before=int(cur.fetchone()["n"])
 cur.execute("SELECT count(*) n FROM sets WHERE game_id=%s",(game_id,)); sets_before=int(cur.fetchone()["n"])
 if sets_before!=EXPECTED_SETS: raise RuntimeError({"OnePiece_set_count_drift":sets_before})
 if cards_before not in (INITIAL_CARDS,TARGET_CARDS): raise RuntimeError({"OnePiece_card_count_drift":cards_before})
 cur.execute("SELECT id,code FROM sets WHERE game_id=%s",(game_id,)); matches=[dict(r) for r in cur.fetchall() if norm_set(r["code"])==SET_TOKEN]
 if len(matches)!=1: raise RuntimeError({"P_set_not_unique":matches})
 set_id,set_code=int(matches[0]["id"]),str(matches[0]["code"])
 cur.execute("""SELECT p.id print_id,p.card_id,p.collector_number,p.language,p.variant,p.rarity,p.print_key,c.name,c.card_key,
  (SELECT pi.url FROM print_images pi WHERE pi.print_id=p.id ORDER BY pi.is_primary DESC,pi.id ASC LIMIT 1) image_url,
  (SELECT pi.source FROM print_images pi WHERE pi.print_id=p.id ORDER BY pi.is_primary DESC,pi.id ASC LIMIT 1) image_source,
  (SELECT ident.external_id FROM print_identifiers ident WHERE ident.print_id=p.id AND ident.source=%s LIMIT 1) jp_external_id
  FROM prints p JOIN cards c ON c.id=p.card_id WHERE p.set_id=%s ORDER BY p.collector_number,p.language,p.variant,p.id""",(IDENTIFIER_SOURCE,set_id)); db=[dict(r) for r in cur.fetchall()]
 by_collector=defaultdict(set)
 for r in db: by_collector[str(r["collector_number"] or "").upper().strip()].add(int(r["card_id"]))
 collisions={k:sorted(v) for k,v in by_collector.items() if len(v)!=1}
 if collisions: raise RuntimeError({"P_collector_card_collisions":collisions})
 current_collectors=set(by_collector)
 source_collectors={r["collector_number"] for r in target}|BLOCKED
 expected_current=INITIAL_LOGICAL if cards_before==INITIAL_CARDS else TARGET_LOGICAL
 if len(current_collectors)!=expected_current: raise RuntimeError({"P_current_logical_drift":{"cards":cards_before,"collectors":len(current_collectors)}})
 if cards_before==INITIAL_CARDS:
  if source_collectors-current_collectors != set(SAFE_NEW_NAMES)|BLOCKED: raise RuntimeError({"initial_P_residual_drift":sorted(source_collectors-current_collectors)})
 else:
  if source_collectors-current_collectors != BLOCKED: raise RuntimeError({"complete_P_residual_drift":sorted(source_collectors-current_collectors)})

 cur.execute("SELECT id,name,card_key FROM cards WHERE game_id=%s",(game_id,)); all_cards=[dict(r) for r in cur.fetchall()]; cards_by_key=defaultdict(list)
 for r in all_cards: cards_by_key[str(r.get("card_key") or "")].append(r)
 cards_to_create=[]; safe_card_ids={}
 for collector,name in names.items():
  key=f"onepiece:{normalize_collector_number(collector)}"; matches_key=cards_by_key.get(key,[])
  if cards_before==INITIAL_CARDS:
   if matches_key: raise RuntimeError({"safe_card_key_preexists":{collector:matches_key}})
   cards_to_create.append({"collector":collector,"name":name,"card_key":key})
  else:
   if len(matches_key)!=1 or str(matches_key[0]["name"])!=name: raise RuntimeError({"safe_card_identity_drift":{"collector":collector,"matches":matches_key,"expected_name":name}})
   safe_card_ids[collector]=int(matches_key[0]["id"])
   if collector not in by_collector or next(iter(by_collector[collector]))!=safe_card_ids[collector]: raise RuntimeError({"safe_card_not_owned_by_P_print":collector})

 ja=[r for r in db if str(r.get("language") or "").lower()==LANGUAGE]
 if len(ja) not in (0,TARGET_PHYSICAL): raise RuntimeError({"partial_P_JA_surface_blocked":len(ja)})
 target_keys={(r["collector_number"],normalize_variant(r["variant"])) for r in target}
 ja_keys={(str(r["collector_number"]).upper(),normalize_variant(r["variant"])) for r in ja}
 if ja and ja_keys!=target_keys: raise RuntimeError({"P_JA_key_drift":{"only_db":sorted(ja_keys-target_keys),"only_source":sorted(target_keys-ja_keys)}})

 if ja:
  source_by_key={(r["collector_number"],normalize_variant(r["variant"])):r for r in target}; mismatch=[]
  for d in ja:
   key=(str(d["collector_number"]).upper(),normalize_variant(d["variant"])); s=source_by_key[key]
   if str(d.get("rarity") or "")!=str(s.get("rarity") or "") or str(d.get("image_url") or "")!=str(s.get("image_url") or "") or str(d.get("image_source") or "")!=IMAGE_SOURCE or str(d.get("jp_external_id") or "")!=str(s.get("source_print_id") or ""): mismatch.append(key)
  if mismatch: raise RuntimeError({"P_existing_JA_drift":mismatch})

 if not ja:
  ext=[r["source_print_id"] for r in target]; cur.execute("SELECT print_id,external_id FROM print_identifiers WHERE source=%s AND external_id=ANY(%s)",(IDENTIFIER_SOURCE,ext)); claims=[dict(r) for r in cur.fetchall()]
  if claims: raise RuntimeError({"P_JP_identifier_claims_without_surface":claims})
 return {"game_id":game_id,"set_id":set_id,"set_code":set_code,"cards_before":cards_before,"sets_before":sets_before,"cards_to_create":cards_to_create,"safe_card_ids":safe_card_ids,"target":target,"ja_existing":len(ja)}


def run(*,apply:bool,confirm:str="")->dict:
 if apply and confirm!=CONFIRM: raise RuntimeError(f"--apply requires --confirm {CONFIRM}")
 target,names=load_source(); conn=connect(readonly=not apply)
 try:
  with conn.cursor(cursor_factory=RealDictCursor) as cur:
   state=build(cur,target,names)
   report={"mode":"apply" if apply else "dry_run","status":"pass","production_writes":0,"official_surface_sha256":FULL_SURFACE_SHA256,"target_logical_collectors":TARGET_LOGICAL,"target_physical":TARGET_PHYSICAL,"blocked_collectors":sorted(BLOCKED),"cards_before":state["cards_before"],"sets_before":state["sets_before"],"logical_cards_ready":len(state["cards_to_create"]),"existing_exact_ja":state["ja_existing"],"new_ja_prints_ready":0 if state["ja_existing"] else TARGET_PHYSICAL,"created_card_ids":[],"created_print_ids":[]}
   if not apply: conn.rollback(); return report
   card_ids=dict(state["safe_card_ids"]); created_cards=[]
   for row in state["cards_to_create"]:
    cur.execute("INSERT INTO cards(game_id,name,card_key) VALUES(%s,%s,%s) RETURNING id",(state["game_id"],row["name"],row["card_key"])); cid=int(cur.fetchone()["id"]); card_ids[row["collector"]]=cid; created_cards.append(cid)
   cur.execute("SELECT p.card_id,p.collector_number FROM prints p WHERE p.set_id=%s",(state["set_id"],)); owner=defaultdict(set)
   for r in cur.fetchall(): owner[str(r["collector_number"] or "").upper().strip()].add(int(r["card_id"]))
   for collector,ids in owner.items():
    if len(ids)==1: card_ids.setdefault(collector,next(iter(ids)))
   created_prints=[]
   if not state["ja_existing"]:
    for s in state["target"]:
     collector=s["collector_number"]; variant=normalize_variant(s["variant"]); cid=card_ids.get(collector)
     if cid is None: raise RuntimeError({"P_target_card_missing":collector})
     key=f"onepiece:{state['set_code'].lower()}:{normalize_collector_number(collector)}:{LANGUAGE}:{variant}"
     cur.execute("INSERT INTO prints(set_id,card_id,collector_number,language,rarity,is_foil,variant,print_key) VALUES(%s,%s,%s,%s,%s,false,%s,%s) RETURNING id",(state["set_id"],cid,collector,LANGUAGE,s.get("rarity"),variant,key)); pid=int(cur.fetchone()["id"]); created_prints.append(pid)
     cur.execute("INSERT INTO print_images(print_id,url,is_primary,source) VALUES(%s,%s,true,%s)",(pid,s["image_url"],IMAGE_SOURCE)); cur.execute("INSERT INTO print_identifiers(print_id,source,external_id) VALUES(%s,%s,%s)",(pid,IDENTIFIER_SOURCE,s["source_print_id"]))
   cur.execute("SELECT count(*) n FROM cards WHERE game_id=%s",(state["game_id"],)); cards_after=int(cur.fetchone()["n"]); cur.execute("SELECT count(*) n FROM sets WHERE game_id=%s",(state["game_id"],)); sets_after=int(cur.fetchone()["n"])
   if cards_after!=TARGET_CARDS or sets_after!=EXPECTED_SETS: raise RuntimeError({"logical_catalog_post_apply":(cards_after,sets_after)})
   cur.execute("SELECT count(*) n,count(DISTINCT card_id) cards FROM prints WHERE set_id=%s AND lower(coalesce(language,''))=%s",(state["set_id"],LANGUAGE)); x=cur.fetchone()
   if (int(x["n"]),int(x["cards"]))!=(TARGET_PHYSICAL,TARGET_LOGICAL): raise RuntimeError({"P_JA_post_apply":dict(x)})
   report.update({"production_writes":len(created_cards)+len(created_prints)*3,"created_card_ids":created_cards,"created_print_ids":created_prints,"cards_after":cards_after,"sets_after":sets_after,"ja_after":TARGET_PHYSICAL}); conn.commit(); return report
 except Exception: conn.rollback(); raise
 finally: conn.close()


def main()->int:
 p=argparse.ArgumentParser(); p.add_argument("--apply",action="store_true"); p.add_argument("--confirm",default=""); p.add_argument("--report",type=Path,default=Path("/tmp/onepiece-jp-p-promo-partial-v1.json")); a=p.parse_args(); payload=run(apply=a.apply,confirm=a.confirm); a.report.parent.mkdir(parents=True,exist_ok=True); text=json.dumps(payload,ensure_ascii=False,indent=2,sort_keys=True,default=str)+"\n"; a.report.write_text(text,encoding="utf-8"); print(text,end=""); return 0

if __name__=="__main__": raise SystemExit(main())
