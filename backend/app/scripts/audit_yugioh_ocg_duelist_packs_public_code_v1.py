from __future__ import annotations

import hashlib
import json
import os
import unicodedata
from collections import Counter
from pathlib import Path

import psycopg2
from psycopg2.extras import RealDictCursor

GAME='yugioh'
ACCEPTED=('accepted','mapped','exact')
EXPECTED_JA=36426

TARGETS={
    'DP23':{
        'idExpansion':'4569','products':56,'prints':56,
        'public_title':'Duelist Pack: Legend Duelist 6',
        'public_code':'DP23',
        'evidence_urls':[
            'https://www.cardmarket.com/en/YuGiOh/Products/Singles/Duelist-Pack-Legend-Duelist-6',
            'https://www.cardmarket.com/en/YuGiOh/Products/Singles/Duelist-Pack-Legend-Duelist-6/Generation-Next',
        ],
        'observed_names':['Dark Magician Girl','The Dark Magicians','Generation Next','Magicians Souls'],
    },
    'DP22':{
        'idExpansion':'4580','products':56,'prints':56,
        'public_title':'Duelist Pack: Legend Duelist 5',
        'public_code':'DP22',
        'evidence_urls':[
            'https://www.cardmarket.com/en/YuGiOh/Products/Singles/Duelist-Pack-Legend-Duelist-5',
            'https://www.cardmarket.com/en/YuGiOh/Products/Singles/Duelist-Pack-Legend-Duelist-5/Evil-HERO-Malicious-Bane',
        ],
        'observed_names':['Dark Sanctuary','Evil HERO Malicious Bane','Raigeki'],
    },
    'DP21':{
        'idExpansion':'4604','products':56,'prints':56,
        'public_title':'Duelist Pack: Legend Duelist 4',
        'public_code':'DP21',
        'evidence_urls':[
            'https://www.cardmarket.com/en/YuGiOh/Products/Singles/Duelist-Pack-Legend-Duelist-4',
            'https://www.cardmarket.com/en/YuGiOh/Products/Singles/Duelist-Pack-Legend-Duelist-4/Special-Schedule',
        ],
        'observed_names':['Harpie Lady Sisters','Red Rose Dragon','Harpie Perfumer','Special Schedule'],
    },
    'DP19':{
        'idExpansion':'4633','products':51,'prints':51,
        'public_title':'Duelist Pack: Legend Duelist 2',
        'public_code':'DP19',
        'evidence_urls':[
            'https://www.cardmarket.com/en/YuGiOh/Products/Singles/Duelist-Pack-Legend-Duelist-2/Relinquished',
            'https://www.cardmarket.com/en/YuGiOh/Products/Singles/Duelist-Pack-Legend-Duelist-2/Perfectly-Ultimate-Great-Moth',
            'https://www.cardmarket.com/en/YuGiOh/Products/Singles/Duelist-Pack-Legend-Duelist-2/Desperado-Barrel-Dragon',
        ],
        'observed_names':['Relinquished','Perfectly Ultimate Great Moth','Desperado Barrel Dragon'],
    },
}

FIRST_PARTY_EVIDENCE={
    'source':'Cardmarket first-party public Yu-Gi-Oh pages',
    'verified_at_utc':'2026-08-19',
    'scope':'OCG Duelist Pack public expansion/product codes only; numeric Cardmarket idExpansion candidates remain live-guarded',
    'targets':TARGETS,
    'observations':{
        'DP23':'Cardmarket expansion page title Duelist Pack: Legend Duelist 6 lists singles with DP23 prefix; direct product pages say Printed in Duelist Pack: Legend Duelist 6.',
        'DP22':'Cardmarket expansion page title Duelist Pack: Legend Duelist 5 lists singles with DP22 prefix; direct product pages say Printed in Duelist Pack: Legend Duelist 5.',
        'DP21':'Cardmarket expansion page title Duelist Pack: Legend Duelist 4 lists singles with DP21 prefix; direct product pages say Printed in Duelist Pack: Legend Duelist 4.',
        'DP19':'Multiple Cardmarket direct product pages are titled with (DP19) and say Printed in Duelist Pack: Legend Duelist 2.',
    },
}


