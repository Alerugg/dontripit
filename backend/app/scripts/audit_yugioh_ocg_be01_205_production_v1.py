from __future__ import annotations

import json, os
from decimal import Decimal
from pathlib import Path

import psycopg2
from psycopg2.extras import RealDictCursor

from app.scripts.apply_yugioh_ocg_be01_205_v1 import (
    EXPECTED_CAPTURE, EXPECTED_TOTAL, ID_EXPANSION, METHOD, SET_CODE,
    STABLE_IDENTITY_SHA256, run as writer_run,
)

ACCEPTED=('accepted','mapped','exact')

def pos(v):
    try: return v is not None and Decimal(str(v))>0
    except Exception: return False

def meaningful(r): return any(pos(r.get(k)) for k in ('price_low','price_mid','price_market','price_last'))

def price_variant(r):
    v=str(r.get('variant') or '').lower()
    if 'etched' in v or 'glossy' in v: return None
    return 'foil' if bool(r.get('is_foil')) else 'nonfoil'

def main()->int:
    w=writer_run(False,''); failures=[]
    if w['status']!='pass' or w['production_writes']!=0 or w['stable_identity_sha256']!=STABLE_IDENTITY_SHA256: failures.append('writer_guard')
    if (w['already_accepted_same_pair'],w['new_links_ready'])!=(EXPECTED_TOTAL,0): failures.append('writer_not_idempotent_205')
    url=os.getenv('DATABASE_URL_UNPOOLED') or os.getenv('DATABASE_URL')
    if not url: raise RuntimeError('DATABASE URL required')
    c=psycopg2.connect(url,connect_timeout=30,application_name='dontripit_ygo_ocg_be01_205_proof_v1'); c.set_session(readonly=True,autocommit=False)
    try:
        with c.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT id FROM games WHERE slug='yugioh' LIMIT 1"); gid=int(cur.fetchone()['id'])
            cur.execute("SELECT max(last_seen_at) ts FROM external_catalog_products WHERE source='cardmarket' AND game_id=%s",(gid,)); capture=cur.fetchone()['ts']
            if str(capture)!=EXPECTED_CAPTURE: failures.append('capture_drift')
            cur.execute("SELECT max(mp.as_of) ts FROM external_market_price_snapshots mp JOIN external_catalog_products e ON e.id=mp.external_product_id WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single'",(gid,)); asof=cur.fetchone()['ts']
            cur.execute("""SELECT l.mapping_method,l.confidence,l.reviewed,e.id external_product_id,e.external_id id_product,e.expansion_external_id,e.last_seen_at,
                    p.id print_id,p.language,p.collector_number,p.variant,p.is_foil,s.code set_code
                FROM external_catalog_print_links l JOIN external_catalog_products e ON e.id=l.external_product_id
                JOIN prints p ON p.id=l.print_id JOIN sets s ON s.id=p.set_id
                WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single'
                  AND e.expansion_external_id=%s AND l.link_status=ANY(%s) AND l.mapping_method=%s
                ORDER BY e.external_id::bigint""",(gid,ID_EXPANSION,list(ACCEPTED),METHOD)); links=[dict(r) for r in cur.fetchall()]
            pids=[int(r['external_product_id']) for r in links]; printids=[int(r['print_id']) for r in links]; ext={}; canon={}
            if asof and pids:
                cur.execute("SELECT external_product_id,price_variant,price_low,price_mid,price_market,price_last FROM external_market_price_snapshots WHERE external_product_id=ANY(%s) AND currency='EUR' AND as_of=%s",(pids,asof))
                for r in cur.fetchall(): ext[(int(r['external_product_id']),str(r['price_variant']))]=dict(r)
            if asof and printids:
                cur.execute("""SELECT ps.entity_id print_id,ps.price_low,ps.price_mid,ps.price_market,ps.price_last,ps.raw_json
                    FROM price_snapshots ps JOIN price_sources src ON src.id=ps.source_id
                    WHERE src.name='cardmarket' AND ps.entity_type='print' AND ps.entity_id=ANY(%s) AND ps.currency='EUR' AND ps.as_of=%s""",(printids,asof))
                for r in cur.fetchall(): canon.setdefault(int(r['print_id']),[]).append(dict(r))
            c.rollback()
    finally: c.close()

    identity={
        'links':len(links),'products':len({int(r['external_product_id']) for r in links}),'prints':len({int(r['print_id']) for r in links}),
        'wrong_language':sum(str(r['language']).lower()!='ja' for r in links),
        'wrong_set':sum(str(r['set_code']).upper()!=SET_CODE for r in links),
        'wrong_method':sum(str(r['mapping_method'])!=METHOD or str(r['confidence'])!='exact' or not bool(r['reviewed']) for r in links),
        'stale':sum(str(r['last_seen_at'])!=EXPECTED_CAPTURE for r in links),
    }
    if (identity['links'],identity['products'],identity['prints'])!=(EXPECTED_TOTAL,EXPECTED_TOTAL,EXPECTED_TOTAL): failures.append('identity_cardinality')
    if any(identity[k] for k in ('wrong_language','wrong_set','wrong_method','stale')): failures.append('identity_quality')

    priceable=missing_ext=unsupported=canon_exact=missing_canon=wrong=0; samples=[]
    for r in links:
        pv=price_variant(r); er=None if pv is None else ext.get((int(r['external_product_id']),pv))
        if pv is None: unsupported+=1
        elif not er or not meaningful(er): missing_ext+=1
        else: priceable+=1
        cr=canon.get(int(r['print_id']),[])
        exact=[x for x in cr if str((x.get('raw_json') or {}).get('idProduct') or '')==str(r['id_product'])]
        mismatch=[x for x in cr if str((x.get('raw_json') or {}).get('idProduct') or '') not in ('',str(r['id_product']))]
        wrong+=bool(mismatch)
        if exact and any(meaningful(x) for x in exact): canon_exact+=1
        else:
            missing_canon+=1
            if len(samples)<20: samples.append({'collector_number':r['collector_number'],'idProduct':str(r['id_product']),'print_id':int(r['print_id']),'external_current_meaningful':bool(er and meaningful(er))})
    if wrong: failures.append(f'wrong_idProduct_{wrong}')
    if canon_exact!=priceable: failures.append(f'canonical_exact_{canon_exact}_external_priceable_{priceable}')

    report={'status':'pass' if not failures else 'fail','production_writes':0,'stable_identity_sha256':STABLE_IDENTITY_SHA256,'catalog_capture':str(capture),'price_guide_as_of':str(asof),'identity':identity,'pricing':{'externally_priceable_links':priceable,'missing_external_current_price':missing_ext,'unsupported_finish':unsupported,'canonical_current_exact_idProduct_prices':canon_exact,'missing_canonical_current_price':missing_canon,'canonical_current_wrong_idProduct':wrong},'failures':failures,'unpriced_samples':samples}
    out=Path(os.getenv('YGO_OCG_BE01_205_PROOF_OUTPUT','/tmp/ygo-ocg-be01-205-proof.json')); out.parent.mkdir(parents=True,exist_ok=True); out.write_text(json.dumps(report,ensure_ascii=False,indent=2,sort_keys=True,default=str)+'\n'); print(json.dumps(report,ensure_ascii=False,indent=2,sort_keys=True,default=str)); return 0 if not failures else 2

if __name__=='__main__': raise SystemExit(main())
