from __future__ import annotations

import json
import os
import unicodedata
from collections import defaultdict
from pathlib import Path

import psycopg2
from psycopg2.extras import RealDictCursor

GAME="yugioh"; LANGUAGE="ja"; ACCEPTED=("accepted","mapped","exact")
CERTIFIED={
 "DAMA":"4537","LIOV":"4540","BLVO":"4546","ROTD":"4555",
 "ETCO":"4563","IGAS":"4570","CHIM":"4577","RIRA":"4583",
}


def norm(v):
 text=unicodedata.normalize("NFKD",str(v or "")).casefold()
 return "".join(ch for ch in text if ch.isalnum())


def connect():
 url=os.getenv("DATABASE_URL_UNPOOLED") or os.getenv("DATABASE_URL")
 if not url: raise RuntimeError("DATABASE URL required")
 c=psycopg2.connect(url,connect_timeout=30,application_name="dontripit_ygo_ocg_singletons_v2"); c.set_session(readonly=True,autocommit=False); return c


def main()->int:
 conn=connect()
 try:
  with conn.cursor(cursor_factory=RealDictCursor) as cur:
   cur.execute("SELECT id FROM games WHERE slug=%s LIMIT 1",(GAME,)); gid=int(cur.fetchone()['id'])
   cur.execute("SELECT max(last_seen_at) capture FROM external_catalog_products WHERE source='cardmarket' AND game_id=%s",(gid,)); capture=cur.fetchone()['capture']
   cur.execute("""SELECT e.metacard_external_id,p.card_id FROM external_catalog_print_links l
    JOIN external_catalog_products e ON e.id=l.external_product_id JOIN prints p ON p.id=l.print_id
    WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single' AND e.metacard_external_id IS NOT NULL
      AND l.link_status=ANY(%s) GROUP BY e.metacard_external_id,p.card_id""",(gid,list(ACCEPTED)))
   meta_cards=defaultdict(set)
   for r in cur.fetchall(): meta_cards[str(r['metacard_external_id'])].add(int(r['card_id']))
   cur.execute("""SELECT l.external_product_id,l.print_id FROM external_catalog_print_links l
    JOIN external_catalog_products e ON e.id=l.external_product_id
    WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single' AND l.link_status=ANY(%s)""",(gid,list(ACCEPTED)))
   by_product=defaultdict(set); by_print=defaultdict(set)
   for r in cur.fetchall(): by_product[int(r['external_product_id'])].add(int(r['print_id'])); by_print[int(r['print_id'])].add(int(r['external_product_id']))
   cur.execute("SELECT count(*) n FROM prints p JOIN cards c ON c.id=p.card_id WHERE c.game_id=%s AND lower(coalesce(p.language,''))='ja'",(gid,)); ja=int(cur.fetchone()['n']); assert ja==36426,ja
   reports=[]; allpairs=[]
   for set_code,exp in CERTIFIED.items():
    cur.execute("""SELECT e.id external_product_id,e.external_id id_product,e.name,e.metacard_external_id
      FROM external_catalog_products e WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single'
      AND e.expansion_external_id=%s AND e.last_seen_at=%s ORDER BY e.metacard_external_id,e.external_id::bigint""",(gid,exp,capture)); products=[dict(r) for r in cur.fetchall()]
    cur.execute("""SELECT p.id print_id,p.card_id,p.collector_number,p.rarity,p.variant,c.name card_name
      FROM prints p JOIN cards c ON c.id=p.card_id JOIN sets s ON s.id=p.set_id
      WHERE c.game_id=%s AND upper(coalesce(s.code,''))=%s AND lower(coalesce(p.language,''))='ja'
      ORDER BY p.card_id,p.collector_number,p.id""",(gid,set_code)); prints=[dict(r) for r in cur.fetchall()]
    if not products or not prints: raise RuntimeError({'missing_surface':set_code})
    p_by_meta=defaultdict(list)
    for r in products: p_by_meta[str(r.get('metacard_external_id') or '')].append(r)
    prints_by_card=defaultdict(list); name_cards=defaultdict(set); cards=set()
    for r in prints:
     cid=int(r['card_id']); cards.add(cid); prints_by_card[cid].append(r); name_cards[norm(r['card_name'])].add(cid)
    pairs=[]; blocked=defaultdict(int)
    for meta,grp in p_by_meta.items():
     if not meta or len(grp)!=1: blocked['non_singleton_product_group']+=1; continue
     product=grp[0]; globals=meta_cards.get(meta,set()); cid=None; method=None
     if len(globals)==1 and next(iter(globals)) in cards: cid=next(iter(globals)); method='unique_global_metacard'
     elif len(globals)>1:
      inter=set(globals)&cards&name_cards.get(norm(product.get('name')),set())
      if len(inter)==1: cid=next(iter(inter)); method='metacard_set_name_intersection'
     if cid is None: blocked['unresolved_metacard']+=1; continue
     cprints=prints_by_card[cid]
     if len(cprints)!=1: blocked['multi_physical_card_group']+=1; continue
     pr=cprints[0]
     if norm(product.get('name'))!=norm(pr.get('card_name')): blocked['name_mismatch']+=1; continue
     eid=int(product['external_product_id']); pid=int(pr['print_id'])
     product_claims=by_product.get(eid,set()); print_claims=by_print.get(pid,set())
     if any(x!=pid for x in product_claims) or any(x!=eid for x in print_claims): blocked['accepted_claim_conflict']+=1; continue
     pairs.append({'set_code':set_code,'idExpansion':exp,'external_product_id':eid,'idProduct':str(product['id_product']),'idMetacard':meta,'print_id':pid,'card_id':cid,'card_name':str(pr['card_name']),'collector_number':str(pr['collector_number']),'rarity':pr.get('rarity'),'variant':pr.get('variant'),'method':method,'already_same':pid in product_claims})
    if len({x['external_product_id'] for x in pairs})!=len(pairs) or len({x['print_id'] for x in pairs})!=len(pairs): raise RuntimeError({'not_one_to_one':set_code})
    reports.append({'set_code':set_code,'idExpansion':exp,'products':len(products),'canonical_ja_prints':len(prints),'canonical_cards':len(cards),'pairs':len(pairs),'existing_same':sum(x['already_same'] for x in pairs),'new_ready':sum(not x['already_same'] for x in pairs),'blocked':dict(sorted(blocked.items())),'pairs_detail':pairs})
    allpairs.extend(pairs)
   conn.rollback()
 finally: conn.close()
 if len({x['external_product_id'] for x in allpairs})!=len(allpairs) or len({x['print_id'] for x in allpairs})!=len(allpairs): raise RuntimeError('global_not_one_to_one')
 payload={'status':'pass','mode':'read_only','production_writes':0,'ja_baseline':ja,'cardmarket_capture':str(capture),'certified_expansions':CERTIFIED,'sets':reports,'total_pairs':len(allpairs),'total_existing_same':sum(x['already_same'] for x in allpairs),'total_new_ready':sum(not x['already_same'] for x in allpairs)}
 out=Path('/tmp/yugioh-ocg-certified-singletons-v2.json'); out.write_text(json.dumps(payload,ensure_ascii=False,indent=2,sort_keys=True,default=str)+'\n',encoding='utf-8'); print(json.dumps(payload,ensure_ascii=False,indent=2,sort_keys=True,default=str)); return 0

if __name__=='__main__': raise SystemExit(main())
