from __future__ import annotations

import json
import os
import re
from collections import Counter
from pathlib import Path

import psycopg2
import requests
from psycopg2.extras import RealDictCursor

from app.ingest.connectors.onepiece_v2 import OnePieceV2Connector

SOURCES={
    'en':'https://en.onepiece-cardgame.com/cardlist/',
    'ja':'https://www.onepiece-cardgame.com/cardlist/',
}
TARGET_SET='OP-16'
TARGET_COLLECTORS={'OP16-011','OP16-042'}


def norm_label(value):
    return re.sub(r'[^A-Z0-9]+','',str(value or '').upper())


def key(row):
    return (str(row.get('collector_number') or '').upper(),str(row.get('variant') or 'default').lower())


def fetch_surface(base_url: str) -> dict:
    timeout=float(os.getenv('ONEPIECE_HTTP_TIMEOUT','30'))
    headers={'User-Agent':'TCGCatalogV2/1.0 (+https://github.com/Alerugg/dontripit)'}
    connector=OnePieceV2Connector()
    index=requests.get(base_url,timeout=timeout,headers=headers); index.raise_for_status()
    options=connector._parse_official_series_options(index.text)
    candidates=[(sid,label) for sid,label in options if 'OP16' in norm_label(label)]
    if not candidates:
        raise RuntimeError({'op16_series_missing':base_url,'options':len(options)})
    rows=[]; selected=[]
    for sid,label in candidates:
        response=requests.get(f'{base_url}?series={sid}',timeout=timeout,headers=headers); response.raise_for_status()
        parsed=connector._parse_official_cards_page(response.text,base_url=base_url)
        target=[r for r in parsed if str(r.get('set_code') or '').upper()==TARGET_SET]
        if target:
            rows.extend(target); selected.append({'series_id':str(sid),'label':label,'entries':len(target)})
    by={}
    drift=[]
    for row in rows:
        k=key(row)
        item={
            'collector_number':k[0], 'variant':k[1], 'source_print_id':str(row.get('print_id') or ''),
            'name':str(row.get('name') or ''), 'rarity':str(row.get('rarity') or ''),
            'image_url':str(row.get('image_url') or ''),
        }
        if k in by and by[k]!=item: drift.append({'key':k,'first':by[k],'other':item})
        else: by[k]=item
    if drift: raise RuntimeError({'official_duplicate_drift':base_url,'rows':drift[:20]})
    return {'selected':selected,'by_key':by}


