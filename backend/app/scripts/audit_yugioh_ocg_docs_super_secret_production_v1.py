from __future__ import annotations

import json
import os
from decimal import Decimal
from pathlib import Path

import psycopg2
from psycopg2.extras import RealDictCursor

GAME='yugioh'
ACCEPTED=('accepted','mapped','exact')
METHOD='cardmarket_ocg_certified_public_super_secret_contract_v1'
EXPECTED=20
ID_EXPANSION='4680'
SET_CODE='DOCS'
REGIONAL_TOTAL=108


def positive(value):
    try: return value is not None and Decimal(str(value))>0
    except Exception: return False


def meaningful(row):
    return any(positive(row.get(k)) for k in ('price_low','price_mid','price_market','price_last'))


def price_variant(row):
    variant=str(row.get('variant') or '').lower()
    if 'etched' in variant or 'glossy' in variant: return None
    return 'foil' if bool(row.get('is_foil')) else 'nonfoil'


def main()->int:
    url=os.getenv('DATABASE_URL_UNPOOLED') or os.getenv('DATABASE_URL')
    if not url: raise RuntimeError('DATABASE URL required')
    conn=psycopg2.connect(url,connect_timeout=30,application_name='dontripit_ygo_ocg_docs_super_secret_proof_v1'); conn.set_session(readonly=True,autocommit=False)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT id FROM games WHERE slug=%s LIMIT 1",(GAME,)); gid=int(cur.fetchone()['id'])
            cur.execute("SELECT max(last_seen_at) ts FROM external_catalog_products WHERE source='cardmarket' AND game_id=%s",(gid,)); capture=cur.fetchone()['ts']
            cur.execute("""SELECT max(mp.as_of) ts FROM external_market_price_snapshots mp JOIN external_catalog_products e ON e.id=mp.external_product_id WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single'""",(gid,)); asof=cur.fetchone()['ts']
            cur.execute("""SELECT l.mapping_method,l.confidence,l.reviewed,e.id external_product_id,e.external_id id_product,e.expansion_external_id,e.last_seen_at,p.id print_id,p.language,p.collector_number,p.variant,p.is_foil,s.code set_code,c.name card_name
                FROM external_catalog_print_links l JOIN external_catalog_products e ON e.id=l.external_product_id JOIN prints p ON p.id=l.print_id JOIN sets s ON s.id=p.set_id JOIN cards c ON c.id=p.card_id
                WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single' AND l.mapping_method=%s AND l.link_status=ANY(%s)
                ORDER BY e.external_id::bigint""",(gid,METHOD,list(ACCEPTED))); links=[dict(r) for r in cur.fetchall()]
            pids=[int(r['external_product_id']) for r in links]; printids=[int(r['print_id']) for r in links]
            ext={}
            if asof and pids:
                cur.execute("""SELECT external_product_id,price_variant,price_low,price_mid,price_market,price_last FROM external_market_price_snapshots WHERE external_product_id=ANY(%s) AND currency='EUR' AND as_of=%s""",(pids,asof))
                for r in cur.fetchall(): ext[(int(r['external_product_id']),str(r['price_variant']))]=dict(r)
            canon={}
            if asof and printids:
                cur.execute("""SELECT ps.entity_id print_id,ps.price_low,ps.price_mid,ps.price_market,ps.price_last,ps.raw_json FROM price_snapshots ps JOIN price_sources src ON src.id=ps.source_id WHERE src.name='cardmarket' AND ps.entity_type='print' AND ps.entity_id=ANY(%s) AND ps.currency='EUR' AND ps.as_of=%s""",(printids,asof))
                for r in cur.fetchall(): canon.setdefault(int(r['print_id']),[]).append(dict(r))
            cur.execute("""SELECT count(*) total,count(DISTINCT l.external_product_id) products,count(DISTINCT l.print_id) prints,
                    count(*) FILTER (WHERE lower(coalesce(p.language,''))='ja' AND upper(coalesce(s.code,''))=%s) good,
                    count(*) FILTER (WHERE e.last_seen_at=%s) current
                FROM external_catalog_print_links l JOIN external_catalog_products e ON e.id=l.external_product_id JOIN prints p ON p.id=l.print_id JOIN sets s ON s.id=p.set_id
                WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single' AND e.expansion_external_id=%s AND l.link_status=ANY(%s)""",(SET_CODE,capture,gid,ID_EXPANSION,list(ACCEPTED))); regional=cur.fetchone()
            conn.rollback()
    finally: conn.close()

    failures=[]
    if len(links)!=EXPECTED or len({int(r['external_product_id']) for r in links})!=EXPECTED or len({int(r['print_id']) for r in links})!=EXPECTED: failures.append('method_identity_not_20_one_to_one')
    wrong_language=sum(str(r['language']).lower()!='ja' for r in links); wrong_set=sum(str(r['set_code']).upper()!=SET_CODE for r in links); wrong_expansion=sum(str(r['expansion_external_id'])!=ID_EXPANSION for r in links); wrong_method=sum(str(r['mapping_method'])!=METHOD or str(r['confidence'])!='exact' or not bool(r['reviewed']) for r in links); stale=sum(capture is not None and r['last_seen_at']!=capture for r in links)
    if wrong_language or wrong_set or wrong_expansion or wrong_method or stale: failures.append('method_identity_property_drift')
    vals=(int(regional['total']),int(regional['products']),int(regional['prints']),int(regional['good']),int(regional['current']))
    if vals!=(REGIONAL_TOTAL,REGIONAL_TOTAL,REGIONAL_TOTAL,REGIONAL_TOTAL,REGIONAL_TOTAL): failures.append('DOCS_regional_surface_not_complete_108')

    priceable=missing_ext=unsupported=canon_exact=missing_canon=wrong_product=0; unpriced=[]
    for r in links:
        pv=price_variant(r); er=None if pv is None else ext.get((int(r['external_product_id']),pv))
        if pv is None: unsupported+=1
        elif not er or not meaningful(er): missing_ext+=1
        else: priceable+=1
        current=canon.get(int(r['print_id']),[])
        exact=[x for x in current if str((x.get('raw_json') or {}).get('idProduct') or '')==str(r['id_product'])]
        mismatch=[x for x in current if str((x.get('raw_json') or {}).get('idProduct') or '') not in ('',str(r['id_product']))]
        wrong_product+=bool(mismatch)
        if exact and any(meaningful(x) for x in exact): canon_exact+=1
        else:
            missing_canon+=1
            if len(unpriced)<20: unpriced.append({'collector_number':r['collector_number'],'card_name':r['card_name'],'idProduct':str(r['id_product']),'print_id':int(r['print_id']),'external_current_meaningful':bool(er and meaningful(er))})
    if wrong_product: failures.append(f'canonical_wrong_idProduct_{wrong_product}')
    if canon_exact!=priceable: failures.append(f'canonical_exact_{canon_exact}_external_priceable_{priceable}')

    report={'status':'pass' if not failures else 'fail','production_writes':0,'mapping_method':METHOD,'catalog_capture':str(capture),'price_guide_as_of':str(asof),'accepted_links':len(links),'unique_products':len({int(r['external_product_id']) for r in links}),'unique_prints':len({int(r['print_id']) for r in links}),'identity':{'wrong_language':wrong_language,'wrong_set':wrong_set,'wrong_expansion':wrong_expansion,'wrong_method':wrong_method,'stale':stale},'regional_DOCS':{'accepted_links':int(regional['total']),'unique_products':int(regional['products']),'unique_prints':int(regional['prints']),'exact_JA_set_links':int(regional['good']),'current_capture_links':int(regional['current'])},'pricing':{'externally_priceable_links':priceable,'missing_external_current_price':missing_ext,'unsupported_finish':unsupported,'canonical_current_exact_idProduct_prices':canon_exact,'missing_canonical_current_price':missing_canon,'canonical_current_wrong_idProduct':wrong_product},'failures':failures,'unpriced_samples':unpriced}
    out=Path(os.getenv('YGO_OCG_DOCS_SUPER_SECRET_PROOF_OUTPUT','/tmp/yugioh-ocg-docs-super-secret-production-v1.json')); out.parent.mkdir(parents=True,exist_ok=True); out.write_text(json.dumps(report,ensure_ascii=False,indent=2,sort_keys=True,default=str)+'\n',encoding='utf-8'); print(json.dumps(report,ensure_ascii=False,indent=2,sort_keys=True,default=str)); return 0 if not failures else 2


if __name__=='__main__': raise SystemExit(main())
