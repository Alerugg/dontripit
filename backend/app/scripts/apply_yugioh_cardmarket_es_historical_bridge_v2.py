from __future__ import annotations

import argparse,json,os,re
from collections import Counter,defaultdict
import psycopg2
from psycopg2.extras import Json,execute_values
from app.scripts.audit_yugioh_cardmarket_es_bridge_v1 import ACCEPTED,_physical_match

METHOD='ygo_es_historical_exact_en_cardmarket_product_v2'
EXPECTED=1159

def _raw(v): return str(v or '').upper().strip().replace(' ','')
def _ext(v,lang): return re.sub(rf'-{"SP" if lang=="es" else "EN"}(?=[A-Z0-9])','-XX',_raw(v),count=1)
def _single(v,lang): return re.sub(rf'-{"S" if lang=="es" else "E"}(?=[A-Z0-9])','-X',_raw(v),count=1)
def _base(r,key): return (int(r['card_id']),str(r.get('set_code') or '').upper(),key,bool(r.get('is_foil')))

def _load(cur):
    cur.execute("SELECT id FROM games WHERE slug='yugioh' LIMIT 1"); gid=int(cur.fetchone()[0])
    cur.execute("""SELECT p.id AS print_id,p.card_id,p.language,p.collector_number,p.rarity,p.is_foil,p.variant,s.code AS set_code
      FROM prints p JOIN cards c ON c.id=p.card_id JOIN sets s ON s.id=p.set_id
      WHERE c.game_id=%s AND lower(coalesce(p.language,'')) IN ('en','es') ORDER BY p.id""",(gid,))
    cols=[d[0] for d in cur.description]; rows=[dict(zip(cols,r)) for r in cur.fetchall()]
    cur.execute("""SELECT l.print_id,e.id AS market_row_id,e.external_id AS id_product,e.name,e.website_path,l.link_status,l.confidence
      FROM external_catalog_print_links l JOIN external_catalog_products e ON e.id=l.external_product_id
      WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single' AND l.link_status=ANY(%s)
      ORDER BY l.print_id,e.id""",(gid,list(ACCEPTED)))
    cols=[d[0] for d in cur.description]; links=[dict(zip(cols,r)) for r in cur.fetchall()]
    return gid,rows,links

def _plan(rows,links):
    byprint=defaultdict(list)
    for x in links: byprint[int(x['print_id'])].append(x)
    en=[r for r in rows if str(r.get('language') or '').lower()=='en']; es=[r for r in rows if str(r.get('language') or '').lower()=='es']
    idx={'extended':defaultdict(list),'legacy_single':defaultdict(list)}
    for r in en:
        idx['extended'][_base(r,_ext(r.get('collector_number'),'en'))].append(r)
        idx['legacy_single'][_base(r,_single(r.get('collector_number'),'en'))].append(r)
    b=Counter(); props=[]
    for r in es:
        if byprint.get(int(r['print_id'])): b['already_linked']+=1; continue
        raw=_raw(r.get('collector_number')); modes=[]
        if re.search(r'-SP(?=[A-Z])',raw): modes.append('extended')
        if re.search(r'-S(?=[A-Z0-9])',raw) and not re.search(r'-SP',raw): modes.append('legacy_single')
        if not modes: b['unsupported']+=1; continue
        candidates={}
        for mode in modes:
            fn=_ext if mode=='extended' else _single
            for e in idx[mode].get(_base(r,fn(r.get('collector_number'),'es')),[]):
                if _physical_match(r,e): candidates[int(e['print_id'])]=(mode,e)
        if len(candidates)==0: b['no_en']+=1; continue
        if len(candidates)!=1: b['ambiguous_en']+=1; continue
        enid,(mode,erow)=next(iter(candidates.items())); market={int(x['market_row_id']):x for x in byprint.get(enid,[])}
        if len(market)==0: b['en_without_market']+=1; continue
        if len(market)!=1: b['ambiguous_market']+=1; continue
        mid,m=next(iter(market.items()))
        props.append({'mode':mode,'external_product_id':mid,'es_print_id':int(r['print_id']),'en_print_id':enid,'card_id':int(r['card_id']),'set_code':r.get('set_code'),'es_collector':r.get('collector_number'),'en_collector':erow.get('collector_number'),'rarity':r.get('rarity'),'variant':r.get('variant'),'id_product':str(m.get('id_product') or '')})
    b['proposal']=len(props); return {'proposals':props,'buckets':dict(sorted(b.items()))}