def evidence_sha256()->str:
    raw=json.dumps(FIRST_PARTY_EVIDENCE,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode('utf-8')
    return hashlib.sha256(raw).hexdigest()


def norm(value: object)->str:
    text=unicodedata.normalize('NFKD',str(value or '')).casefold()
    return ''.join(ch for ch in text if ch.isalnum())


def main()->int:
    url=os.getenv('DATABASE_URL_UNPOOLED') or os.getenv('DATABASE_URL')
    if not url: raise RuntimeError('DATABASE URL required')
    conn=psycopg2.connect(url,connect_timeout=30,application_name='dontripit_ygo_ocg_duelist_packs_public_code_v1')
    conn.set_session(readonly=True,autocommit=False)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT id FROM games WHERE slug=%s LIMIT 1",(GAME,)); row=cur.fetchone()
            if not row: raise RuntimeError('Yu-Gi-Oh game missing')
            gid=int(row['id'])
            cur.execute("SELECT max(last_seen_at) capture FROM external_catalog_products WHERE source='cardmarket' AND game_id=%s",(gid,)); capture=cur.fetchone()['capture']
            if capture is None: raise RuntimeError('Cardmarket capture missing')
            cur.execute("""SELECT count(*) n FROM prints p JOIN cards c ON c.id=p.card_id WHERE c.game_id=%s AND lower(coalesce(p.language,''))='ja'""",(gid,)); ja=int(cur.fetchone()['n'])
            if ja!=EXPECTED_JA: raise RuntimeError({'ja_baseline_drift':ja})

            cur.execute("""SELECT e.metacard_external_id,p.card_id FROM external_catalog_print_links l
                JOIN external_catalog_products e ON e.id=l.external_product_id JOIN prints p ON p.id=l.print_id
                WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single' AND e.metacard_external_id IS NOT NULL
                  AND l.link_status=ANY(%s) GROUP BY e.metacard_external_id,p.card_id""",(gid,list(ACCEPTED)))
            meta_cards={}
            tmp={}
            from collections import defaultdict
            tmp=defaultdict(set)
            for r in cur.fetchall(): tmp[str(r['metacard_external_id'])].add(int(r['card_id']))
            meta_cards=dict(tmp)

            reports=[]
            for code,cfg in TARGETS.items():
                cur.execute("""SELECT e.id external_product_id,e.external_id id_product,e.name,e.metacard_external_id,e.expansion_external_id,e.last_seen_at
                    FROM external_catalog_products e
                    WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single'
                      AND e.expansion_external_id=%s AND e.last_seen_at=%s
                    ORDER BY e.metacard_external_id,e.external_id::bigint""",(gid,cfg['idExpansion'],capture)); products=[dict(r) for r in cur.fetchall()]
                cur.execute("""SELECT p.id print_id,p.card_id,p.collector_number,p.rarity,p.variant,c.name card_name,s.code set_code
                    FROM prints p JOIN cards c ON c.id=p.card_id JOIN sets s ON s.id=p.set_id
                    WHERE c.game_id=%s AND upper(coalesce(s.code,''))=%s AND lower(coalesce(p.language,''))='ja'
                    ORDER BY p.card_id,p.collector_number,p.id""",(gid,code)); prints=[dict(r) for r in cur.fetchall()]
                if (len(products),len(prints))!=(cfg['products'],cfg['prints']):
                    raise RuntimeError({'surface_count_drift':code,'products':len(products),'prints':len(prints),'expected':cfg})

                cur.execute("""SELECT count(*) n,count(DISTINCT l.external_product_id) products,count(DISTINCT l.print_id) prints
                    FROM external_catalog_print_links l JOIN external_catalog_products e ON e.id=l.external_product_id
                    JOIN prints p ON p.id=l.print_id JOIN sets s ON s.id=p.set_id
                    WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single' AND e.expansion_external_id=%s
                      AND l.link_status=ANY(%s) AND lower(coalesce(p.language,''))='ja' AND upper(coalesce(s.code,''))=%s""",(gid,cfg['idExpansion'],list(ACCEPTED),code)); x=cur.fetchone()
                accepted={'links':int(x['n']),'products':int(x['products']),'prints':int(x['prints'])}
                if accepted!={'links':0,'products':0,'prints':0}:
                    raise RuntimeError({'regional_candidate_already_claimed':code,'accepted':accepted})

                product_names=Counter(norm(r['name']) for r in products)
                print_names=Counter(norm(r['card_name']) for r in prints)
                product_metas=[str(r.get('metacard_external_id') or '') for r in products]
                resolved=0; resolved_into_set=0; ambiguous=0; unresolved=0
                set_card_ids={int(r['card_id']) for r in prints}
                for meta in product_metas:
                    cards=meta_cards.get(meta,set()) if meta else set()
                    if len(cards)==1:
                        resolved+=1
                        if next(iter(cards)) in set_card_ids: resolved_into_set+=1
                    elif len(cards)>1: ambiguous+=1
                    else: unresolved+=1

                observed_present={name:any(norm(name)==norm(r['name']) for r in products) for name in cfg['observed_names']}
                report={
                    'set_code':code,'idExpansion':cfg['idExpansion'],'public_code':cfg['public_code'],'public_title':cfg['public_title'],
                    'products':len(products),'canonical_ja_prints':len(prints),'accepted':accepted,
                    'unique_metacards':len(set(product_metas)),'blank_metacards':sum(not x for x in product_metas),
                    'unique_canonical_cards':len(set_card_ids),
                    'name_multiset_equal':product_names==print_names,
                    'product_only_names':sorted((product_names-print_names).elements())[:20],
                    'canonical_only_names':sorted((print_names-product_names).elements())[:20],
                    'global_metacard_resolution':{'unique':resolved,'into_exact_set':resolved_into_set,'ambiguous':ambiguous,'unresolved':unresolved},
                    'first_party_observed_names_present':observed_present,
                    'all_observed_names_present':all(observed_present.values()),
                    'candidate_certified':False,
                }
                report['candidate_certified']=(
                    report['name_multiset_equal']
                    and report['unique_metacards']==len(products)
                    and report['blank_metacards']==0
                    and report['unique_canonical_cards']==len(prints)
                    and resolved_into_set==len(products)
                    and ambiguous==0 and unresolved==0
                    and report['all_observed_names_present']
                )
                reports.append(report)
            conn.rollback()
    finally: conn.close()

    payload={
        'status':'pass' if all(r['candidate_certified'] for r in reports) else 'incomplete',
        'mode':'read_only','production_writes':0,'ja_baseline':ja,'cardmarket_capture':str(capture),
        'method':'frozen_first_party_public_expansion_code_plus_complete_current_product_to_exact_JA_name_and_metacard_bijection',
        'evidence_sha256':evidence_sha256(),'evidence':FIRST_PARTY_EVIDENCE,
        'certified_count':sum(r['candidate_certified'] for r in reports),'results':reports,
    }
    out=Path(os.getenv('YGO_OCG_DUELIST_PACKS_PUBLIC_CODE_OUTPUT','/tmp/yugioh-ocg-duelist-packs-public-code-v1.json'))
    out.parent.mkdir(parents=True,exist_ok=True); out.write_text(json.dumps(payload,ensure_ascii=False,indent=2,sort_keys=True,default=str)+'\n',encoding='utf-8'); print(json.dumps(payload,ensure_ascii=False,indent=2,sort_keys=True,default=str)); return 0


if __name__=='__main__': raise SystemExit(main())
