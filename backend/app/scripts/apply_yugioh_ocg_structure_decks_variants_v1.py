from __future__ import annotations

import argparse,json,os
from collections import defaultdict
from pathlib import Path
import psycopg2
from psycopg2.extras import Json,RealDictCursor
from app.scripts.audit_yugioh_ocg_structure_decks_variants_v1 import TARGETS,evidence_sha256,norm,rarity,rkey

GAME='yugioh'; ACCEPTED=('accepted','mapped','exact')
METHOD='cardmarket_ocg_certified_public_version_contract_v2'
CONFIRM='APPLY_YUGIOH_OCG_STRUCTURE_DECK_VARIANTS_V1'
EXPECTED_JA=36426; EXPECTED_CAPTURE='2026-08-18 21:09:20.740891+00:00'; EVIDENCE_SHA256='4bebcbd1d942966ac88744a6fde7029b506bed761efbf1bea71f558c52b71b74'
EXPECTED={'SD41':10,'SD40':16,'SD38':10,'SD36':4}; BEFORE={'SD41':43,'SD40':38,'SD38':43,'SD36':44}; AFTER={'SD41':53,'SD40':54,'SD38':53,'SD36':48}; EXPECTED_TOTAL=40

def connect(readonly):
 url=os.getenv('DATABASE_URL_UNPOOLED') or os.getenv('DATABASE_URL')
 if not url: raise RuntimeError('DATABASE URL required')
 c=psycopg2.connect(url,connect_timeout=30,application_name='dontripit_ygo_ocg_structure_deck_variants_apply_v1'); c.set_session(readonly=readonly,autocommit=False); return c

