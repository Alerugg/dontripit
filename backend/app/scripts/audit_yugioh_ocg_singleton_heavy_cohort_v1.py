from __future__ import annotations

import hashlib,json,os,unicodedata
from collections import Counter,defaultdict
from pathlib import Path
import psycopg2
from psycopg2.extras import RealDictCursor

GAME='yugioh'; ACCEPTED=('accepted','mapped','exact'); EXPECTED_JA=36426; EXPECTED_CAPTURE='2026-08-18 21:09:20.740891+00:00'
TARGETS={
 'SPTR':{'idExpansion':'4698','physical':102,'logical':60},
 'SLT1':{'idExpansion':'4543','physical':73,'logical':50},
 '309':{'idExpansion':'4885','physical':66,'logical':57},
 '302':{'idExpansion':'4905','physical':60,'logical':55},
 'CP19':{'idExpansion':'4581','physical':55,'logical':46},
 'CP17':{'idExpansion':'4643','physical':54,'logical':45},
 'CPF1':{'idExpansion':'4664','physical':54,'logical':45},
 'SR14':{'idExpansion':'5475','physical':53,'logical':48},
 'SR13':{'idExpansion':'5111','physical':50,'logical':45},
 'ST19':{'idExpansion':'4585','physical':47,'logical':45},
 'SD35':{'idExpansion':'4600','physical':47,'logical':44},
 'DP15':{'idExpansion':'4716','physical':35,'logical':30},
 'TBC1':{'idExpansion':'5543','physical':15,'logical':14},
}

def norm(v):
 t=unicodedata.normalize('NFKD',str(v or '')).casefold(); return ''.join(ch for ch in t if ch.isalnum())

