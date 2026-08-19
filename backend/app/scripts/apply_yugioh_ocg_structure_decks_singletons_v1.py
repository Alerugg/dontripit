from __future__ import annotations

import argparse,json,os
from collections import Counter,defaultdict
from pathlib import Path
import psycopg2
from psycopg2.extras import Json,RealDictCursor
from app.scripts.audit_yugioh_ocg_structure_decks_public_code_v1 import TARGETS,evidence_sha256,norm

GAME='yugioh'; ACCEPTED=('accepted','mapped','exact')
METHOD='cardmarket_ocg_certified_public_code_singleton_v2'
CONFIRM='APPLY_YUGIOH_OCG_STRUCTURE_DECK_SINGLETONS_V1'
EXPECTED_JA=36426; EXPECTED_CAPTURE='2026-08-18 21:09:20.740891+00:00'; EVIDENCE_SHA256='07442825adbebf570c237eb793b38a2f12072b930b2f7e8c7c9ebdd3bc3c15c7'
EXPECTED={'SD41':43,'SD40':38,'SD38':43,'SD36':44}; EXPECTED_TOTAL=168

def connect(readonly):
 url=os.getenv('DATABASE_URL_UNPOOLED') or os.getenv('DATABASE_URL')
 if not url: raise RuntimeError('DATABASE URL required')
 c=psycopg2.connect(url,connect_timeout=30,application_name='dontripit_ygo_ocg_structure_deck_singletons_apply_v1'); c.set_session(readonly=readonly,autocommit=False); return c

def derive(cur):
 if evidence_sha256()!=EVIDENCE_SHA256: raise RuntimeError('identity evidence hash drift')
 cur.execute("SELECT id FROM games WHERE slug=%s LIMIT 1",(GAME,)); gid=int(cur.fetchone()['id'])
 cur.execute("SELECT max(last_seen_at) capture FROM external_catalog_products WHERE source='cardmarket' AND game_id=%s",(gid,)); capture=cur.fetchone()['capture']
 if str(capture)!=EXPECTED_CAPTURE: raise RuntimeError({'capture_drift':str(capture)})
 cur.execute("""SELECT count(*) n FROM prints p JOIN cards c ON c.id=p.card_id WHERE c.game_id=%s AND lower(coalesce(p.language,''))='ja'""",(gid,)); ja=int(cur.fetchone()['n'])
 if ja!=EXPECTED_JA: raise RuntimeError({'ja_baseline_drift':ja})
 cur.execute("""SELECT e.metacard_external_id,p.card_id,count(*) evidence_links FROM external_catalog_print_links l JOIN external_catalog_products e ON e.id=l.external_product_id JOIN prints p ON p.id=l.print_id WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single' AND e.metacard_external_id IS NOT NULL AND l.link_status=ANY(%s) GROUP BY e.metacard_external_id,p.card_id""",(gid,list(ACCEPTED)))
 meta=defaultdict(set); ev=Counter()
 for r in cur.fetchall(): m=str(r['metacard_external_id']); cid=int(r['card_id']); meta[m].add(cid); ev[(m,cid)]+=int(r['evidence_links'] or 0)
 cur.execute("""SELECT e.external_id id_product,l.external_product_id,l.print_id,l.mapping_method,l.confidence,l.reviewed,l.link_status FROM external_catalog_print_links l JOIN external_catalog_products e ON e.id=l.external_product_id WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single' AND l.link_status=ANY(%s)""",(gid,list(ACCEPTED)))
 accepted=[dict(r) for r in cur.fetchall()]; bp=defaultdict(list); br=defaultdict(list)
 for r in accepted: bp[int(r['external_product_id'])].append(r); br[int(r['print_id'])].append(r)
 proposal=[]; existing=[]; reports=[]
 for code,cfg in TARGETS.items():
  cur.execute("""SELECT e.id external_product_id,e.external_id id_product,e.name,e.metacard_external_id FROM external_catalog_products e WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single' AND e.expansion_external_id=%s AND e.last_seen_at=%s ORDER BY e.metacard_external_id,e.external_id::bigint""",(gid,cfg['idExpansion'],capture)); products=[dict(r) for r in cur.fetchall()]
  cur.execute("""SELECT p.id print_id,p.card_id,p.collector_number,p.rarity,p.variant,c.name card_name FROM prints p JOIN cards c ON c.id=p.card_id JOIN sets s ON s.id=p.set_id WHERE c.game_id=%s AND upper(coalesce(s.code,''))=%s AND lower(coalesce(p.language,''))='ja' ORDER BY p.card_id,p.collector_number,p.id""",(gid,code)); prints=[dict(r) for r in cur.fetchall()]
  if (len(products),len(prints))!=(cfg['products'],cfg['prints']): raise RuntimeError({'surface_drift':code})
  pg=defaultdict(list); pc=defaultdict(list)
  for x in products: pg[str(x.get('metacard_external_id') or '')].append(x)
  for x in prints: pc[int(x['card_id'])].append(x)
  ex=new=0; pairs=[]
  for m,g in pg.items():
   cards=meta.get(m,set()) if m else set()
   if not m or len(cards)!=1: raise RuntimeError({'metacard_resolution_drift':code,'idMetacard':m,'cards':sorted(cards)})
   cid=next(iter(cards)); cprints=pc.get(cid,[])
   if len(g)!=len(cprints): raise RuntimeError({'group_cardinality_drift':code,'idMetacard':m})
   if len(g)!=1: continue
   prod=g[0]; pr=cprints[0]
   if norm(prod['name'])!=norm(pr['card_name']): raise RuntimeError({'name_drift':code,'idProduct':str(prod['id_product'])})
   eid=int(prod['external_product_id']); pid=int(pr['print_id']); pclaims=bp.get(eid,[]); rclaims=br.get(pid,[])
   if any(int(r['print_id'])!=pid for r in pclaims) or any(int(r['external_product_id'])!=eid for r in rclaims): raise RuntimeError({'accepted_identity_conflict':code,'idProduct':str(prod['id_product']),'print_id':pid})
   same=[r for r in pclaims if int(r['print_id'])==pid]
   if same:
    r=same[0]
    if len(same)!=1 or len(pclaims)!=1 or len(rclaims)!=1 or str(r.get('mapping_method') or '')!=METHOD or str(r.get('confidence') or '')!='exact' or not bool(r.get('reviewed')): raise RuntimeError({'unexpected_existing_pair':code,'idProduct':str(prod['id_product'])})
    ex+=1
   else: new+=1
   pairs.append({'set_code':code,'idExpansion':str(cfg['idExpansion']),'external_product_id':eid,'idProduct':str(prod['id_product']),'idMetacard':m,'print_id':pid,'card_id':cid,'card_name':str(pr['card_name']),'collector_number':str(pr['collector_number']),'canonical_rarity':pr.get('rarity'),'canonical_variant':pr.get('variant'),'metacard_evidence_links':int(ev.get((m,cid),0)),'already_same':bool(same)})
  expected=EXPECTED[code]
  if len(pairs)!=expected or (ex,new) not in ((0,expected),(expected,0)): raise RuntimeError({'singleton_surface_drift':code,'pairs':len(pairs),'existing':ex,'new':new})
  proposal.extend(x for x in pairs if not x['already_same']); existing.extend(x for x in pairs if x['already_same']); reports.append({'set_code':code,'idExpansion':str(cfg['idExpansion']),'pairs':expected,'existing_same':ex,'new_ready':new})
 if len(proposal)+len(existing)!=EXPECTED_TOTAL or (len(existing),len(proposal)) not in ((0,EXPECTED_TOTAL),(EXPECTED_TOTAL,0)): raise RuntimeError({'global_partial_state':{'existing':len(existing),'new':len(proposal)}})
 return {'gid':gid,'capture':capture,'ja':ja,'proposal':proposal,'existing':existing,'sets':reports}

