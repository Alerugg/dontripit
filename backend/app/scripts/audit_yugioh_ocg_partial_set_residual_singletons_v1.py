from __future__ import annotations

import hashlib,json,os,unicodedata
from collections import Counter,defaultdict
from pathlib import Path
import psycopg2
from psycopg2.extras import RealDictCursor

GAME='yugioh'; ACCEPTED=('accepted','mapped','exact')
EXPECTED_JA=36426; EXPECTED_CAPTURE='2026-08-18 21:09:20.740891+00:00'
TARGETS={
 'ROTA':{'idExpansion':'5840','physical':132},
 'BLVO':{'idExpansion':'4546','physical':126},
 'CHIM':{'idExpansion':'4577','physical':119},
 'ETCO':{'idExpansion':'4563','physical':119},
 'CIBR':{'idExpansion':'4640','physical':103},
 'EXFO':{'idExpansion':'4634','physical':103},
 'FLOD':{'idExpansion':'4627','physical':103},
 'INOV':{'idExpansion':'4660','physical':103},
 'RATE':{'idExpansion':'4655','physical':103},
 'TDIL':{'idExpansion':'4666','physical':103},
 'CSOC':{'idExpansion':'4809','physical':87},
}

def norm(v):
 t=unicodedata.normalize('NFKD',str(v or '')).casefold(); return ''.join(ch for ch in t if ch.isalnum())

