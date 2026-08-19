from __future__ import annotations

import html
import json
import os
import re
import time
import unicodedata
from collections import defaultdict
from pathlib import Path

import psycopg2
import requests
from psycopg2.extras import RealDictCursor

GAME='yugioh'
ACCEPTED=('accepted','mapped','exact')
USER_AGENT='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36'

TARGETS={
    'DOCS':{'idExpansion':'4680','products':108,'prints':108,'accepted':69},
    'LTGY':{'idExpansion':'4725','products':86,'prints':86,'accepted':75},
    'CSOC':{'idExpansion':'4809','products':87,'prints':87,'accepted':74},
}

# Only these physical rarity geometries are eligible in this pass.  DOCS
# Super->Secret and CSOC Common->Parallel are intentionally absent until a
# separate first-party anchor exists for those exact geometries.
CONTRACTS={
    'DOCS':{
        'ultra|secret|ultimate':('ultra','secret','ultimate'),
        'ultra|secret|ultimate|ghost':('ultra','secret','ultimate','ghost'),
    },
    'LTGY':{
        'ultra|ultimate':('ultra','ultimate'),
        'ultra|ultimate|ghost':('ultra','ultimate','ghost'),
    },
    'CSOC':{
        'ultra|ultimate':('ultra','ultimate'),
        'ultra|ultimate|ghost':('ultra','ultimate','ghost'),
    },
}

ANCHORS={
    'DOCS':[
        {
            'key':'scarlight_v1',
            'url':'https://www.cardmarket.com/en/YuGiOh/Products/Singles/Dimension-of-Chaos-Japanese/Scarlight-Red-Dragon-Archfiend-V1-Ultra-Rare',
            'contains':['Scarlight Red Dragon Archfiend (V.1 - Ultra Rare)','Dimension of Chaos (OCG)'],
        },
        {
            'key':'scarlight_v2',
            'url':'https://www.cardmarket.com/en/YuGiOh/Products/Singles/Dimension-of-Chaos-Japanese/Scarlight-Red-Dragon-Archfiend-V2-Secret-Rare',
            'contains':['Scarlight Red Dragon Archfiend (V.2 - Secret Rare)','Dimension of Chaos (OCG)'],
        },
        {
            'key':'scarlight_v4_with_v3_carousel',
            'url':'https://www.cardmarket.com/en/YuGiOh/Products/Singles/Dimension-of-Chaos-Japanese/Scarlight-Red-Dragon-Archfiend-V4-Holographic-Rare',
            'contains':['Scarlight Red Dragon Archfiend (V.4 - Holographic Rare)','Scarlight Red Dragon Archfiend (V.3 - Ultimate Rare)','Dimension of Chaos (OCG)'],
        },
    ],
    'LTGY':[
        {
            'key':'dracossack_v1',
            'url':'https://www.cardmarket.com/en/YuGiOh/Products/Singles/Lord-of-the-Tachyon-Galaxy-OCG/Mecha-Phantom-Beast-Dracossack-V1-Ultra-Rare',
            'contains':['Mecha Phantom Beast Dracossack (V.1 - Ultra Rare)','Lord of the Tachyon Galaxy (OCG)'],
        },
        {
            'key':'dracossack_v2',
            'url':'https://www.cardmarket.com/en/YuGiOh/Products/Singles/Lord-of-the-Tachyon-Galaxy-OCG/Mecha-Phantom-Beast-Dracossack-V2-Ultimate-Rare',
            'contains':['Mecha Phantom Beast Dracossack (V.2 - Ultimate Rare)','Lord of the Tachyon Galaxy (OCG)'],
        },
        {
            'key':'tachyon_v1',
            'url':'https://www.cardmarket.com/en/YuGiOh/Products/Singles/Lord-of-the-Tachyon-Galaxy-OCG/Number-107-Galaxy-Eyes-Tachyon-Dragon-V1-Ultra-Rare',
            'contains':['Number 107: Galaxy-Eyes Tachyon Dragon (V.1 - Ultra Rare)','Lord of the Tachyon Galaxy (OCG)'],
        },
        {
            'key':'tachyon_v2',
            'url':'https://www.cardmarket.com/en/YuGiOh/Products/Singles/Lord-of-the-Tachyon-Galaxy-OCG/Number-107-Galaxy-Eyes-Tachyon-Dragon-V2-Ultimate-Rare',
            'contains':['Number 107: Galaxy-Eyes Tachyon Dragon (V.2 - Ultimate Rare)','Lord of the Tachyon Galaxy (OCG)'],
        },
        {
            'key':'tachyon_v3',
            'url':'https://www.cardmarket.com/en/YuGiOh/Products/Singles/Lord-of-the-Tachyon-Galaxy-OCG/Number-107-Galaxy-Eyes-Tachyon-Dragon-V3-Holographic-Rare',
            'contains':['Number 107: Galaxy-Eyes Tachyon Dragon (V.3 - Holographic Rare)','Lord of the Tachyon Galaxy (OCG)'],
        },
    ],
    'CSOC':[
        {
            'key':'black_rose_v1',
            'url':'https://www.cardmarket.com/en/YuGiOh/Products/Singles/Crossroads-of-Chaos-Japanese/Black-Rose-Dragon-V1-Ultra-Rare',
            'contains':['Black Rose Dragon (V.1 - Ultra Rare)','Crossroads of Chaos (OCG)'],
        },
        {
            'key':'black_rose_v2',
            'url':'https://www.cardmarket.com/en/YuGiOh/Products/Singles/Crossroads-of-Chaos-Japanese/Black-Rose-Dragon-V2-Ultimate-Rare',
            'contains':['Black Rose Dragon (V.2 - Ultimate Rare)','Crossroads of Chaos (OCG)'],
        },
        {
            'key':'black_rose_v3',
            'url':'https://www.cardmarket.com/en/YuGiOh/Products/Singles/Crossroads-of-Chaos-Japanese/Black-Rose-Dragon-V3-Holographic-Rare',
            'contains':['Black Rose Dragon (V.3 - Holographic Rare)','Crossroads of Chaos (OCG)'],
        },
    ],
}