def derive(cur):
 if evidence_sha256()!=EVIDENCE_SHA256: raise RuntimeError({'evidence_hash_drift':evidence_sha256()})
 cur.execute("SELECT id FROM games WHERE slug=%s LIMIT 1",(GAME,)); gid=int(cur.fetchone()['id'])
 cur.execute("SELECT max(last_seen_at) capture FROM external_catalog_products WHERE source='cardmarket' AND game_id=%s",(gid,)); capture=cur.fetchone()['capture']
 if str(capture)!=EXPECTED_CAPTURE: raise RuntimeError({'capture_drift':str(capture)})
 cur.execute("""SELECT count(*) n FROM prints p JOIN cards c ON c.id=p.card_id WHERE c.game_id=%s AND lower(coalesce(p.language,''))='ja'""",(gid,)); ja=int(cur.fetchone()['n'])
 if ja!=EXPECTED_JA: raise RuntimeError({'ja_baseline_drift':ja})
 cur.execute("""SELECT e.metacard_external_id,p.card_id FROM external_catalog_print_links l JOIN external_catalog_products e ON e.id=l.external_product_id JOIN prints p ON p.id=l.print_id WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single' AND e.metacard_external_id IS NOT NULL AND l.link_status=ANY(%s) GROUP BY e.metacard_external_id,p.card_id""",(gid,list(ACCEPTED)))
 meta=defaultdict(set)
 for r in cur.fetchall(): meta[str(r['metacard_external_id'])].add(int(r['card_id']))
 cur.execute("""SELECT e.external_id id_product,l.external_product_id,l.print_id,l.mapping_method,l.confidence,l.reviewed,l.link_status FROM external_catalog_print_links l JOIN external_catalog_products e ON e.id=l.external_product_id WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single' AND l.link_status=ANY(%s)""",(gid,list(ACCEPTED)))
 accepted=[dict(r) for r in cur.fetchall()]; bp=defaultdict(list); br=defaultdict(list)
 for r in accepted: bp[int(r['external_product_id'])].append(r); br[int(r['print_id'])].append(r)
 proposal=[]; existing=[]; reports=[]
 for code,cfg in TARGETS.items():
  cur.execute("""SELECT e.id external_product_id,e.external_id id_product,e.name,e.metacard_external_id FROM external_catalog_products e WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single' AND e.expansion_external_id=%s AND e.last_seen_at=%s ORDER BY e.metacard_external_id,e.external_id::bigint""",(gid,cfg['idExpansion'],capture)); products=[dict(r) for r in cur.fetchall()]
  cur.execute("""SELECT p.id print_id,p.card_id,p.collector_number,p.rarity,p.variant,c.name card_name FROM prints p JOIN cards c ON c.id=p.card_id JOIN sets s ON s.id=p.set_id WHERE c.game_id=%s AND upper(coalesce(s.code,''))=%s AND lower(coalesce(p.language,''))='ja' ORDER BY p.card_id,p.id""",(gid,code)); prints=[dict(r) for r in cur.fetchall()]
  if (len(products),len(prints))!=(cfg['physical'],cfg['physical']): raise RuntimeError({'surface_drift':code})
  pg=defaultdict(list); pc=defaultdict(list)
  for x in products: pg[str(x.get('metacard_external_id') or '')].append(x)
  for x in prints: pc[int(x['card_id'])].append(x)
  pairs=[]; ex=new=0
  for m,g in pg.items():
   cards=meta.get(m,set()) if m else set()
   if not m or len(cards)!=1: raise RuntimeError({'metacard_resolution':code,'meta':m,'cards':sorted(cards)})
   cid=next(iter(cards)); cps=pc.get(cid,[])
   if len(g)!=len(cps): raise RuntimeError({'group_cardinality':code,'meta':m})
   if len(g)==1: continue
   if len(g)!=2: raise RuntimeError({'unexpected_variant_group_size':code,'meta':m,'size':len(g)})
   if any(norm(x['name'])!=norm(cps[0]['card_name']) for x in g): raise RuntimeError({'name_drift':code,'meta':m})
   key=rkey(cps); seq=cfg['contracts'].get(key)
   if not seq or len(seq)!=2: raise RuntimeError({'unsupported_geometry':code,'meta':m,'rarity_key':key})
   byrar=defaultdict(list)
   for x in cps: byrar[rarity(x['rarity'])].append(x)
   if any(len(byrar[r])!=1 for r in seq): raise RuntimeError({'rarity_not_bijective':code,'meta':m})
   ordered=sorted(g,key=lambda x:int(x['id_product']))
   for ordinal,(prod,rar) in enumerate(zip(ordered,seq),1):
    pr=byrar[rar][0]; eid=int(prod['external_product_id']); pid=int(pr['print_id']); pclaims=bp.get(eid,[]); rclaims=br.get(pid,[])
    if any(int(r['print_id'])!=pid for r in pclaims) or any(int(r['external_product_id'])!=eid for r in rclaims): raise RuntimeError({'accepted_identity_conflict':code,'idProduct':str(prod['id_product']),'print_id':pid})
    same=[r for r in pclaims if int(r['print_id'])==pid]
    if same:
     r=same[0]
     if len(same)!=1 or len(pclaims)!=1 or len(rclaims)!=1 or str(r.get('mapping_method') or '')!=METHOD or str(r.get('confidence') or '')!='exact' or not bool(r.get('reviewed')): raise RuntimeError({'unexpected_existing_pair':code,'idProduct':str(prod['id_product'])})
     ex+=1
    else: new+=1
    pairs.append({'set_code':code,'idExpansion':str(cfg['idExpansion']),'idMetacard':m,'external_product_id':eid,'idProduct':str(prod['id_product']),'product_name':str(prod['name']),'product_ordinal':ordinal,'contract_rarity':rar,'print_id':pid,'card_id':cid,'card_name':str(pr['card_name']),'collector_number':str(pr['collector_number']),'canonical_rarity':str(pr['rarity']),'canonical_variant':str(pr['variant'] or ''),'contract_key':key,'already_same':bool(same)})
  expected=EXPECTED[code]
  if len(pairs)!=expected or (ex,new) not in ((0,expected),(expected,0)): raise RuntimeError({'variant_surface_drift':code,'pairs':len(pairs),'existing':ex,'new':new})
  regional=sum(1 for r in accepted if any(int(r['external_product_id'])==int(p['external_product_id']) for p in products) and any(int(r['print_id'])==int(q['print_id']) for q in prints))
  if new and regional!=BEFORE[code]: raise RuntimeError({'regional_preapply_drift':code,'actual':regional,'expected':BEFORE[code]})
  if not new and regional!=AFTER[code]: raise RuntimeError({'regional_postapply_drift':code,'actual':regional,'expected':AFTER[code]})
  proposal.extend(x for x in pairs if not x['already_same']); existing.extend(x for x in pairs if x['already_same']); reports.append({'set_code':code,'idExpansion':str(cfg['idExpansion']),'pairs':expected,'existing_same':ex,'new_ready':new,'regional_accepted_before_or_after':regional})
 if len(proposal)+len(existing)!=EXPECTED_TOTAL or (len(existing),len(proposal)) not in ((0,EXPECTED_TOTAL),(EXPECTED_TOTAL,0)): raise RuntimeError({'global_partial_state':{'existing':len(existing),'new':len(proposal)}})
 return {'gid':gid,'capture':capture,'ja':ja,'proposal':proposal,'existing':existing,'sets':reports}