def run(apply=False,confirm=''):
 if apply and confirm!=CONFIRM: raise RuntimeError(f'--apply requires --confirm {CONFIRM}')
 c=connect(not apply)
 try:
  with c.cursor(cursor_factory=RealDictCursor) as cur:
   s=derive(cur); report={'mode':'apply' if apply else 'dry_run','status':'pass','production_writes':0,'mapping_method':METHOD,'cardmarket_capture':str(s['capture']),'ja_baseline':s['ja'],'identity_evidence_sha256':EVIDENCE_SHA256,'certified_pairs':EXPECTED_TOTAL,'already_accepted_same_pair':len(s['existing']),'new_links_ready':len(s['proposal']),'sets':s['sets']}
   if not apply: c.rollback(); return report
   writes=0
   for x in s['proposal']:
    evidence={'source':'cardmarket_first_party_public_structure_deck_code+current_cardmarket_product_catalog+yugioh_canonical_physical_identity','identity_basis':['first_party_public_Structure_Deck_code','pinned_current_cardmarket_capture','accepted_metacard_to_logical_card_bridge','single_product_for_metacard','single_canonical_JA_print_for_card_in_exact_set','strict_normalized_name_match','global_product_and_print_unclaimed','global_one_to_one'],'identity_evidence_sha256':EVIDENCE_SHA256,'idExpansion':x['idExpansion'],'canonical_set':x['set_code'],'idProduct':x['idProduct'],'idMetacard':x['idMetacard'],'collector_number':x['collector_number'],'canonical_variant':x['canonical_variant'],'canonical_rarity':x['canonical_rarity'],'metacard_evidence_links':x['metacard_evidence_links']}
    cur.execute("""INSERT INTO external_catalog_print_links(external_product_id,print_id,mapping_method,confidence,link_status,reviewed,evidence) VALUES(%s,%s,%s,'exact','accepted',true,%s) ON CONFLICT(external_product_id,print_id) DO NOTHING""",(x['external_product_id'],x['print_id'],METHOD,Json(evidence)))
    if cur.rowcount!=1:
     raise RuntimeError({'insert_failed':x['idProduct']})
    writes+=1
   report['production_writes']=writes; c.commit(); return report
 except Exception: c.rollback(); raise
 finally: c.close()
def main():
 p=argparse.ArgumentParser(); p.add_argument('--apply',action='store_true'); p.add_argument('--confirm',default=''); p.add_argument('--report',type=Path,default=Path('/tmp/yugioh-ocg-structure-decks-singletons-apply-v1.json')); a=p.parse_args(); payload=run(a.apply,a.confirm); a.report.parent.mkdir(parents=True,exist_ok=True); text=json.dumps(payload,ensure_ascii=False,indent=2,sort_keys=True,default=str)+'\n'; a.report.write_text(text,encoding='utf-8'); print(text,end=''); return 0
if __name__=='__main__': raise SystemExit(main())