def main() -> int:
    official={lang:fetch_surface(url) for lang,url in SOURCES.items()}
    en=official['en']['by_key']; ja=official['ja']['by_key']
    if len(en)!=149 or len(ja)!=149:
        raise RuntimeError({'official_count_drift':{'en':len(en),'ja':len(ja)}})
    en_collectors={k[0] for k in en}; ja_collectors={k[0] for k in ja}
    if len(en_collectors)!=119 or en_collectors!=ja_collectors:
        raise RuntimeError({'collector_geometry_drift':{'en':len(en_collectors),'ja':len(ja_collectors),'only_en':sorted(en_collectors-ja_collectors),'only_ja':sorted(ja_collectors-en_collectors)}})
    only_en=sorted(set(en)-set(ja)); only_ja=sorted(set(ja)-set(en)); overlap=set(en)&set(ja)

    url=os.getenv('DATABASE_URL_UNPOOLED') or os.getenv('DATABASE_URL')
    if not url: raise RuntimeError('database url missing')
    conn=psycopg2.connect(url,connect_timeout=30,application_name='onepiece_op16_regional_variant_delta_v1')
    conn.set_session(readonly=True,autocommit=False)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT id FROM games WHERE slug='onepiece' LIMIT 1"); game_id=int(cur.fetchone()['id'])
            cur.execute("""SELECT p.id print_id,p.card_id,p.collector_number,p.variant,p.rarity,p.language,p.print_key,c.name card_name,s.id set_id,s.code set_code
                FROM prints p JOIN cards c ON c.id=p.card_id JOIN sets s ON s.id=p.set_id
                WHERE c.game_id=%s AND upper(replace(coalesce(s.code,''),'_','-')) IN ('OP16','OP-16')
                ORDER BY p.collector_number,p.variant,p.id""",(game_id,))
            neon=[dict(r) for r in cur.fetchall()]
            conn.rollback()
    finally: conn.close()
    neon_en=[r for r in neon if str(r.get('language') or '').lower()=='en']
    neon_en_by={key(r):r for r in neon_en}
    if len(neon_en_by)!=149: raise RuntimeError({'neon_en_key_count':len(neon_en_by)})

    official_en_vs_neon={
        'only_official_en':sorted(set(en)-set(neon_en_by)),
        'only_neon_en':sorted(set(neon_en_by)-set(en)),
        'rarity_mismatches':[],
    }
    for k in sorted(set(en)&set(neon_en_by)):
        if str(en[k]['rarity']).casefold()!=str(neon_en_by[k]['rarity']).casefold():
            official_en_vs_neon['rarity_mismatches'].append({'key':k,'official':en[k]['rarity'],'neon':neon_en_by[k]['rarity']})

    collector_card_ids={}
    collector_set_ids={}
    collector_invariants=[]
    for r in neon_en:
        collector=str(r['collector_number']).upper()
        collector_card_ids.setdefault(collector,set()).add(int(r['card_id']))
        collector_set_ids.setdefault(collector,set()).add(int(r['set_id']))
    for collector in sorted(ja_collectors):
        cards=collector_card_ids.get(collector,set()); sets=collector_set_ids.get(collector,set())
        if len(cards)!=1 or len(sets)!=1:
            collector_invariants.append({'collector':collector,'card_ids':sorted(cards),'set_ids':sorted(sets)})
    if collector_invariants: raise RuntimeError({'logical_collector_not_unique':collector_invariants})

    focus={}
    for collector in sorted(TARGET_COLLECTORS):
        focus[collector]={
            'official_en':[en[k] for k in sorted(en) if k[0]==collector],
            'official_ja':[ja[k] for k in sorted(ja) if k[0]==collector],
            'neon_en':[r for r in neon_en if str(r['collector_number']).upper()==collector],
            'logical_card_id':next(iter(collector_card_ids[collector])),
            'set_id':next(iter(collector_set_ids[collector])),
        }

    payload={
        'status':'pass','production_writes':0,
        'official_en_physical':len(en),'official_ja_physical':len(ja),'logical_collectors':len(en_collectors),
        'exact_key_overlap':len(overlap),'only_en_exact_keys':only_en,'only_ja_exact_keys':only_ja,
        'official_en_vs_neon':official_en_vs_neon,
        'all_collectors_resolve_one_logical_card':len(collector_invariants)==0,
        'regional_delta_focus':focus,
        'selected_series':{lang:official[lang]['selected'] for lang in official},
    }
    expected_only_en=[('OP16-011','p1')]; expected_only_ja=[('OP16-042','p1')]
    if only_en!=expected_only_en or only_ja!=expected_only_ja:
        raise RuntimeError({'unexpected_regional_variant_delta':{'only_en':only_en,'only_ja':only_ja}})
    if official_en_vs_neon['only_official_en'] or official_en_vs_neon['only_neon_en'] or official_en_vs_neon['rarity_mismatches']:
        raise RuntimeError({'Neon_EN_not_exact_official_EN':official_en_vs_neon})
    out=Path(os.getenv('ONEPIECE_OP16_REGIONAL_DELTA_OUTPUT','/tmp/onepiece-op16-regional-variant-delta-v1.json'))
    out.write_text(json.dumps(payload,ensure_ascii=False,indent=2,sort_keys=True,default=str)+'\n',encoding='utf-8')
    print(out.read_text(encoding='utf-8'),end='')
    return 0

if __name__=='__main__': raise SystemExit(main())
