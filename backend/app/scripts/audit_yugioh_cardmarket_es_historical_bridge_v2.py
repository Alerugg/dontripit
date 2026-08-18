from __future__ import annotations

import json
import os
import re
from collections import Counter, defaultdict

import psycopg2
from psycopg2.extras import RealDictCursor

from app.scripts.audit_yugioh_cardmarket_es_bridge_v1 import ACCEPTED, _physical_match


def _connect():
    url=os.getenv('DATABASE_URL_UNPOOLED') or os.getenv('DATABASE_URL')
    if not url: raise RuntimeError('DATABASE_URL required')
    conn=psycopg2.connect(url,connect_timeout=30,application_name='dontripit_ygo_es_historical_cm_readonly')
    conn.set_session(readonly=True,autocommit=False)
    return conn


def _raw(value):
    return str(value or '').upper().strip().replace(' ','')


def _extended_key(value, language):
    raw=_raw(value)
    token='SP' if language=='es' else 'EN'
    return re.sub(rf'-{token}(?=[A-Z0-9])','-XX',raw,count=1)


def _legacy_single_key(value, language):
    raw=_raw(value)
    token='S' if language=='es' else 'E'
    return re.sub(rf'-{token}(?=[A-Z0-9])','-X',raw,count=1)


def _base(row, collector_key):
    return (int(row['card_id']),str(row.get('set_code') or '').upper(),collector_key,bool(row.get('is_foil')))


def main():
    conn=_connect()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT id FROM games WHERE slug='yugioh' LIMIT 1")
            gid=int(cur.fetchone()['id'])
            cur.execute("""
              SELECT p.id AS print_id,p.card_id,p.language,p.collector_number,p.rarity,p.is_foil,p.variant,
                     s.code AS set_code,s.region AS set_region,s.name AS set_name
              FROM prints p JOIN cards c ON c.id=p.card_id JOIN sets s ON s.id=p.set_id
              WHERE c.game_id=%s AND lower(coalesce(p.language,'')) IN ('en','es') ORDER BY p.id
            """,(gid,))
            rows=[dict(r) for r in cur.fetchall()]
            cur.execute("""
              SELECT l.print_id,e.id AS market_row_id,e.external_id AS id_product,e.name,e.website_path,
                     l.link_status,l.confidence,l.mapping_method
              FROM external_catalog_print_links l JOIN external_catalog_products e ON e.id=l.external_product_id
              WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single'
                AND l.link_status = ANY(%s)
              ORDER BY l.print_id,e.id
            """,(gid,list(ACCEPTED)))
            links=[dict(r) for r in cur.fetchall()]
            conn.rollback()
    finally:
        conn.close()

    links_by_print=defaultdict(list)
    for link in links: links_by_print[int(link['print_id'])].append(link)
    en=[r for r in rows if str(r.get('language') or '').lower()=='en']
    es=[r for r in rows if str(r.get('language') or '').lower()=='es']
    indices={'extended':defaultdict(list),'legacy_single':defaultdict(list)}
    for row in en:
        indices['extended'][_base(row,_extended_key(row.get('collector_number'),'en'))].append(row)
        indices['legacy_single'][_base(row,_legacy_single_key(row.get('collector_number'),'en'))].append(row)

    buckets=Counter(); proposals=[]; samples=defaultdict(list)
    for row in es:
        existing=links_by_print.get(int(row['print_id']),[])
        if existing:
            buckets['already_has_accepted_product']+=1
            continue
        raw=_raw(row.get('collector_number'))
        candidate_rows={}
        modes=[]
        if re.search(r'-SP(?=[A-Z])',raw):
            modes.append('extended')
        if re.search(r'-S(?=[A-Z0-9])',raw) and not re.search(r'-SP',raw):
            modes.append('legacy_single')
        if not modes:
            buckets['not_historical_pattern_v2']+=1
            continue
        for mode in modes:
            keyfn=_extended_key if mode=='extended' else _legacy_single_key
            for candidate in indices[mode].get(_base(row,keyfn(row.get('collector_number'),'es')),[]):
                if _physical_match(row,candidate):
                    candidate_rows[int(candidate['print_id'])]=(mode,candidate)
        if not candidate_rows:
            buckets['no_physical_en_candidate']+=1
            if len(samples['no_physical_en_candidate'])<20: samples['no_physical_en_candidate'].append(row)
            continue
        if len(candidate_rows)!=1:
            buckets['ambiguous_en_candidate']+=1
            if len(samples['ambiguous_en_candidate'])<20:
                samples['ambiguous_en_candidate'].append({'es':row,'candidates':[v[1] for v in candidate_rows.values()]})
            continue
        en_print_id,(mode,enrow)=next(iter(candidate_rows.items()))
        market={int(x['market_row_id']):x for x in links_by_print.get(en_print_id,[])}
        if not market:
            buckets['en_candidate_without_cardmarket']+=1
            continue
        if len(market)!=1:
            buckets['en_cardmarket_ambiguous']+=1
            continue
        mid,mlink=next(iter(market.items()))
        buckets[f'deterministic_{mode}']+=1
        proposals.append({
          'mode':mode,'es_print_id':int(row['print_id']),'en_print_id':en_print_id,'card_id':int(row['card_id']),
          'set_code':row.get('set_code'),'es_collector':row.get('collector_number'),'en_collector':enrow.get('collector_number'),
          'rarity':row.get('rarity'),'variant':row.get('variant'),'market_row_id':mid,
          'id_product':str(mlink.get('id_product') or ''),'market_name':mlink.get('name'),'website_path':mlink.get('website_path')
        })
    product_cards=defaultdict(set); proposal_by_es=defaultdict(set)
    for p in proposals:
        product_cards[p['market_row_id']].add(p['card_id']); proposal_by_es[p['es_print_id']].add(p['market_row_id'])
    gates={
      'production_read_only':True,
      'no_ambiguous_en_candidates':buckets['ambiguous_en_candidate']==0,
      'no_ambiguous_en_cardmarket_products':buckets['en_cardmarket_ambiguous']==0,
      'one_product_per_es':all(len(v)==1 for v in proposal_by_es.values()),
      'one_logical_card_per_product':all(len(v)==1 for v in product_cards.values()),
      'proposals_present':bool(proposals),
    }
    report={
      'status':'pass' if all(gates.values()) else 'blocked','production_writes':0,
      'counts':{'es_prints':len(es),'proposal_count':len(proposals),'distinct_products':len({p['market_row_id'] for p in proposals}),**dict(sorted(buckets.items()))},
      'gates':gates,'proposal_samples':proposals[:80],'bucket_samples':dict(samples),
      'rules':{
        'extended':'Spanish -SP[A-Z...] <-> English -EN[A-Z...] preserving suffix',
        'legacy_single':'Spanish -S[...] <-> English -E[...] preserving suffix',
      }
    }
    out=os.getenv('YGO_ES_HISTORICAL_CM_AUDIT_OUTPUT','/tmp/yugioh-cardmarket-es-historical-bridge-v2.json')
    with open(out,'w',encoding='utf-8') as f: json.dump(report,f,ensure_ascii=False,indent=2,sort_keys=True,default=str); f.write('\n')
    print(json.dumps(report,ensure_ascii=False,indent=2,sort_keys=True,default=str))
    return 0 if report['status']=='pass' else 2

if __name__=='__main__': raise SystemExit(main())