def _multi(cur,gid):
    cur.execute("""WITH x AS (SELECT l.external_product_id,COUNT(DISTINCT lower(p.language)) n FROM external_catalog_print_links l
      JOIN external_catalog_products e ON e.id=l.external_product_id JOIN prints p ON p.id=l.print_id
      WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single' AND l.link_status=ANY(%s)
      AND lower(coalesce(p.language,'')) IN ('en','es') GROUP BY l.external_product_id) SELECT COUNT(*) FROM x WHERE n>1""",(gid,list(ACCEPTED)))
    return int(cur.fetchone()[0] or 0)

def run(apply,expected,output):
    url=os.getenv('DATABASE_URL_UNPOOLED') or os.getenv('DATABASE_URL'); assert url
    conn=psycopg2.connect(url,connect_timeout=30,application_name='dontripit_ygo_es_historical_cm_v2')
    conn.set_session(isolation_level='SERIALIZABLE',readonly=False,autocommit=False); committed=False
    try:
      with conn.cursor() as cur:
        gid,rows,links=_load(cur); before=_plan(rows,links); before_multi=_multi(cur,gid)
        if before['buckets'].get('ambiguous_en',0) or before['buckets'].get('ambiguous_market',0): raise AssertionError(before['buckets'])
        if len(before['proposals'])!=expected: raise AssertionError(f"expected {expected}, got {len(before['proposals'])}: {before['buckets']}")
        writes=0
        if apply:
          vals=[]
          for p in before['proposals']:
            vals.append((p['external_product_id'],p['es_print_id'],METHOD,'exact','accepted',False,Json({'bridge_version':2,'mode':p['mode'],'source_en_print_id':p['en_print_id'],'card_id':p['card_id'],'set_code':p['set_code'],'es_collector':p['es_collector'],'en_collector':p['en_collector'],'rarity':p['rarity'],'variant':p['variant'],'id_product':p['id_product']})))
          execute_values(cur,"""INSERT INTO external_catalog_print_links (external_product_id,print_id,mapping_method,confidence,link_status,reviewed,evidence) VALUES %s
            ON CONFLICT (external_product_id,print_id) DO UPDATE SET mapping_method=EXCLUDED.mapping_method,confidence=EXCLUDED.confidence,link_status=EXCLUDED.link_status,reviewed=EXCLUDED.reviewed,evidence=EXCLUDED.evidence,updated_at=now()""",vals,page_size=1000); writes=len(vals)
          _,rows2,links2=_load(cur); after=_plan(rows2,links2); after_multi=_multi(cur,gid)
          if after['proposals'] or after['buckets'].get('ambiguous_en',0) or after['buckets'].get('ambiguous_market',0): raise AssertionError(after['buckets'])
          if after_multi-before_multi!=expected: raise AssertionError((before_multi,after_multi,expected))
          conn.commit(); committed=True
        else:
          after=None; after_multi=before_multi+expected; conn.rollback()
      report={'status':'pass','apply':bool(apply),'commit_confirmed':committed,'production_writes':writes if committed else 0,'expected_initial_proposals':expected,'before':{'proposal_count':len(before['proposals']),'buckets':before['buckets'],'multilingual_products':before_multi},'after':{'proposal_count':len(after['proposals']) if after else 0,'buckets':after['buckets'] if after else None,'multilingual_products':after_multi},'economics_untouched':True,'catalog_identity_untouched':True,'images_untouched':True,'ja_untouched':True}
    except Exception: conn.rollback(); raise
    finally: conn.close()
    with open(output,'w') as f: json.dump(report,f,indent=2,sort_keys=True); f.write('\n')
    print(json.dumps(report,indent=2,sort_keys=True)); return report

def main():
    p=argparse.ArgumentParser(); p.add_argument('--apply',action='store_true'); p.add_argument('--expected-proposals',type=int,default=EXPECTED); p.add_argument('--output',default='/tmp/yugioh-cardmarket-es-historical-apply-v2.json'); a=p.parse_args(); run(a.apply,a.expected_proposals,a.output); return 0
if __name__=='__main__': raise SystemExit(main())