def run(apply=False,confirm=''):
 if apply and confirm!=CONFIRM: raise RuntimeError(f'--apply requires --confirm {CONFIRM}')
 c=connect(not apply)
 try:
  with c.cursor(cursor_factory=RealDictCursor) as cur:
   s=derive(cur); report={'mode':'apply' if apply else 'dry_run','status':'pass','production_writes':0,'mapping_method':METHOD,'cardmarket_capture':str(s['capture']),'ja_baseline':s['ja'],'evidence_sha256':EVIDENCE_SHA256,'certified_pairs':EXPECTED_TOTAL,'already_accepted_same_pair':len(s['existing']),'new_links_ready':len(s['proposal']),'sets':s['sets']}
   if not apply: c.rollback(); return report
   writes=0
   for x in s['proposal']:
    ev={'source':'cardmarket_first_party_public_structure_deck_version_contract+current_cardmarket_product_catalog+yugioh_canonical_physical_identity','identity_basis':['first_party_public_set_local_version_rarity_contract','pinned_current_cardmarket_capture','accepted_metacard_to_logical_card_bridge','complete_two_product_metacard_surface','complete_two_print_exact_set_JA_surface','strict_normalized_name_match','product_ordinal_to_canonical_rarity_contract','global_product_and_print_unclaimed','global_one_to_one'],'evidence_sha256':EVIDENCE_SHA256,'idExpansion':x['idExpansion'],'canonical_set':x['set_code'],'idProduct':x['idProduct'],'idMetacard':x['idMetacard'],'collector_number':x['collector_number'],'canonical_variant':x['canonical_variant'],'canonical_rarity':x['canonical_rarity'],'product_ordinal':x['product_ordinal'],'contract_rarity':x['contract_rarity'],'contract_key':x['contract_key']}
    cur.execute("""INSERT INTO external_catalog_print_links(external_product_id,print_id,mapping_method,confidence,link_status,reviewed,evidence) VALUES(%s,%s,%s,'exact','accepted',true,%s) ON CONFLICT(external_product_id,print_id) DO NOTHING""",(x['external_product_id'],x['print_id'],METHOD,Json(ev)))
    if cur.rowcount!=1: raise RuntimeError({'insert_failed':x['idProduct']})
    writes+=1
   report['production_writes']=writes; c.commit(); return report
 except Exception: c.rollback(); raise
 finally: c.close()
def main():
 p=argparse.ArgumentParser(); p.add_argument('--apply',action='store_true'); p.add_argument('--confirm',default=''); p.add_argument('--report',type=Path,default=Path('/tmp/yugioh-ocg-structure-deck-variants-apply-v1.json')); a=p.parse_args(); payload=run(a.apply,a.confirm); a.report.parent.mkdir(parents=True,exist_ok=True); text=json.dumps(payload,ensure_ascii=False,indent=2,sort_keys=True,default=str)+'\n'; a.report.write_text(text,encoding='utf-8'); print(text,end=''); return 0
if __name__=='__main__': raise SystemExit(main())