def norm(v: object)->str:
    text=unicodedata.normalize('NFKD',str(v or '')).casefold()
    return ''.join(ch for ch in text if ch.isalnum())


def rarity(v: object)->str:
    x=norm(v)
    aliases={
        'superrare':'super','super':'super',
        'ultrarare':'ultra','ultra':'ultra',
        'secretrare':'secret','secret':'secret',
        'ultimaterare':'ultimate','ultimate':'ultimate',
        'holographicrare':'ghost','ghostrare':'ghost','ghost':'ghost',
        'commonparallelrare':'commonparallel','parallelcommonrare':'commonparallel','commonparallel':'commonparallel',
        'common':'common','rare':'rare',
    }
    return aliases.get(x,x)


def rarity_key(values)->str:
    return '|'.join(sorted(rarity(v) for v in values))


def clean_html(body: str)->str:
    text=re.sub(r'<script\b[^>]*>.*?</script>',' ',body,flags=re.I|re.S)
    text=re.sub(r'<style\b[^>]*>.*?</style>',' ',text,flags=re.I|re.S)
    text=re.sub(r'<[^>]+>',' ',text)
    text=html.unescape(text)
    return re.sub(r'\s+',' ',text).strip()


def fetch_anchor(anchor: dict)->dict:
    last=None
    for attempt in range(1,3):
        try:
            r=requests.get(anchor['url'],headers={'User-Agent':USER_AGENT,'Accept':'text/html,application/xhtml+xml','Accept-Language':'en-US,en;q=0.9'},timeout=25,allow_redirects=True)
            text=clean_html(r.text[:2_000_000]) if r.text else ''
            missing=[token for token in anchor['contains'] if token not in text]
            if r.status_code==200 and not missing:
                return {'key':anchor['key'],'url':anchor['url'],'status':'certified','http_status':200,'missing_tokens':[],'body_chars':len(r.text)}
            if r.status_code in (403,429,503):
                last={'key':anchor['key'],'url':anchor['url'],'status':'inconclusive','http_status':r.status_code,'missing_tokens':missing,'body_chars':len(r.text)}
            else:
                last={'key':anchor['key'],'url':anchor['url'],'status':'not_certified','http_status':r.status_code,'missing_tokens':missing,'body_chars':len(r.text)}
            if attempt<2 and r.status_code in (403,429,503):
                time.sleep(3*attempt); continue
            return last
        except Exception as exc:
            last={'key':anchor['key'],'url':anchor['url'],'status':'inconclusive','http_status':None,'missing_tokens':list(anchor['contains']),'error':f'{type(exc).__name__}: {exc}'}
            if attempt<2: time.sleep(3*attempt); continue
            return last
    return last or {'key':anchor['key'],'url':anchor['url'],'status':'inconclusive'}


