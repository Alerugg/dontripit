from __future__ import annotations

import argparse,hashlib,json,os,unicodedata
from collections import Counter,defaultdict
from pathlib import Path
import psycopg2
from psycopg2.extras import Json,RealDictCursor

GAME='yugioh'; ACCEPTED=('accepted','mapped','exact')
METHOD='cardmarket_ocg_certified_singleton_heavy_cohort_v1'
CONFIRM='APPLY_YUGIOH_OCG_SINGLETON_HEAVY_COHORT_V1'
EXPECTED_JA=36426; EXPECTED_CAPTURE='2026-08-18 21:09:20.740891+00:00'
STABLE_IDENTITY_SHA256='0ee6337441230d6a89b48f52bcade321dc7943fa6c1c1eb56f743237f325228d'
TARGETS={
 'SPTR':{'idExpansion':'4698','physical':102,'logical':60,'singletons':18},
 'SLT1':{'idExpansion':'4543','physical':73,'logical':50,'singletons':27},
 '309':{'idExpansion':'4885','physical':66,'logical':57,'singletons':49},
 '302':{'idExpansion':'4905','physical':60,'logical':55,'singletons':50},
 'CP19':{'idExpansion':'4581','physical':55,'logical':46,'singletons':37},
 'CP17':{'idExpansion':'4643','physical':54,'logical':45,'singletons':36},
 'CPF1':{'idExpansion':'4664','physical':54,'logical':45,'singletons':36},
 'SR14':{'idExpansion':'5475','physical':53,'logical':48,'singletons':43},
 'SR13':{'idExpansion':'5111','physical':50,'logical':45,'singletons':40},
 'ST19':{'idExpansion':'4585','physical':47,'logical':45,'singletons':43},
 'SD35':{'idExpansion':'4600','physical':47,'logical':44,'singletons':41},
 'DP15':{'idExpansion':'4716','physical':35,'logical':30,'singletons':25},
}
EXPECTED_TOTAL=sum(x['singletons'] for x in TARGETS.values())
IDENTITY_FIELDS=('set_code','idExpansion','external_product_id','idProduct','idMetacard','print_id','card_id','card_name','collector_number','canonical_rarity','canonical_variant')

def norm(v):
 t=unicodedata.normalize('NFKD',str(v or '')).casefold(); return ''.join(ch for ch in t if ch.isalnum())

def connect(readonly):
 url=os.getenv('DATABASE_URL_UNPOOLED') or os.getenv('DATABASE_URL')
 if not url: raise RuntimeError('DATABASE URL required')
 c=psycopg2.connect(url,connect_timeout=30,application_name='dontripit_ygo_ocg_singleton_heavy_apply_v1'); c.set_session(readonly=readonly,autocommit=False); return c