def main()->int:
 url=os.getenv('DATABASE_URL_UNPOOLED') or os.getenv('DATABASE_URL')
 if not url: raise RuntimeError('DATABASE URL required')
 c=psycopg2.connect(url,connect_timeout=30,application_name='dontripit_ygo_ocg_singleton_heavy_cohort_v1'); c.set_session(readonly=True,autocommit=False)
 try:
  with c.cursor(cursor_factory=RealDictCursor) as cur:
   cur.execute("SELECT id FROM games WHERE slug=%s LIMIT 1",(GAME,)); gid=int(cur.fetchone()['id'])
   cur.execute("SELECT max(last_seen_at) capture FROM external_catalog_products WHERE source='cardmarket' AND game_id=%s",(gid,)); capture=cur.fetchone()['capture']
   if str(capture)!=EXPECTED_CAPTURE: raise RuntimeError({'capture_drift':str(capture)})
   cur.execute("SELECT count(*) n FROM prints p JOIN cards c ON c.id=p.card_id WHERE c.game_id=%s AND lower(coalesce(p.language,''))='ja'",(gid,)); ja=int(cur.fetchone()['n'])
   if ja!=EXPECTED_JA: raise RuntimeError({'ja_baseline_drift':ja})
   cur.execute("""SELECT e.metacard_external_id,p.card_id,count(*) evidence_links FROM external_catalog_print_links l JOIN external_catalog_products e ON e.id=l.external_product_id JOIN prints p ON p.id=l.print_id WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single' AND e.metacard_external_id IS NOT NULL AND l.link_status=ANY(%s) GROUP BY e.metacard_external_id,p.card_id""",(gid,list(ACCEPTED)))
   meta=defaultdict(set); ev=Counter()
   for r in cur.fetchall(): m=str(r['metacard_external_id']); cid=int(r['card_id']); meta[m].add(cid); ev[(m,cid)]+=int(r['evidence_links'] or 0)
   cur.execute("""SELECT l.external_product_id,l.print_id,e.external_id id_product FROM external_catalog_print_links l JOIN external_catalog_products e ON e.id=l.external_product_id WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single' AND l.link_status=ANY(%s)""",(gid,list(ACCEPTED)))
   pc=defaultdict(list); rc=defaultdict(list)
   for r in cur.fetchall(): row=dict(r); pc[int(r['external_product_id'])].append(row); rc[int(r['print_id'])].append(row)
   cur.execute("""SELECT e.id external_product_id,e.external_id id_product,e.expansion_external_id,e.name,e.metacard_external_id FROM external_catalog_products e WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single' AND e.last_seen_at=%s AND e.expansion_external_id IS NOT NULL ORDER BY e.expansion_external_id,e.external_id::bigint""",(gid,capture))
   allp=defaultdict(list)
   for r in cur.fetchall(): allp[str(r['expansion_external_id'])].append(dict(r))
   certified=[]; rejected=[]; proposal=[]; residual=[]
   for code,cfg in TARGETS.items():
    exp=str(cfg['idExpansion']); products=allp.get(exp,[])
    cur.execute("""SELECT p.id print_id,p.card_id,p.collector_number,p.rarity,p.variant,c.name card_name FROM prints p JOIN cards c ON c.id=p.card_id JOIN sets s ON s.id=p.set_id WHERE c.game_id=%s AND upper(coalesce(s.code,''))=%s AND lower(coalesce(p.language,''))='ja' ORDER BY p.card_id,p.id""",(gid,code)); prints=[dict(r) for r in cur.fetchall()]
    if len(products)!=cfg['physical'] or len(prints)!=cfg['physical'] or len({int(x['card_id']) for x in prints})!=cfg['logical']:
     raise RuntimeError({'surface_drift':code,'products':len(products),'prints':len(prints),'logical':len({int(x['card_id']) for x in prints})})
    pg=defaultdict(list); cg=defaultdict(list); bad=False; reasons=[]
    for x in products: pg[str(x.get('metacard_external_id') or '')].append(x)
    for x in prints: cg[int(x['card_id'])].append(x)
    resolved_counter=Counter(); canonical_counter=Counter(int(x['card_id']) for x in prints)
    target_claims=0
    for m,g in pg.items():
     cards=meta.get(m,set()) if m else set()
     if not m or len(cards)!=1: bad=True; reasons.append(f'metacard_{m}_resolution_{len(cards)}'); continue
     cid=next(iter(cards)); resolved_counter[cid]+=len(g)
     if cid not in cg: bad=True; reasons.append(f'metacard_{m}_outside_set'); continue
     if len(g)!=len(cg[cid]): bad=True; reasons.append(f'group_cardinality_{m}_{len(g)}_{len(cg[cid])}')
     if any(norm(p['name'])!=norm(cg[cid][0]['card_name']) for p in g): bad=True; reasons.append(f'name_{m}')
     target_claims+=sum(bool(pc.get(int(p['external_product_id']))) for p in g)+sum(bool(rc.get(int(pr['print_id']))) for pr in cg[cid])
    if resolved_counter!=canonical_counter: bad=True; reasons.append('resolved_physical_card_multiset')
    canonical_names=Counter(norm(x['card_name']) for x in prints); product_names=Counter(norm(x['name']) for x in products)
    if canonical_names!=product_names: bad=True; reasons.append('name_multiset')
    competitors=[]
    for oexp,other in allp.items():
     if oexp==exp or len(other)!=cfg['physical']: continue
     oc=Counter(); ok=True
     for p in other:
      m=str(p.get('metacard_external_id') or ''); cards=meta.get(m,set())
      if not m or len(cards)!=1: ok=False; break
      cid=next(iter(cards));
      if cid not in cg: ok=False; break
      oc[cid]+=1
     if ok and oc==canonical_counter and Counter(norm(p['name']) for p in other)==canonical_names: competitors.append(oexp)
    if competitors: bad=True; reasons.append('competing_full_physical_bijection')
    if target_claims: bad=True; reasons.append(f'existing_claims_{target_claims}')
    if bad:
     rejected.append({'set_code':code,'idExpansion':exp,'physical':cfg['physical'],'logical':cfg['logical'],'reasons':sorted(set(reasons)),'competitors':competitors}); continue
    singles=[]; variants=[]
    for m,g in pg.items():
     cid=next(iter(meta[m])); cprints=cg[cid]
     if len(g)==1 and len(cprints)==1:
      p=g[0]; pr=cprints[0]
      singles.append({'set_code':code,'idExpansion':exp,'external_product_id':int(p['external_product_id']),'idProduct':str(p['id_product']),'idMetacard':m,'print_id':int(pr['print_id']),'card_id':cid,'card_name':str(pr['card_name']),'collector_number':str(pr['collector_number']),'canonical_rarity':pr.get('rarity'),'canonical_variant':pr.get('variant'),'metacard_evidence_links':int(ev.get((m,cid),0))})
     else:
      variants.append({'set_code':code,'idExpansion':exp,'idMetacard':m,'card_id':cid,'card_name':str(cprints[0]['card_name']),'product_count':len(g),'print_count':len(cprints),'idProducts':[str(x['id_product']) for x in g],'prints':[{'print_id':int(x['print_id']),'collector_number':str(x['collector_number']),'rarity':x.get('rarity'),'variant':x.get('variant')} for x in cprints]})
    certified.append({'set_code':code,'idExpansion':exp,'physical':cfg['physical'],'logical':cfg['logical'],'singleton_pairs':len(singles),'variant_physical':sum(x['product_count'] for x in variants),'variant_groups':len(variants)})
    proposal.extend(singles); residual.extend(variants)
   c.rollback()
 finally: c.close()
 if len({x['external_product_id'] for x in proposal})!=len(proposal) or len({x['print_id'] for x in proposal})!=len(proposal): raise RuntimeError('global_singleton_not_one_to_one')
 if not all(x['metacard_evidence_links']>0 for x in proposal): raise RuntimeError('singleton_without_evidence')
 identity=[{k:v for k,v in x.items() if k!='metacard_evidence_links'} for x in proposal]
 fp=hashlib.sha256(json.dumps(identity,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()
 report={'status':'pass','mode':'read_only','production_writes':0,'cardmarket_capture':str(capture),'ja_baseline':ja,'screened_targets':len(TARGETS),'certified_targets':len(certified),'rejected_targets':len(rejected),'certified_singleton_pairs':len(proposal),'stable_singleton_identity_sha256':fp,'certified':certified,'rejected':rejected,'proposal':proposal,'variant_residual_groups':residual}
 out=Path(os.getenv('YGO_OCG_SINGLETON_HEAVY_COHORT_OUTPUT','/tmp/ygo-ocg-singleton-heavy-cohort-v1.json')); out.parent.mkdir(parents=True,exist_ok=True); out.write_text(json.dumps(report,ensure_ascii=False,indent=2,sort_keys=True,default=str)+'\n'); print(json.dumps(report,ensure_ascii=False,indent=2,sort_keys=True,default=str)); return 0
if __name__=='__main__': raise SystemExit(main())
