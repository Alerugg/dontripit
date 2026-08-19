from __future__ import annotations

import json,os
from decimal import Decimal
from pathlib import Path
import psycopg2
from psycopg2.extras import RealDictCursor

GAME='yugioh'; ACCEPTED=('accepted','mapped','exact'); METHOD='cardmarket_ocg_certified_unique_physical_v7'; EXPECTED=293
SURFACES={'4660':('INOV',64),'4666':('TDIL',64),'4702':('NECH',82),'4709':('PRIO',83)}

def positive(v):
 try: return v is not None and Decimal(str(v))>0
 except Exception: return False

def meaningful(r): return any(positive(r.get(k)) for k in ('price_low','price_mid','price_market','price_last'))
def price_variant(r):
 v=str(r.get('variant') or '').lower()
 if 'etched' in v or 'glossy' in v: return None
 return 'foil' if bool(r.get('is_foil')) else 'nonfoil'

def main()->int:
 url=os.getenv('DATABASE_URL_UNPOOLED') or os.getenv('DATABASE_URL')
 if not url: raise RuntimeError('DATABASE URL required')
 conn=psycopg2.connect(url,connect_timeout=30,application_name='dontripit_ygo_ocg_singletons_proof_v5'); conn.set_session(readonly=True,autocommit=False)
 try:
  with conn.cursor(cursor_factory=RealDictCursor) as cur:
   cur.execute("SELECT id FROM games WHERE slug=%s LIMIT 1",(GAME,)); gid=int(cur.fetchone()['id'])
   cur.execute("SELECT max(last_seen_at) ts FROM external_catalog_products WHERE source='cardmarket' AND game_id=%s",(gid,)); capture=cur.fetchone()['ts']
   cur.execute("""SELECT max(mp.as_of) ts FROM external_market_price_snapshots mp JOIN external_catalog_products e ON e.id=mp.external_product_id WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single'""",(gid,)); asof=cur.fetchone()['ts']
   cur.execute("""SELECT l.mapping_method,l.confidence,l.reviewed,e.id external_product_id,e.external_id id_product,e.expansion_external_id,e.last_seen_at,p.id print_id,p.language,p.collector_number,p.variant,p.is_foil,s.code set_code,c.name card_name FROM external_catalog_print_links l JOIN external_catalog_products e ON e.id=l.external_product_id JOIN prints p ON p.id=l.print_id JOIN sets s ON s.id=p.set_id JOIN cards c ON c.id=p.card_id WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single' AND e.expansion_external_id=ANY(%s) AND l.link_status=ANY(%s) ORDER BY e.expansion_external_id,e.external_id::bigint""",(gid,list(SURFACES),list(ACCEPTED))); links=[dict(r) for r in cur.fetchall()]
   pids=[int(r['external_product_id']) for r in links]; printids=[int(r['print_id']) for r in links]
   ext={}
   if asof and pids:
    cur.execute("""SELECT external_product_id,price_variant,price_low,price_mid,price_market,price_last FROM external_market_price_snapshots WHERE external_product_id=ANY(%s) AND currency='EUR' AND as_of=%s""",(pids,asof))
    for r in cur.fetchall(): ext[(int(r['external_product_id']),str(r['price_variant']))]=dict(r)
   canon={}
   if asof and printids:
    cur.execute("""SELECT ps.entity_id print_id,ps.price_low,ps.price_mid,ps.price_market,ps.price_last,ps.raw_json FROM price_snapshots ps JOIN price_sources src ON src.id=ps.source_id WHERE src.name='cardmarket' AND ps.entity_type='print' AND ps.entity_id=ANY(%s) AND ps.currency='EUR' AND ps.as_of=%s""",(printids,asof))
    for r in cur.fetchall(): canon.setdefault(int(r['print_id']),[]).append(dict(r))
   conn.rollback()
 finally: conn.close()
 failures=[]; per={}
 for exp,(code,n) in SURFACES.items():
  rows=[r for r in links if str(r['expansion_external_id'])==exp]
  vals={'links':len(rows),'products':len({int(r['external_product_id']) for r in rows}),'prints':len({int(r['print_id']) for r in rows}),'wrong_language':sum(str(r['language']).lower()!='ja' for r in rows),'wrong_set':sum(str(r['set_code']).upper()!=code for r in rows),'wrong_method':sum(str(r['mapping_method'])!=METHOD or str(r['confidence'])!='exact' or not bool(r['reviewed']) for r in rows),'stale':sum(capture is not None and r['last_seen_at']!=capture for r in rows)}
  if (vals['links'],vals['products'],vals['prints'])!=(n,n,n): failures.append(f'{code}_identity_count_drift')
  if vals['wrong_language'] or vals['wrong_set'] or vals['wrong_method'] or vals['stale']: failures.append(f'{code}_identity_property_drift')
  per[code]={'idExpansion':exp,**vals}
 if len(links)!=EXPECTED or len({int(r['external_product_id']) for r in links})!=EXPECTED or len({int(r['print_id']) for r in links})!=EXPECTED: failures.append('global_identity_not_293_one_to_one')
 priceable=missing_ext=unsupported=canon_exact=missing_canon=wrong_product=0; unpriced=[]
 for r in links:
  pv=price_variant(r); er=None if pv is None else ext.get((int(r['external_product_id']),pv))
  if pv is None: unsupported+=1
  elif not er or not meaningful(er): missing_ext+=1
  else: priceable+=1
  cr=canon.get(int(r['print_id']),[]); exact=[x for x in cr if str((x.get('raw_json') or {}).get('idProduct') or '')==str(r['id_product'])]; mismatch=[x for x in cr if str((x.get('raw_json') or {}).get('idProduct') or '') not in ('',str(r['id_product']))]
  wrong_product+=bool(mismatch)
  if exact and any(meaningful(x) for x in exact): canon_exact+=1
  else:
   missing_canon+=1
   if len(unpriced)<20: unpriced.append({'set_code':r['set_code'],'collector_number':r['collector_number'],'card_name':r['card_name'],'idProduct':str(r['id_product']),'print_id':int(r['print_id']),'external_current_meaningful':bool(er and meaningful(er))})
 if wrong_product: failures.append(f'canonical_wrong_idProduct_{wrong_product}')
 if canon_exact!=priceable: failures.append(f'canonical_exact_{canon_exact}_external_priceable_{priceable}')
 report={'status':'pass' if not failures else 'fail','production_writes':0,'mapping_method':METHOD,'catalog_capture':str(capture),'price_guide_as_of':str(asof),'accepted_links':len(links),'unique_products':len({int(r['external_product_id']) for r in links}),'unique_prints':len({int(r['print_id']) for r in links}),'sets':per,'pricing':{'externally_priceable_links':priceable,'missing_external_current_price':missing_ext,'unsupported_finish':unsupported,'canonical_current_exact_idProduct_prices':canon_exact,'missing_canonical_current_price':missing_canon,'canonical_current_wrong_idProduct':wrong_product},'failures':failures,'unpriced_samples':unpriced}
 out=Path(os.getenv('YGO_OCG_CERTIFIED_SINGLETONS_PROOF_V5_OUTPUT','/tmp/yugioh-ocg-certified-singletons-production-v5.json')); out.parent.mkdir(parents=True,exist_ok=True); out.write_text(json.dumps(report,ensure_ascii=False,indent=2,sort_keys=True,default=str)+'\n',encoding='utf-8'); print(json.dumps(report,ensure_ascii=False,indent=2,sort_keys=True,default=str)); return 0 if not failures else 2

if __name__=='__main__': raise SystemExit(main())
