from __future__ import annotations

import argparse,hashlib,json,os
from collections import Counter,defaultdict
from pathlib import Path
import psycopg2
from psycopg2.extras import Json,RealDictCursor
from app.scripts.audit_yugioh_ocg_variant_ordinal_calibration_v1 import rarity,signature,seqkey
from app.scripts.apply_yugioh_ocg_singleton_heavy_cohort_v1 import EXPECTED_CAPTURE,EXPECTED_JA,norm

GAME='yugioh'; ACCEPTED=('accepted','mapped','exact')
METHOD='cardmarket_ocg_certified_independent_ordinal_rarity_v1'
CONFIRM='APPLY_YUGIOH_OCG_CALIBRATED_VARIANTS54_V1'
STABLE_IDENTITY_SHA256='f864e644d3b27e8adcf0a09bdb124a4735736b72134c719e5ff9931664efa72e'
TARGETS={
 'SLT1':{'idExpansion':'4543','pairs':26},
 'SR14':{'idExpansion':'5475','pairs':10},
 'SR13':{'idExpansion':'5111','pairs':10},
 'ST19':{'idExpansion':'4585','pairs':4},
 'SD35':{'idExpansion':'4600','pairs':4},
}
EXPECTED_TOTAL=54; MIN_SUPPORT=2
ALLOWED={('secret','super'):('super','secret'),('secret','ultra'):('ultra','secret')}
IDENTITY_FIELDS=('set_code','idExpansion','idMetacard','card_id','card_name','external_product_id','idProduct','product_ordinal','calibrated_rarity','print_id','collector_number','canonical_rarity','canonical_variant','rarity_signature')

def connect(readonly):
 url=os.getenv('DATABASE_URL_UNPOOLED') or os.getenv('DATABASE_URL')
 if not url: raise RuntimeError('DATABASE URL required')
 c=psycopg2.connect(url,connect_timeout=30,application_name='dontripit_ygo_ocg_calibrated_variants54_v1'); c.set_session(readonly=readonly,autocommit=False); return c