def main()->int:
 url=os.getenv('DATABASE_URL_UNPOOLED') or os.getenv('DATABASE_URL')
 if not url: raise RuntimeError('DATABASE URL required')
 conn=psycopg2.connect(url,connect_timeout=30,application_name='dontripit_ygo_ocg_partial_residual_singletons_v1'); conn.set_session(readonly=True,autocommit=False)
 try:
  with conn.cursor(cursor_factory=RealDictCursor) as cur:
   cur.execute("SELECT id FROM games WHERE slug=%s LIMIT 1",(GAME,)); gid=int(cur.fetchone()['id'])
   cur.execute("SELECT max(last_seen_at) capture FROM external_catalog_products WHERE source='cardmarket' AND game_id=%s",(gid,)); capture=cur.fetchone()['capture']
   if str(capture)!=EXPECTED_CAPTURE: raise RuntimeError({'capture_drift':str(capture)})
   cur.execute("SELECT count(*) n FROM prints p JOIN cards c ON c.id=p.card_id WHERE c.game_id=%s AND lower(coalesce(p.language,''))='ja'",(gid,)); ja=int(cur.fetchone()['n'])
   if ja!=EXPECTED_JA: raise RuntimeError({'ja_baseline_drift':ja})
   # Accepted metacard -> logical Card bridge across all exact historical evidence.
   cur.execute("""SELECT e.metacard_external_id,p.card_id,count(*) n FROM external_catalog_print_links l JOIN external_catalog_products e ON e.id=l.external_product_id JOIN prints p ON p.id=l.print_id WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single' AND e.metacard_external_id IS NOT NULL AND l.link_status=ANY(%s) GROUP BY e.metacard_external_id,p.card_id""",(gid,list(ACCEPTED)))
   meta=defaultdict(set); ev=Counter()
   for r in cur.fetchall(): m=str(r['metacard_external_id']); cid=int(r['card_id']); meta[m].add(cid); ev[(m,cid)]+=int(r['n'] or 0)
   # All accepted claims are global conflict guards.
   cur.execute("""SELECT l.external_product_id,l.print_id,e.external_id id_product,p.card_id,s.code set_code,p.language,l.mapping_method,l.confidence,l.reviewed FROM external_catalog_print_links l JOIN external_catalog_products e ON e.id=l.external_product_id JOIN prints p ON p.id=l.print_id JOIN sets s ON s.id=p.set_id WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single' AND l.link_status=ANY(%s)""",(gid,list(ACCEPTED)))
   bp=defaultdict(list); br=defaultdict(list)
   for r in cur.fetchall(): row=dict(r); bp[int(row['external_product_id'])].append(row); br[int(row['print_id'])].append(row)
   proposal=[]; reports=[]; ambiguous=[]
   for code,cfg in TARGETS.items():
    exp=str(cfg['idExpansion']); expected=int(cfg['physical'])
    cur.execute("""SELECT e.id external_product_id,e.external_id id_product,e.name,e.metacard_external_id FROM external_catalog_products e WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single' AND e.expansion_external_id=%s AND e.last_seen_at=%s ORDER BY e.metacard_external_id,e.external_id::bigint""",(gid,exp,capture)); products=[dict(r) for r in cur.fetchall()]
    cur.execute("""SELECT p.id print_id,p.card_id,p.collector_number,p.rarity,p.variant,c.name card_name FROM prints p JOIN cards c ON c.id=p.card_id JOIN sets s ON s.id=p.set_id WHERE c.game_id=%s AND upper(coalesce(s.code,''))=%s AND lower(coalesce(p.language,''))='ja' ORDER BY p.card_id,p.id""",(gid,code)); prints=[dict(r) for r in cur.fetchall()]
    if len(products)!=expected or len(prints)!=expected: raise RuntimeError({'surface_count_drift':code,'products':len(products),'prints':len(prints),'expected':expected})
    pg=defaultdict(list); cg=defaultdict(list); resolved=Counter(); canonical=Counter(int(x['card_id']) for x in prints)
    for p in products:
     m=str(p.get('metacard_external_id') or ''); cards=meta.get(m,set())
     if not m or len(cards)!=1: raise RuntimeError({'metacard_resolution_drift':code,'idProduct':str(p['id_product']),'idMetacard':m,'cards':sorted(cards)})
     cid=next(iter(cards)); p['resolved_card_id']=cid; pg[m].append(p); resolved[cid]+=1
    for pr in prints: cg[int(pr['card_id'])].append(pr)
    if resolved!=canonical: raise RuntimeError({'full_physical_card_multiset_drift':code,'resolved':dict(resolved),'canonical':dict(canonical)})
    if Counter(norm(x['name']) for x in products)!=Counter(norm(x['card_name']) for x in prints): raise RuntimeError({'full_name_multiset_drift':code})
    # Every pre-existing claim on a target product must already point to the exact resolved card / target JA set.
    existing_target=0
    for p in products:
     claims=bp.get(int(p['external_product_id']),[])
     if len(claims)>1: raise RuntimeError({'multiple_product_claims':code,'idProduct':str(p['id_product']),'claims':len(claims)})
     if claims:
      r=claims[0]; existing_target+=1
      if int(r['card_id'])!=int(p['resolved_card_id']) or str(r['set_code']).upper()!=code or str(r['language']).lower()!='ja': raise RuntimeError({'existing_target_claim_mismatch':code,'idProduct':str(p['id_product']),'claim':r})
    pairs=[]; group_residual=[]
    for m,g in pg.items():
     cid=int(g[0]['resolved_card_id']); cprints=cg[cid]
     if any(norm(p['name'])!=norm(cprints[0]['card_name']) for p in g): raise RuntimeError({'group_name_drift':code,'idMetacard':m})
     unclaimed_products=[p for p in g if not bp.get(int(p['external_product_id']))]
     unclaimed_prints=[pr for pr in cprints if not br.get(int(pr['print_id']))]
     # Claimed products/prints within the group must balance; otherwise another expansion owns a target print or vice versa.
     if len(unclaimed_products)!=len(unclaimed_prints):
      raise RuntimeError({'residual_group_cardinality_conflict':code,'idMetacard':m,'products':len(g),'prints':len(cprints),'unclaimed_products':len(unclaimed_products),'unclaimed_prints':len(unclaimed_prints)})
     if len(unclaimed_products)==1:
      p=unclaimed_products[0]; pr=unclaimed_prints[0]
      row={'set_code':code,'idExpansion':exp,'idMetacard':m,'card_id':cid,'card_name':str(pr['card_name']),'external_product_id':int(p['external_product_id']),'idProduct':str(p['id_product']),'print_id':int(pr['print_id']),'collector_number':str(pr['collector_number']),'canonical_rarity':pr.get('rarity'),'canonical_variant':pr.get('variant'),'metacard_evidence_links':int(ev.get((m,cid),0))}
      pairs.append(row); proposal.append(row)
     elif len(unclaimed_products)>1:
      group_residual.append({'set_code':code,'idExpansion':exp,'idMetacard':m,'card_id':cid,'card_name':str(cprints[0]['card_name']),'unclaimed_products':len(unclaimed_products),'unclaimed_prints':len(unclaimed_prints),'idProducts':[str(x['id_product']) for x in unclaimed_products],'prints':[{'print_id':int(x['print_id']),'collector_number':str(x['collector_number']),'rarity':x.get('rarity'),'variant':x.get('variant')} for x in unclaimed_prints]})
    residual_physical=sum(x['unclaimed_products'] for x in group_residual)
    reports.append({'set_code':code,'idExpansion':exp,'physical':expected,'existing_accepted_target_links':existing_target,'deterministic_residual_singletons':len(pairs),'ambiguous_residual_groups':len(group_residual),'ambiguous_residual_physical':residual_physical,'total_residual_physical':len(pairs)+residual_physical})
    ambiguous.extend(group_residual)
   conn.rollback()
 finally: conn.close()
 if len({x['external_product_id'] for x in proposal})!=len(proposal) or len({x['print_id'] for x in proposal})!=len(proposal): raise RuntimeError('global_residual_singletons_not_one_to_one')
 if not all(x['metacard_evidence_links']>0 for x in proposal): raise RuntimeError('residual_singleton_without_accepted_metacard_evidence')
 stable_rows=[{k:v for k,v in x.items() if k!='metacard_evidence_links'} for x in proposal]
 stable=hashlib.sha256(json.dumps(stable_rows,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()
 payload={'status':'pass','mode':'read_only','production_writes':0,'cardmarket_capture':str(capture),'ja_baseline':ja,'screened_sets':len(TARGETS),'certified_residual_singleton_pairs':len(proposal),'stable_identity_sha256':stable,'sets':reports,'ambiguous_residual_groups':ambiguous,'proposal':proposal}
 out=Path(os.getenv('YGO_OCG_PARTIAL_RESIDUAL_SINGLETONS_OUTPUT','/tmp/ygo-ocg-partial-residual-singletons-v1.json')); out.parent.mkdir(parents=True,exist_ok=True); out.write_text(json.dumps(payload,ensure_ascii=False,indent=2,sort_keys=True,default=str)+'\n',encoding='utf-8'); print(json.dumps(payload,ensure_ascii=False,indent=2,sort_keys=True,default=str)); return 0
if __name__=='__main__': raise SystemExit(main())
