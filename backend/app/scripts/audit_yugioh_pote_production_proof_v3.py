from __future__ import annotations

import json
import os
from collections import Counter
from decimal import Decimal
from pathlib import Path

import psycopg2
from psycopg2.extras import RealDictCursor

ACCEPTED=("accepted","mapped","exact")
EXPECTED={
    "POTE":{"exp":"5044","set":"POTE","count":126,"methods":{
        "cardmarket_ocg_certified_unique_physical_v2":63,
        "cardmarket_ocg_certified_image_bijection_v2":24,
        "cardmarket_ocg_certified_version_ordinal_v1":39,
    }},
    "ALIN":{"exp":"6025","set":"ALIN","count":55,"methods":{"cardmarket_ocg_certified_unique_physical_v2":55}},
    "AGOV":{"exp":"5421","set":"AGOV","count":98,"methods":None},
}

def positive(v):
    try: return v is not None and Decimal(str(v))>0
    except Exception: return False

def meaningful(r):
    return any(positive(r.get(k)) for k in ("price_low","price_mid","price_market","price_last"))

def main()->int:
    url=os.getenv("DATABASE_URL_UNPOOLED") or os.getenv("DATABASE_URL")
    if not url: raise RuntimeError("DATABASE_URL_UNPOOLED or DATABASE_URL is required")
    conn=psycopg2.connect(url,connect_timeout=30,application_name="dontripit_ygo_pote_production_proof_v3")
    conn.set_session(readonly=True,autocommit=False)
    failures=[]; report={"production_writes":0,"surfaces":{}}
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT id FROM games WHERE slug='yugioh' LIMIT 1")
            game_id=int(cur.fetchone()["id"])
            cur.execute("SELECT max(last_seen_at) capture FROM external_catalog_products WHERE source='cardmarket'")
            capture=cur.fetchone()["capture"]
            cur.execute("""SELECT max(mp.as_of) ts FROM external_market_price_snapshots mp JOIN external_catalog_products e ON e.id=mp.external_product_id WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single'""",(game_id,))
            price_as_of=cur.fetchone()["ts"]
            report["cardmarket_capture"]=str(capture); report["price_guide_as_of"]=str(price_as_of)
            for label,cfg in EXPECTED.items():
                cur.execute("""SELECT l.mapping_method,l.confidence,l.reviewed,e.id external_product_id,e.external_id id_product,e.last_seen_at,p.id print_id,p.language,p.collector_number,p.variant,p.rarity,c.name card_name,s.code set_code FROM external_catalog_print_links l JOIN external_catalog_products e ON e.id=l.external_product_id JOIN prints p ON p.id=l.print_id JOIN cards c ON c.id=p.card_id JOIN sets s ON s.id=p.set_id WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single' AND e.expansion_external_id=%s AND l.link_status=ANY(%s) ORDER BY e.external_id::bigint""",(game_id,cfg["exp"],list(ACCEPTED)))
                links=[dict(r) for r in cur.fetchall()]
                n=len(links); products=len({str(r['id_product']) for r in links}); prints=len({int(r['print_id']) for r in links})
                methods=dict(Counter(str(r.get('mapping_method') or '') for r in links))
                wrong_lang=[r for r in links if str(r.get('language') or '').casefold()!='ja']
                wrong_set=[r for r in links if str(r.get('set_code') or '').upper()!=cfg['set']]
                stale=[r for r in links if capture is not None and r.get('last_seen_at')!=capture]
                wrong_conf=[r for r in links if str(r.get('confidence') or '')!='exact']
                unreviewed=[r for r in links if not bool(r.get('reviewed'))]
                if (n,products,prints)!=(cfg['count'],cfg['count'],cfg['count']): failures.append(f"{label}_identity_{n}_{products}_{prints}_expected_{cfg['count']}")
                if cfg['methods'] is not None and methods!=cfg['methods']: failures.append(f"{label}_methods_{methods}")
                if wrong_lang: failures.append(f"{label}_wrong_language_{len(wrong_lang)}")
                if wrong_set: failures.append(f"{label}_wrong_set_{len(wrong_set)}")
                if stale: failures.append(f"{label}_stale_{len(stale)}")
                if wrong_conf: failures.append(f"{label}_wrong_confidence_{len(wrong_conf)}")
                if unreviewed: failures.append(f"{label}_unreviewed_{len(unreviewed)}")

                ext_priceable=set(); canonical_priceable=set(); wrong_product=[]
                ext_ids=[int(r['external_product_id']) for r in links]; print_ids=[int(r['print_id']) for r in links]
                if ext_ids and price_as_of is not None:
                    cur.execute("""SELECT external_product_id,price_low,price_mid,price_market,price_last FROM external_market_price_snapshots WHERE external_product_id=ANY(%s) AND currency='EUR' AND as_of=%s""",(ext_ids,price_as_of))
                    for row in cur.fetchall():
                        d=dict(row)
                        if meaningful(d): ext_priceable.add(int(d['external_product_id']))
                if print_ids and price_as_of is not None:
                    link_by_print={int(r['print_id']):str(r['id_product']) for r in links}
                    cur.execute("""SELECT ps.entity_id print_id,ps.price_low,ps.price_mid,ps.price_market,ps.price_last,ps.raw_json FROM price_snapshots ps JOIN price_sources src ON src.id=ps.source_id WHERE src.name='cardmarket' AND ps.entity_type='print' AND ps.entity_id=ANY(%s) AND ps.currency='EUR' AND ps.as_of=%s""",(print_ids,price_as_of))
                    for row in cur.fetchall():
                        d=dict(row); pid=int(d['print_id']); actual=str((d.get('raw_json') or {}).get('idProduct') or ''); expected=link_by_print.get(pid)
                        if actual and actual!=expected: wrong_product.append({'print_id':pid,'expected':expected,'actual':actual})
                        elif actual==expected and meaningful(d): canonical_priceable.add(pid)
                if wrong_product: failures.append(f"{label}_wrong_canonical_idProduct_{len(wrong_product)}")
                if len(canonical_priceable)!=len(ext_priceable): failures.append(f"{label}_price_projection_{len(canonical_priceable)}_vs_external_{len(ext_priceable)}")
                unpriced=[{'idProduct':str(r['id_product']),'print_id':int(r['print_id']),'collector_number':r['collector_number'],'card_name':r['card_name'],'variant':r['variant']} for r in links if int(r['external_product_id']) not in ext_priceable]
                report['surfaces'][label]={
                    'accepted_links':n,'unique_products':products,'unique_prints':prints,'method_counts':methods,
                    'wrong_language':len(wrong_lang),'wrong_set':len(wrong_set),'stale_products':len(stale),
                    'wrong_confidence':len(wrong_conf),'unreviewed':len(unreviewed),
                    'externally_priceable':len(ext_priceable),'canonical_exact_idProduct_priceable':len(canonical_priceable),
                    'canonical_wrong_idProduct':len(wrong_product),'unpriced':unpriced,
                }
            conn.rollback()
    finally: conn.close()
    report['failures']=failures; report['status']='pass' if not failures else 'fail'
    out=Path(os.getenv('YGO_POTE_PRODUCTION_PROOF_V3_OUTPUT','/tmp/yugioh-pote-production-proof-v3.json'))
    text=json.dumps(report,ensure_ascii=False,indent=2,sort_keys=True,default=str)+'\n'; out.write_text(text,encoding='utf-8'); print(text,end='')
    return 0 if not failures else 2

if __name__=='__main__': raise SystemExit(main())
