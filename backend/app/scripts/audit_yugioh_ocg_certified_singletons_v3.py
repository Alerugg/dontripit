from __future__ import annotations

import json,os,unicodedata
from collections import defaultdict
from pathlib import Path
import psycopg2
from psycopg2.extras import RealDictCursor

GAME='yugioh'; ACCEPTED=('accepted','mapped','exact')
CERTIFIED={'PHRA':'4550','SAST':'4605','DANE':'4594','SOFU':'4611'}

def norm(v):
 text=unicodedata.normalize('NFKD',str(v or '')).casefold(); return ''.join(ch for ch in text if ch.isalnum())

def main():
 url=os.getenv('DATABASE_URL_UNPOOLED') or os.getenv('DATABASE_URL')
 if not url: raise RuntimeError('DATABASE URL required')
 c=psycopg2.connect(url,connect_timeout=30,application_name='dontripit_ygo_ocg_singletons_v3'); c.set_session(readonly=True,autocommit=False)
 try:
  with c.cursor(cursor_factory=RealDictCursor) as cur:
   cur.execute("SELECT id FROM games WHERE slug=%s LIMIT 1",(GAME,)); gid=int(cur.fetchone()['id'])
   cur.execute("SELECT max(last_seen_at) capture FROM external_catalog_products WHERE source='cardmarket' AND game_id=%s",(gid,)); capture=cur.fetchone()['capture']
   cur.execute("""SELECT e.metacard_external_id,p.card_id FROM external_catalog_print_links l JOIN external_catalog_products e ON e.id=l.external_product_id JOIN prints p ON p.id=l.print_id WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single' AND e.metacard_external_id IS NOT NULL AND l.link_status=ANY(%s) GROUP BY e.metacard_external_id,p.card_id""",(gid,list(ACCEPTED)))
   meta=defaultdict(set)
   for r in cur.fetchall(): meta[str(r['metacard_external_id'])].add(int(r['card_id']))
   cur.execute("""SELECT l.external_product_id,l.print_id FROM external_catalog_print_links l JOIN external_catalog_products e ON e.id=l.external_product_id WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single' AND l.link_status=ANY(%s)""",(gid,list(ACCEPTED)))
   byp=defaultdict(set); byr=defaultdict(set)
   for r in cur.fetchall(): byp[int(r['external_product_id'])].add(int(r['print_id'])); byr[int(r['print_id'])].add(int(r['external_product_id']))
   cur.execute("SELECT count(*) n FROM prints p JOIN cards c ON c.id=p.card_id WHERE c.game_id=%s AND lower(coalesce(p.language,''))='ja'",(gid,)); ja=int(cur.fetchone()['n']); assert ja==36426,ja
   reports=[]; allpairs=[]
   for code,exp in CERTIFIED.items():
    cur.execute("""SELECT e.id external_product_id,e.external_id id_product,e.name,e.metacard_external_id FROM external_catalog_products e WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single' AND e.expansion_external_id=%s AND e.last_seen_at=%s ORDER BY e.metacard_external_id,e.external_id::bigint""",(gid,exp,capture)); products=[dict(r) for r in cur.fetchall()]
    cur.execute("""SELECT p.id print_id,p.card_id,p.collector_number,p.rarity,p.variant,c.name card_name FROM prints p JOIN cards c ON c.id=p.card_id JOIN sets s ON s.id=p.set_id WHERE c.game_id=%s AND upper(coalesce(s.code,''))=%s AND lower(coalesce(p.language,''))='ja' ORDER BY p.card_id,p.collector_number,p.id""",(gid,code)); prints=[dict(r) for r in cur.fetchall()]
    pg=defaultdict(list); pc=defaultdict(list); nc=defaultdict(set); cards=set()
    for x in products: pg[str(x.get('metacard_external_id') or '')].append(x)
    for x in prints:
     cid=int(x['card_id']); cards.add(cid); pc[cid].append(x); nc[norm(x['card_name'])].add(cid)
    pairs=[]; blocked=defaultdict(int)
    for m,g in pg.items():
     if not m or len(g)!=1: blocked['non_singleton_product_group']+=1; continue
     prod=g[0]; globals=meta.get(m,set()); cid=None
     if len(globals)==1 and next(iter(globals)) in cards: cid=next(iter(globals))
     elif len(globals)>1:
      inter=set(globals)&cards&nc.get(norm(prod.get('name')),set())
      if len(inter)==1: cid=next(iter(inter))
     if cid is None: blocked['unresolved_metacard']+=1; continue
     if len(pc[cid])!=1: blocked['multi_physical_card_group']+=1; continue
     pr=pc[cid]
     if norm(prod.get('name'))!=norm(pr[0].get('card_name')): blocked['name_mismatch']+=1; continue
     pr=pr[0]; eid=int(prod['external_product_id']); pid=int(pr['print_id'])
     if any(x!=pid for x in byp.get(eid,set())) or any(x!=eid for x in byr.get(pid,set())): blocked['accepted_claim_conflict']+=1; continue
     pairs.append({'set_code':code,'idExpansion':exp,'external_product_id':eid,'idProduct':str(prod['id_product']),'idMetacard':m,'print_id':pid,'card_id':cid,'card_name':str(pr['card_name']),'collector_number':str(pr['collector_number']),'rarity':pr.get('rarity'),'variant':pr.get('variant'),'already_same':pid in byp.get(eid,set())})
    if len({x['external_product_id'] for x in pairs})!=len(pairs) or len({x['print_id'] for x in pairs})!=len(pairs): raise RuntimeError({'not_one_to_one':code})
    reports.append({'set_code':code,'idExpansion':exp,'products':len(products),'canonical_ja_prints':len(prints),'canonical_cards':len(cards),'pairs':len(pairs),'existing_same':sum(x['already_same'] for x in pairs),'new_ready':sum(not x['already_same'] for x in pairs),'blocked':dict(sorted(blocked.items())),'pairs_detail':pairs}); allpairs.extend(pairs)
   c.rollback()
 finally: c.close()
 if len({x['external_product_id'] for x in allpairs})!=len(allpairs) or len({x['print_id'] for x in allpairs})!=len(allpairs): raise RuntimeError('global_not_one_to_one')
 payload={'status':'pass','production_writes':0,'ja_baseline':ja,'cardmarket_capture':str(capture),'certified_expansions':CERTIFIED,'sets':reports,'total_pairs':len(allpairs),'total_existing_same':sum(x['already_same'] for x in allpairs),'total_new_ready':sum(not x['already_same'] for x in allpairs)}
 Path('/tmp/yugioh-ocg-certified-singletons-v3.json').write_text(json.dumps(payload,ensure_ascii=False,indent=2,sort_keys=True,default=str)+'\n',encoding='utf-8'); print(json.dumps(payload,ensure_ascii=False,indent=2,sort_keys=True,default=str)); return 0
if __name__=='__main__': raise SystemExit(main())