def main()->int:
    anchor_reports={code:[fetch_anchor(a) for a in anchors] for code,anchors in ANCHORS.items()}
    anchor_status={}
    for code,rows in anchor_reports.items():
        if all(r['status']=='certified' for r in rows): anchor_status[code]='certified'
        elif any(r['status']=='not_certified' for r in rows): anchor_status[code]='not_certified'
        else: anchor_status[code]='inconclusive'

    url=os.getenv('DATABASE_URL_UNPOOLED') or os.getenv('DATABASE_URL')
    if not url: raise RuntimeError('DATABASE URL required')
    conn=psycopg2.connect(url,connect_timeout=30,application_name='dontripit_ygo_ocg_first_party_version_contract_v1'); conn.set_session(readonly=True,autocommit=False)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT id FROM games WHERE slug=%s LIMIT 1",(GAME,)); gid=int(cur.fetchone()['id'])
            cur.execute("SELECT max(last_seen_at) capture FROM external_catalog_products WHERE source='cardmarket' AND game_id=%s",(gid,)); capture=cur.fetchone()['capture']
            if capture is None: raise RuntimeError('Cardmarket capture missing')
            cur.execute("""SELECT e.metacard_external_id,p.card_id FROM external_catalog_print_links l
                JOIN external_catalog_products e ON e.id=l.external_product_id JOIN prints p ON p.id=l.print_id
                WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single' AND e.metacard_external_id IS NOT NULL
                  AND l.link_status=ANY(%s) GROUP BY e.metacard_external_id,p.card_id""",(gid,list(ACCEPTED)))
            meta_cards=defaultdict(set)
            for r in cur.fetchall(): meta_cards[str(r['metacard_external_id'])].add(int(r['card_id']))
            cur.execute("""SELECT e.external_id id_product,l.external_product_id,l.print_id FROM external_catalog_print_links l
                JOIN external_catalog_products e ON e.id=l.external_product_id
                WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single' AND l.link_status=ANY(%s)""",(gid,list(ACCEPTED)))
            claims=[dict(r) for r in cur.fetchall()]; by_product=defaultdict(list); by_print=defaultdict(list)
            for r in claims: by_product[str(r['id_product'])].append(r); by_print[int(r['print_id'])].append(r)

            proposal=[]; set_reports=[]; blocked=[]
            for code,cfg in TARGETS.items():
                cur.execute("""SELECT e.id external_product_id,e.external_id id_product,e.name,e.metacard_external_id
                    FROM external_catalog_products e WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single'
                      AND e.expansion_external_id=%s AND e.last_seen_at=%s ORDER BY e.metacard_external_id,e.external_id::bigint""",(gid,cfg['idExpansion'],capture)); products=[dict(r) for r in cur.fetchall()]
                cur.execute("""SELECT p.id print_id,p.card_id,p.collector_number,p.rarity,p.variant,c.name card_name
                    FROM prints p JOIN cards c ON c.id=p.card_id JOIN sets s ON s.id=p.set_id
                    WHERE c.game_id=%s AND upper(coalesce(s.code,''))=%s AND lower(coalesce(p.language,''))='ja'
                    ORDER BY p.card_id,p.id""",(gid,code)); prints=[dict(r) for r in cur.fetchall()]
                if (len(products),len(prints))!=(cfg['products'],cfg['prints']): raise RuntimeError({'surface_drift':code,'products':len(products),'prints':len(prints)})
                product_ids={str(r['id_product']) for r in products}; print_ids={int(r['print_id']) for r in prints}
                accepted_here=[r for r in claims if str(r['id_product']) in product_ids and int(r['print_id']) in print_ids]
                if len(accepted_here)!=cfg['accepted']: raise RuntimeError({'accepted_drift':code,'actual':len(accepted_here),'expected':cfg['accepted']})
                pg=defaultdict(list); pc=defaultdict(list); names={}
                for r in products: pg[str(r.get('metacard_external_id') or '')].append(r)
                for r in prints: pc[int(r['card_id'])].append(r); names[int(r['card_id'])]=str(r['card_name'])
                set_pairs=[]; group_rows=[]
                for meta,group in pg.items():
                    if not meta or len(group)<2: continue
                    cards=meta_cards.get(meta,set())
                    if len(cards)!=1: continue
                    cid=next(iter(cards)); cprints=pc.get(cid,[])
                    residual_products=[r for r in group if not by_product.get(str(r['id_product']))]
                    residual_prints=[r for r in cprints if not by_print.get(int(r['print_id']))]
                    if not residual_products: continue
                    key=rarity_key(r['rarity'] for r in cprints); sequence=CONTRACTS.get(code,{}).get(key)
                    base={'set_code':code,'idMetacard':meta,'card_id':cid,'card_name':names.get(cid,''),'group_size':len(group),'rarity_key':key,'residual_products':len(residual_products),'residual_prints':len(residual_prints)}
                    if anchor_status[code]!='certified':
                        x={**base,'status':'blocked','reason':'first_party_version_anchor_'+anchor_status[code]}; group_rows.append(x); blocked.append(x); continue
                    if not sequence:
                        x={**base,'status':'blocked','reason':'rarity_geometry_not_first_party_certified'}; group_rows.append(x); blocked.append(x); continue
                    if len(group)!=len(cprints) or len(residual_products)!=len(residual_prints) or len(sequence)!=len(group):
                        x={**base,'status':'blocked','reason':'surface_cardinality_mismatch'}; group_rows.append(x); blocked.append(x); continue
                    ordered=sorted(group,key=lambda r:int(r['id_product'])); pr_by_rarity=defaultdict(list)
                    for r in cprints: pr_by_rarity[rarity(r['rarity'])].append(r)
                    if any(len(pr_by_rarity[x])!=1 for x in sequence):
                        x={**base,'status':'blocked','reason':'canonical_rarity_not_bijective'}; group_rows.append(x); blocked.append(x); continue
                    pairs=[]; bad=False
                    for ordinal,(prod,rar) in enumerate(zip(ordered,sequence),1):
                        pr=pr_by_rarity[rar][0]; pid=str(prod['id_product']); print_id=int(pr['print_id'])
                        pclaims=by_product.get(pid,[]); rclaims=by_print.get(print_id,[])
                        if pclaims or rclaims:
                            same=any(int(r['print_id'])==print_id for r in pclaims)
                            if not same or any(str(r['id_product'])!=pid for r in rclaims): bad=True; break
                            continue
                        pairs.append({'set_code':code,'idExpansion':cfg['idExpansion'],'idMetacard':meta,'idProduct':pid,'external_product_id':int(prod['external_product_id']),'product_ordinal':ordinal,'calibrated_rarity':rar,'print_id':print_id,'card_id':cid,'card_name':names[cid],'collector_number':str(pr['collector_number']),'canonical_rarity':str(pr['rarity']),'canonical_variant':str(pr['variant'] or ''),'contract_key':key})
                    if bad or len(pairs)!=len(residual_products):
                        x={**base,'status':'blocked','reason':'accepted_claim_conflict_or_partial_pairing'}; group_rows.append(x); blocked.append(x); continue
                    x={**base,'status':'first_party_version_contract_candidate','candidate_pairs':len(pairs),'sequence':list(sequence)}; group_rows.append(x); set_pairs.extend(pairs); proposal.extend(pairs)
                set_reports.append({'set_code':code,'idExpansion':cfg['idExpansion'],'anchor_status':anchor_status[code],'products':len(products),'prints':len(prints),'accepted':len(accepted_here),'residual':len(products)-len(accepted_here),'candidate_pairs':len(set_pairs),'groups':group_rows})
            conn.rollback()
    finally: conn.close()

    if len({r['idProduct'] for r in proposal})!=len(proposal) or len({r['print_id'] for r in proposal})!=len(proposal): raise RuntimeError('proposal not globally one-to-one')
    payload={'status':'pass','mode':'read_only','production_writes':0,'source':'cardmarket_first_party_public_product_pages+current_product_catalog+neon','cardmarket_capture':str(capture),'anchor_status':anchor_status,'anchors':anchor_reports,'contracts':{code:{k:list(v) for k,v in rows.items()} for code,rows in CONTRACTS.items()},'candidate_pairs':len(proposal),'candidate_products':len({r['idProduct'] for r in proposal}),'candidate_prints':len({r['print_id'] for r in proposal}),'sets':set_reports,'proposal':proposal,'blocked_groups':blocked}
    out=Path(os.getenv('YGO_OCG_FIRST_PARTY_VERSION_CONTRACT_OUTPUT','/tmp/yugioh-ocg-first-party-version-contract-v1.json')); out.parent.mkdir(parents=True,exist_ok=True); out.write_text(json.dumps(payload,ensure_ascii=False,indent=2,sort_keys=True,default=str)+'\n',encoding='utf-8'); print(json.dumps(payload,ensure_ascii=False,indent=2,sort_keys=True,default=str)); return 0


if __name__=='__main__': raise SystemExit(main())