def derive(cur):
 cur.execute("SELECT id FROM games WHERE slug=%s LIMIT 1",(GAME,)); gid=int(cur.fetchone()['id'])
 cur.execute("SELECT max(last_seen_at) capture FROM external_catalog_products WHERE source='cardmarket' AND game_id=%s",(gid,)); capture=cur.fetchone()['capture']
 if str(capture)!=EXPECTED_CAPTURE: raise RuntimeError({'capture_drift':str(capture)})
 cur.execute("SELECT count(*) n FROM prints p JOIN cards c ON c.id=p.card_id WHERE c.game_id=%s AND lower(coalesce(p.language,''))='ja'",(gid,)); ja=int(cur.fetchone()['n'])
 if ja!=EXPECTED_JA: raise RuntimeError({'ja_baseline_drift':ja})
 cur.execute("""SELECT e.metacard_external_id,p.card_id,count(*) evidence_links FROM external_catalog_print_links l JOIN external_catalog_products e ON e.id=l.external_product_id JOIN prints p ON p.id=l.print_id WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single' AND e.metacard_external_id IS NOT NULL AND l.link_status=ANY(%s) GROUP BY e.metacard_external_id,p.card_id""",(gid,list(ACCEPTED)))
 meta=defaultdict(set); ev=Counter()
 for r in cur.fetchall(): m=str(r['metacard_external_id']); cid=int(r['card_id']); meta[m].add(cid); ev[(m,cid)]+=int(r['evidence_links'] or 0)
 cur.execute("""SELECT e.external_id id_product,l.external_product_id,l.print_id,l.mapping_method,l.confidence,l.reviewed,l.link_status FROM external_catalog_print_links l JOIN external_catalog_products e ON e.id=l.external_product_id WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single' AND l.link_status=ANY(%s)""",(gid,list(ACCEPTED)))
 pc=defaultdict(list); rc=defaultdict(list)
 for r in cur.fetchall(): row=dict(r); pc[int(r['external_product_id'])].append(row); rc[int(r['print_id'])].append(row)
 cur.execute("""SELECT e.id external_product_id,e.external_id id_product,e.expansion_external_id,e.name,e.metacard_external_id FROM external_catalog_products e WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single' AND e.last_seen_at=%s AND e.expansion_external_id IS NOT NULL ORDER BY e.expansion_external_id,e.external_id::bigint""",(gid,capture))
 allp=defaultdict(list)
 for r in cur.fetchall(): allp[str(r['expansion_external_id'])].append(dict(r))
 proposal=[]; existing=[]; reports=[]; identities=[]
 for code,cfg in TARGETS.items():
  exp=str(cfg['idExpansion']); products=allp.get(exp,[])
  cur.execute("""SELECT p.id print_id,p.card_id,p.collector_number,p.rarity,p.variant,c.name card_name FROM prints p JOIN cards c ON c.id=p.card_id JOIN sets s ON s.id=p.set_id WHERE c.game_id=%s AND upper(coalesce(s.code,''))=%s AND lower(coalesce(p.language,''))='ja' ORDER BY p.card_id,p.id""",(gid,code)); prints=[dict(r) for r in cur.fetchall()]
  card_ids={int(x['card_id']) for x in prints}; pg=defaultdict(list); cg=defaultdict(list)
  if len(products)!=cfg['physical'] or len(prints)!=cfg['physical'] or len(card_ids)!=cfg['logical']: raise RuntimeError({'surface_drift':code})
  for x in products: pg[str(x.get('metacard_external_id') or '')].append(x)
  for x in prints: cg[int(x['card_id'])].append(x)
  resolved=Counter(); canonical=Counter(int(x['card_id']) for x in prints)
  for m,g in pg.items():
   cards=meta.get(m,set()) if m else set()
   if not m or len(cards)!=1: raise RuntimeError({'metacard_resolution_drift':code,'idMetacard':m,'cards':sorted(cards)})
   cid=next(iter(cards)); resolved[cid]+=len(g)
   if cid not in cg or len(g)!=len(cg[cid]): raise RuntimeError({'group_cardinality_drift':code,'idMetacard':m})
   if any(norm(p['name'])!=norm(cg[cid][0]['card_name']) for p in g): raise RuntimeError({'name_drift':code,'idMetacard':m})
  if resolved!=canonical or Counter(norm(x['name']) for x in products)!=Counter(norm(x['card_name']) for x in prints): raise RuntimeError({'physical_bijection_drift':code})
  competitors=[]; cn=Counter(norm(x['card_name']) for x in prints)
  for oexp,other in allp.items():
   if oexp==exp or len(other)!=cfg['physical']: continue
   oc=Counter(); ok=True
   for p in other:
    m=str(p.get('metacard_external_id') or ''); cards=meta.get(m,set())
    if not m or len(cards)!=1: ok=False; break
    cid=next(iter(cards))
    if cid not in cg: ok=False; break
    oc[cid]+=1
   if ok and oc==canonical and Counter(norm(p['name']) for p in other)==cn: competitors.append(oexp)
  if competitors: raise RuntimeError({'competing_full_physical_bijection':code,'competitors':competitors})
  pairs=[]; ex=new=0
  for m,g in pg.items():
   cid=next(iter(meta[m])); cprints=cg[cid]
   if len(g)==1 and len(cprints)==1:
    p=g[0]; pr=cprints[0]; eid=int(p['external_product_id']); pid=int(pr['print_id'])
    pclaims=pc.get(eid,[]); rclaims=rc.get(pid,[])
    if any(int(r['print_id'])!=pid for r in pclaims) or any(int(r['external_product_id'])!=eid for r in rclaims): raise RuntimeError({'accepted_identity_conflict':code,'idProduct':str(p['id_product']),'print_id':pid})
    same=[r for r in pclaims if int(r['print_id'])==pid]
    if same:
     r=same[0]
     if len(same)!=1 or len(pclaims)!=1 or len(rclaims)!=1 or str(r.get('mapping_method') or '')!=METHOD or str(r.get('confidence') or '')!='exact' or not bool(r.get('reviewed')): raise RuntimeError({'unexpected_existing_pair':code,'idProduct':str(p['id_product'])})
     ex+=1
    else:
     if pclaims or rclaims: raise RuntimeError({'unexpected_singleton_claim':code,'idProduct':str(p['id_product'])})
     new+=1
    ident={'set_code':code,'idExpansion':exp,'external_product_id':eid,'idProduct':str(p['id_product']),'idMetacard':m,'print_id':pid,'card_id':cid,'card_name':str(pr['card_name']),'collector_number':str(pr['collector_number']),'canonical_rarity':pr.get('rarity'),'canonical_variant':pr.get('variant'),'metacard_evidence_links':int(ev.get((m,cid),0))}
    pairs.append({**ident,'already_same':bool(same)}); identities.append(ident)
   else:
    for p in g:
     if pc.get(int(p['external_product_id'])): raise RuntimeError({'variant_product_claimed':code,'idProduct':str(p['id_product'])})
    for pr in cprints:
     if rc.get(int(pr['print_id'])): raise RuntimeError({'variant_print_claimed':code,'print_id':int(pr['print_id'])})
  expected=cfg['singletons']
  if len(pairs)!=expected or (ex,new) not in ((0,expected),(expected,0)): raise RuntimeError({'singleton_surface_drift':code,'pairs':len(pairs),'existing':ex,'new':new})
  proposal.extend(x for x in pairs if not x['already_same']); existing.extend(x for x in pairs if x['already_same']); reports.append({'set_code':code,'idExpansion':exp,'pairs':expected,'existing_same':ex,'new_ready':new,'variant_physical':cfg['physical']-expected})
 stable=hashlib.sha256(json.dumps([{k:x.get(k) for k in IDENTITY_FIELDS} for x in identities],sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()
 if stable!=STABLE_IDENTITY_SHA256: raise RuntimeError({'stable_identity_hash_drift':stable})
 if len(identities)!=EXPECTED_TOTAL or len({x['external_product_id'] for x in identities})!=EXPECTED_TOTAL or len({x['print_id'] for x in identities})!=EXPECTED_TOTAL: raise RuntimeError({'global_singleton_bijection_failed':len(identities)})
 if len(proposal)+len(existing)!=EXPECTED_TOTAL or (len(existing),len(proposal)) not in ((0,EXPECTED_TOTAL),(EXPECTED_TOTAL,0)): raise RuntimeError({'global_partial_state':{'existing':len(existing),'new':len(proposal)}})
 if not all(x['metacard_evidence_links']>0 for x in identities): raise RuntimeError('pair_without_evidence')
 return {'gid':gid,'capture':capture,'ja':ja,'proposal':proposal,'existing':existing,'sets':reports,'stable_identity_sha256':stable}

def run(apply=False,confirm=''):
 if apply and confirm!=CONFIRM: raise RuntimeError(f'--apply requires --confirm {CONFIRM}')
 c=connect(not apply)
 try:
  with c.cursor(cursor_factory=RealDictCursor) as cur:
   s=derive(cur); report={'mode':'apply' if apply else 'dry_run','status':'pass','production_writes':0,'mapping_method':METHOD,'cardmarket_capture':str(s['capture']),'ja_baseline':s['ja'],'stable_identity_sha256':s['stable_identity_sha256'],'certified_pairs':EXPECTED_TOTAL,'already_accepted_same_pair':len(s['existing']),'new_links_ready':len(s['proposal']),'sets':s['sets']}
   if not apply: c.rollback(); return report
   writes=0
   for x in s['proposal']:
    evidence={'source':'current_cardmarket_full_physical_bijection+accepted_metacard_bridge+canonical_JA_singleton_partition','identity_basis':['pinned_current_cardmarket_capture','complete_product_to_canonical_physical_card_multiset','unique_metacard_to_logical_card','strict_name_multiset','no_competing_full_physical_bijection','one_product_for_metacard','one_JA_print_for_logical_card','variant_groups_left_unclaimed','stable_identity_hash'],'stable_identity_sha256':STABLE_IDENTITY_SHA256,'idExpansion':x['idExpansion'],'canonical_set':x['set_code'],'idProduct':x['idProduct'],'idMetacard':x['idMetacard'],'collector_number':x['collector_number'],'canonical_variant':x['canonical_variant'],'canonical_rarity':x['canonical_rarity'],'metacard_evidence_links_at_write':x['metacard_evidence_links']}
    cur.execute("""INSERT INTO external_catalog_print_links(external_product_id,print_id,mapping_method,confidence,link_status,reviewed,evidence) VALUES(%s,%s,%s,'exact','accepted',true,%s) ON CONFLICT(external_product_id,print_id) DO NOTHING""",(x['external_product_id'],x['print_id'],METHOD,Json(evidence)))
    if cur.rowcount!=1: raise RuntimeError({'insert_failed':x['idProduct']})
    writes+=1
   report['production_writes']=writes; c.commit(); return report
 except Exception: c.rollback(); raise
 finally: c.close()

def main():
 p=argparse.ArgumentParser(); p.add_argument('--apply',action='store_true'); p.add_argument('--confirm',default=''); p.add_argument('--report',type=Path,default=Path('/tmp/ygo-ocg-singleton-heavy-apply-v1.json')); a=p.parse_args(); payload=run(a.apply,a.confirm); a.report.parent.mkdir(parents=True,exist_ok=True); text=json.dumps(payload,ensure_ascii=False,indent=2,sort_keys=True,default=str)+'\n'; a.report.write_text(text,encoding='utf-8'); print(text,end=''); return 0
if __name__=='__main__': raise SystemExit(main())