def derive(cur):
 cur.execute("SELECT id FROM games WHERE slug=%s LIMIT 1",(GAME,)); gid=int(cur.fetchone()['id'])
 cur.execute("SELECT max(last_seen_at) capture FROM external_catalog_products WHERE source='cardmarket' AND game_id=%s",(gid,)); capture=cur.fetchone()['capture']
 if str(capture)!=EXPECTED_CAPTURE: raise RuntimeError({'capture_drift':str(capture)})
 cur.execute("SELECT count(*) n FROM prints p JOIN cards c ON c.id=p.card_id WHERE c.game_id=%s AND lower(coalesce(p.language,''))='ja'",(gid,)); ja=int(cur.fetchone()['n'])
 if ja!=EXPECTED_JA: raise RuntimeError({'ja_baseline_drift':ja})
 cur.execute("""SELECT e.id external_product_id,e.external_id id_product,e.expansion_external_id,e.metacard_external_id,e.name FROM external_catalog_products e WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single' AND e.last_seen_at=%s AND e.expansion_external_id IS NOT NULL AND e.metacard_external_id IS NOT NULL ORDER BY e.expansion_external_id,e.metacard_external_id,e.external_id::bigint""",(gid,capture))
 products=[dict(r) for r in cur.fetchall()]; pg=defaultdict(list)
 for r in products: pg[(str(r['expansion_external_id']),str(r['metacard_external_id']))].append(r)
 cur.execute("""SELECT e.id external_product_id,e.external_id id_product,e.expansion_external_id,e.metacard_external_id,e.name,l.mapping_method,l.confidence,l.reviewed,p.id print_id,p.card_id,p.rarity,p.variant,p.collector_number,c.name card_name,s.code set_code FROM external_catalog_print_links l JOIN external_catalog_products e ON e.id=l.external_product_id JOIN prints p ON p.id=l.print_id JOIN cards c ON c.id=p.card_id JOIN sets s ON s.id=p.set_id WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single' AND e.last_seen_at=%s AND e.metacard_external_id IS NOT NULL AND l.link_status=ANY(%s) AND lower(coalesce(p.language,''))='ja' AND l.confidence='exact' AND l.reviewed=true ORDER BY e.expansion_external_id,e.metacard_external_id,e.external_id::bigint""",(gid,capture,list(ACCEPTED)))
 accepted=[dict(r) for r in cur.fetchall()]; ag=defaultdict(list); bp=defaultdict(list); br=defaultdict(list); meta=defaultdict(set)
 for r in accepted:
  ag[(str(r['expansion_external_id']),str(r['metacard_external_id']))].append(r); bp[int(r['external_product_id'])].append(r); br[int(r['print_id'])].append(r); meta[str(r['metacard_external_id'])].add(int(r['card_id']))
 # Independent calibration: exclude previous ordinal method and this method itself.
 cal=defaultdict(Counter); calsets=defaultdict(lambda:defaultdict(set)); calmethods=defaultdict(lambda:defaultdict(Counter))
 for key,gproducts in pg.items():
  if len(gproducts)<=1: continue
  rows=[r for r in ag.get(key,[]) if 'version_ordinal' not in str(r['mapping_method']) and str(r['mapping_method'])!=METHOD]
  if len(rows)!=len(gproducts) or len({int(r['external_product_id']) for r in rows})!=len(gproducts) or len({int(r['print_id']) for r in rows})!=len(gproducts): continue
  if len({int(r['card_id']) for r in rows})!=1 or len({str(r['set_code']).upper() for r in rows})!=1 or any(norm(r['name'])!=norm(r['card_name']) for r in rows): continue
  ordered=sorted(rows,key=lambda r:int(r['id_product'])); sig=signature(ordered); seq=seqkey(ordered)
  if len(sig)!=len(set(sig)): continue
  cal[sig][seq]+=1; calsets[sig][seq].add(str(ordered[0]['set_code']).upper()); calmethods[sig][seq].update(str(r['mapping_method']) for r in ordered)
 for sig,seq in ALLOWED.items():
  if cal[sig]!=Counter({seq:cal[sig][seq]}) or cal[sig][seq]<MIN_SUPPORT: raise RuntimeError({'independent_calibration_drift':sig,'sequences':{str(k):v for k,v in cal[sig].items()}})
 proposal=[]; existing=[]; identities=[]; reports=[]
 for code,cfg in TARGETS.items():
  exp=str(cfg['idExpansion']); cur.execute("""SELECT p.id print_id,p.card_id,p.collector_number,p.rarity,p.variant,c.name card_name FROM prints p JOIN cards c ON c.id=p.card_id JOIN sets s ON s.id=p.set_id WHERE c.game_id=%s AND upper(coalesce(s.code,''))=%s AND lower(coalesce(p.language,''))='ja' ORDER BY p.card_id,p.id""",(gid,code)); prints=[dict(r) for r in cur.fetchall()]; bycard=defaultdict(list)
  for r in prints: bycard[int(r['card_id'])].append(r)
  pairs=[]; ex=new=0
  for (gexp,m),gproducts in pg.items():
   if gexp!=exp or len(gproducts)<=1: continue
   cards=meta.get(m,set())
   if len(cards)!=1: raise RuntimeError({'target_metacard_resolution':code,'idMetacard':m,'cards':sorted(cards)})
   cid=next(iter(cards)); cprints=bycard.get(cid,[])
   if len(cprints)!=len(gproducts): continue
   sig=signature(cprints); seq=ALLOWED.get(sig)
   if not seq: continue
   if any(norm(p['name'])!=norm(cprints[0]['card_name']) for p in gproducts): raise RuntimeError({'name_drift':code,'idMetacard':m})
   byrar=defaultdict(list)
   for pr in cprints: byrar[rarity(pr['rarity'])].append(pr)
   if any(len(byrar[r])!=1 for r in seq): raise RuntimeError({'rarity_not_bijective':code,'idMetacard':m})
   ordered=sorted(gproducts,key=lambda x:int(x['id_product']))
   for ordinal,(prod,rar) in enumerate(zip(ordered,seq),1):
    pr=byrar[rar][0]; eid=int(prod['external_product_id']); pid=int(pr['print_id']); pclaims=bp.get(eid,[]); rclaims=br.get(pid,[])
    if any(int(r['print_id'])!=pid for r in pclaims) or any(int(r['external_product_id'])!=eid for r in rclaims): raise RuntimeError({'accepted_identity_conflict':code,'idProduct':str(prod['id_product']),'print_id':pid})
    same=[r for r in pclaims if int(r['print_id'])==pid]
    if same:
     r=same[0]
     if len(same)!=1 or len(pclaims)!=1 or len(rclaims)!=1 or str(r['mapping_method'])!=METHOD or str(r['confidence'])!='exact' or not bool(r['reviewed']): raise RuntimeError({'unexpected_existing_pair':code,'idProduct':str(prod['id_product'])})
     ex+=1
    else:
     if pclaims or rclaims: raise RuntimeError({'unexpected_claim_state':code,'idProduct':str(prod['id_product'])})
     new+=1
    ident={'set_code':code,'idExpansion':exp,'idMetacard':m,'card_id':cid,'card_name':str(pr['card_name']),'external_product_id':eid,'idProduct':str(prod['id_product']),'product_ordinal':ordinal,'calibrated_rarity':rar,'print_id':pid,'collector_number':str(pr['collector_number']),'canonical_rarity':str(pr['rarity']),'canonical_variant':str(pr.get('variant') or ''),'rarity_signature':list(sig)}
    identities.append(ident); pairs.append({**ident,'already_same':bool(same),'calibration_support_groups':int(cal[sig][seq]),'calibration_support_sets':sorted(calsets[sig][seq]),'calibration_methods':dict(calmethods[sig][seq])})
  if len(pairs)!=cfg['pairs'] or (ex,new) not in ((0,cfg['pairs']),(cfg['pairs'],0)): raise RuntimeError({'target_pair_count_drift':code,'pairs':len(pairs),'existing':ex,'new':new})
  proposal.extend(x for x in pairs if not x['already_same']); existing.extend(x for x in pairs if x['already_same']); reports.append({'set_code':code,'idExpansion':exp,'pairs':cfg['pairs'],'existing_same':ex,'new_ready':new})
 stable=hashlib.sha256(json.dumps([{k:x.get(k) for k in IDENTITY_FIELDS} for x in identities],sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()
 if stable!=STABLE_IDENTITY_SHA256: raise RuntimeError({'stable_identity_hash_drift':stable})
 if len(identities)!=EXPECTED_TOTAL or len({x['external_product_id'] for x in identities})!=EXPECTED_TOTAL or len({x['print_id'] for x in identities})!=EXPECTED_TOTAL: raise RuntimeError({'global_bijection_failed':len(identities)})
 if len(proposal)+len(existing)!=EXPECTED_TOTAL or (len(existing),len(proposal)) not in ((0,EXPECTED_TOTAL),(EXPECTED_TOTAL,0)): raise RuntimeError({'global_partial_state':{'existing':len(existing),'new':len(proposal)}})
 return {'capture':capture,'ja':ja,'proposal':proposal,'existing':existing,'sets':reports,'stable':stable}

def run(apply=False,confirm=''):
 if apply and confirm!=CONFIRM: raise RuntimeError(f'--apply requires --confirm {CONFIRM}')
 c=connect(not apply)
 try:
  with c.cursor(cursor_factory=RealDictCursor) as cur:
   s=derive(cur); report={'mode':'apply' if apply else 'dry_run','status':'pass','production_writes':0,'mapping_method':METHOD,'cardmarket_capture':str(s['capture']),'ja_baseline':s['ja'],'stable_identity_sha256':s['stable'],'certified_pairs':EXPECTED_TOTAL,'already_accepted_same_pair':len(s['existing']),'new_links_ready':len(s['proposal']),'sets':s['sets']}
   if not apply: c.rollback(); return report
   writes=0
   for x in s['proposal']:
    ev={'source':'independent_current_exact_multiversion_calibration+target_complete_physical_bijection','identity_basis':['current_capture','unique_metacard_card','strict_name_match','independent_non_version_ordinal_calibration','unique_supported_ordinal_rarity_sequence','rarity_bijective','stable_identity_hash'],'stable_identity_sha256':STABLE_IDENTITY_SHA256,'idExpansion':x['idExpansion'],'canonical_set':x['set_code'],'idProduct':x['idProduct'],'idMetacard':x['idMetacard'],'product_ordinal':x['product_ordinal'],'calibrated_rarity':x['calibrated_rarity'],'calibration_support_groups':x['calibration_support_groups'],'calibration_support_sets':x['calibration_support_sets'],'calibration_methods':x['calibration_methods'],'collector_number':x['collector_number'],'canonical_rarity':x['canonical_rarity'],'canonical_variant':x['canonical_variant']}
    cur.execute("""INSERT INTO external_catalog_print_links(external_product_id,print_id,mapping_method,confidence,link_status,reviewed,evidence) VALUES(%s,%s,%s,'exact','accepted',true,%s) ON CONFLICT(external_product_id,print_id) DO NOTHING""",(x['external_product_id'],x['print_id'],METHOD,Json(ev)))
    if cur.rowcount!=1: raise RuntimeError({'insert_failed':x['idProduct']})
    writes+=1
   report['production_writes']=writes; c.commit(); return report
 except Exception: c.rollback(); raise
 finally: c.close()

def main():
 p=argparse.ArgumentParser();p.add_argument('--apply',action='store_true');p.add_argument('--confirm',default='');p.add_argument('--report',type=Path,default=Path('/tmp/ygo-ocg-calibrated-variants54-v1.json'));a=p.parse_args();payload=run(a.apply,a.confirm);a.report.parent.mkdir(parents=True,exist_ok=True);text=json.dumps(payload,ensure_ascii=False,indent=2,sort_keys=True,default=str)+'\n';a.report.write_text(text,encoding='utf-8');print(text,end='');return 0
if __name__=='__main__':raise SystemExit(main())
