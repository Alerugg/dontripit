from __future__ import annotations

import hashlib
import json
import os
import unicodedata
from collections import defaultdict
from pathlib import Path

import psycopg2
from psycopg2.extras import RealDictCursor

GAME='yugioh'
SET_CODE='DOCS'
ID_EXPANSION='4680'
ACCEPTED=('accepted','mapped','exact')
EXPECTED_JA=36426
EXPECTED_PRODUCTS=108
EXPECTED_PRINTS=108
EXPECTED_ACCEPTED=88
EXPECTED_GROUPS=10
EXPECTED_PAIRS=20
EXPECTED_METACARDS={
    '220961','220978','220985','220989','221004',
    '221006','221017','221041','221393','225872',
}

FIRST_PARTY_EVIDENCE={
    'source':'Cardmarket first-party public Yu-Gi-Oh product page',
    'verified_at_utc':'2026-08-19',
    'anchor_url':'https://www.cardmarket.com/en/YuGiOh/Products/Singles/Dimension-of-Chaos-Japanese/Graydle-Dragon-V1-Super-Rare',
    'observed':[
        'Graydle Dragon (V.1 - Super Rare)',
        'Dimension of Chaos (OCG)',
        'The same first-party product page exposes Graydle Dragon (V.2 - Secret Rare) as the second OCG physical version.',
    ],
    'corroborating_url':'https://www.cardmarket.com/de/YuGiOh/Products/Singles/Dimension-of-Chaos-Japanese/Traptrix-Rafflesia-V2-Secret-Rare',
    'corroborating_observed':'Traptrix Rafflesia (V.2 - Secret Rare) — Dimension of Chaos (OCG)',
    'contract':{
        'rarity_geometry':['secret','super'],
        'idProduct_numeric_ordinal_to_rarity':['super','secret'],
        'scope':'Dimension of Chaos (OCG) / DOCS-JP two-version Super+Secret metacard groups only',
    },
}


def evidence_sha256()->str:
    raw=json.dumps(FIRST_PARTY_EVIDENCE,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode('utf-8')
    return hashlib.sha256(raw).hexdigest()


def norm(value: object)->str:
    text=unicodedata.normalize('NFKD',str(value or '')).casefold()
    return ''.join(ch for ch in text if ch.isalnum())


def rarity(value: object)->str:
    raw=norm(value)
    aliases={
        'superrare':'super','super':'super',
        'secretrare':'secret','secret':'secret',
    }
    return aliases.get(raw,raw)


def main()->int:
    url=os.getenv('DATABASE_URL_UNPOOLED') or os.getenv('DATABASE_URL')
    if not url:
        raise RuntimeError('DATABASE URL required')
    conn=psycopg2.connect(url,connect_timeout=30,application_name='dontripit_ygo_ocg_docs_super_secret_audit_v1')
    conn.set_session(readonly=True,autocommit=False)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT id FROM games WHERE slug=%s LIMIT 1",(GAME,)); game=cur.fetchone()
            if not game: raise RuntimeError('Yu-Gi-Oh game missing')
            gid=int(game['id'])
            cur.execute("SELECT max(last_seen_at) capture FROM external_catalog_products WHERE source='cardmarket' AND game_id=%s",(gid,)); capture=cur.fetchone()['capture']
            if capture is None: raise RuntimeError('Cardmarket capture missing')
            cur.execute("""SELECT count(*) n FROM prints p JOIN cards c ON c.id=p.card_id
                WHERE c.game_id=%s AND lower(coalesce(p.language,''))='ja'""",(gid,)); ja=int(cur.fetchone()['n'])
            if ja!=EXPECTED_JA: raise RuntimeError({'ja_baseline_drift':ja})

            cur.execute("""SELECT e.id external_product_id,e.external_id id_product,e.name,e.metacard_external_id,e.expansion_external_id
                FROM external_catalog_products e
                WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single'
                  AND e.expansion_external_id=%s AND e.last_seen_at=%s
                ORDER BY e.metacard_external_id,e.external_id::bigint""",(gid,ID_EXPANSION,capture)); products=[dict(r) for r in cur.fetchall()]
            cur.execute("""SELECT p.id print_id,p.card_id,p.collector_number,p.rarity,p.variant,p.language,c.name card_name,s.code set_code
                FROM prints p JOIN cards c ON c.id=p.card_id JOIN sets s ON s.id=p.set_id
                WHERE c.game_id=%s AND upper(coalesce(s.code,''))=%s AND lower(coalesce(p.language,''))='ja'
                ORDER BY p.card_id,p.collector_number,p.id""",(gid,SET_CODE)); prints=[dict(r) for r in cur.fetchall()]
            if (len(products),len(prints))!=(EXPECTED_PRODUCTS,EXPECTED_PRINTS):
                raise RuntimeError({'DOCS_surface_drift':{'products':len(products),'prints':len(prints)}})

            # Accepted global claims are hard exclusions and also supply the metacard->logical Card bridge.
            cur.execute("""SELECT e.external_id id_product,e.metacard_external_id,l.external_product_id,l.print_id,l.mapping_method,l.confidence,l.reviewed,
                       p.card_id
                FROM external_catalog_print_links l JOIN external_catalog_products e ON e.id=l.external_product_id
                JOIN prints p ON p.id=l.print_id
                WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single' AND l.link_status=ANY(%s)""",(gid,list(ACCEPTED))); accepted=[dict(r) for r in cur.fetchall()]
            by_product=defaultdict(list); by_print=defaultdict(list); meta_cards=defaultdict(set)
            for r in accepted:
                by_product[int(r['external_product_id'])].append(r)
                by_print[int(r['print_id'])].append(r)
                meta=str(r.get('metacard_external_id') or '')
                if meta: meta_cards[meta].add(int(r['card_id']))

            regional_product_ids={int(r['external_product_id']) for r in products}; regional_print_ids={int(r['print_id']) for r in prints}
            accepted_here=[r for r in accepted if int(r['external_product_id']) in regional_product_ids and int(r['print_id']) in regional_print_ids]
            if len(accepted_here)!=EXPECTED_ACCEPTED:
                raise RuntimeError({'DOCS_accepted_surface_drift':{'actual':len(accepted_here),'expected':EXPECTED_ACCEPTED}})
            if len({int(r['external_product_id']) for r in accepted_here})!=EXPECTED_ACCEPTED or len({int(r['print_id']) for r in accepted_here})!=EXPECTED_ACCEPTED:
                raise RuntimeError('DOCS accepted surface is not one-to-one')

            products_by_meta=defaultdict(list); prints_by_card=defaultdict(list); card_names={}
            for r in products: products_by_meta[str(r.get('metacard_external_id') or '')].append(r)
            for r in prints:
                cid=int(r['card_id']); prints_by_card[cid].append(r); card_names[cid]=str(r['card_name'])

            proposal=[]; groups=[]; residual_metas=set()
            for meta,group in products_by_meta.items():
                if not meta: continue
                unclaimed_products=[r for r in group if not by_product.get(int(r['external_product_id']))]
                if not unclaimed_products: continue
                residual_metas.add(meta)
                cards=meta_cards.get(meta,set())
                if len(cards)!=1:
                    raise RuntimeError({'residual_metacard_not_globally_unique':meta,'card_ids':sorted(cards)})
                cid=next(iter(cards)); cprints=prints_by_card.get(cid,[])
                unclaimed_prints=[r for r in cprints if not by_print.get(int(r['print_id']))]
                base={'idMetacard':meta,'card_id':cid,'card_name':card_names.get(cid,''),'product_count':len(group),'print_count':len(cprints),'residual_products':len(unclaimed_products),'residual_prints':len(unclaimed_prints)}
                if len(group)!=2 or len(cprints)!=2 or len(unclaimed_products)!=2 or len(unclaimed_prints)!=2:
                    raise RuntimeError({'residual_group_not_exact_two_by_two':base})
                if {rarity(r['rarity']) for r in cprints}!={'super','secret'}:
                    raise RuntimeError({'residual_group_wrong_rarity_geometry':{**base,'rarities':[str(r['rarity']) for r in cprints]}})
                if any(norm(r['name'])!=norm(card_names[cid]) for r in group):
                    raise RuntimeError({'residual_product_name_drift':{**base,'product_names':[str(r['name']) for r in group]}})

                ordered=sorted(group,key=lambda r:int(r['id_product']))
                print_by_rarity=defaultdict(list)
                for r in cprints: print_by_rarity[rarity(r['rarity'])].append(r)
                if any(len(print_by_rarity[x])!=1 for x in ('super','secret')):
                    raise RuntimeError({'residual_rarity_not_bijective':base})
                pairs=[]
                for ordinal,(prod,expected_rarity) in enumerate(zip(ordered,('super','secret')),1):
                    pr=print_by_rarity[expected_rarity][0]
                    eid=int(prod['external_product_id']); pid=int(pr['print_id'])
                    if by_product.get(eid) or by_print.get(pid):
                        raise RuntimeError({'residual_claim_race':{'idProduct':str(prod['id_product']),'print_id':pid}})
                    pair={
                        'set_code':SET_CODE,'idExpansion':ID_EXPANSION,'idMetacard':meta,
                        'external_product_id':eid,'idProduct':str(prod['id_product']),'product_name':str(prod['name']),
                        'product_ordinal':ordinal,'contract_rarity':expected_rarity,
                        'print_id':pid,'card_id':cid,'card_name':card_names[cid],
                        'collector_number':str(pr['collector_number']),'canonical_rarity':str(pr['rarity']),
                        'canonical_variant':str(pr['variant'] or ''),
                    }
                    pairs.append(pair); proposal.append(pair)
                groups.append({**base,'status':'certified_super_secret_candidate','pairs':pairs})

            if residual_metas!=EXPECTED_METACARDS:
                raise RuntimeError({'DOCS_residual_metacard_set_drift':{'actual':sorted(residual_metas),'expected':sorted(EXPECTED_METACARDS)}})
            if len(groups)!=EXPECTED_GROUPS or len(proposal)!=EXPECTED_PAIRS:
                raise RuntimeError({'DOCS_candidate_count_drift':{'groups':len(groups),'pairs':len(proposal)}})
            if len({r['external_product_id'] for r in proposal})!=EXPECTED_PAIRS or len({r['idProduct'] for r in proposal})!=EXPECTED_PAIRS or len({r['print_id'] for r in proposal})!=EXPECTED_PAIRS:
                raise RuntimeError('DOCS candidate surface is not globally one-to-one')
            conn.rollback()
    finally:
        conn.close()

    payload={
        'status':'pass','mode':'read_only','production_writes':0,
        'source':'current Cardmarket Product Catalog + frozen first-party Cardmarket OCG version evidence + Neon canonical JA physical surface',
        'cardmarket_capture':str(capture),'ja_baseline':ja,'set_code':SET_CODE,'idExpansion':ID_EXPANSION,
        'regional_products':len(products),'canonical_ja_prints':len(prints),'accepted_links_before':len(accepted_here),
        'evidence':FIRST_PARTY_EVIDENCE,'evidence_sha256':evidence_sha256(),
        'contract':'numeric idProduct ordinal V1->Super, V2->Secret for exact DOCS-JP two-product Super/Secret groups',
        'expected_residual_metacards':sorted(EXPECTED_METACARDS),
        'candidate_groups':len(groups),'candidate_pairs':len(proposal),
        'candidate_products':len({r['idProduct'] for r in proposal}),'candidate_prints':len({r['print_id'] for r in proposal}),
        'groups':groups,'proposal':proposal,
    }
    out=Path(os.getenv('YGO_OCG_DOCS_SUPER_SECRET_OUTPUT','/tmp/yugioh-ocg-docs-super-secret-v1.json'))
    out.parent.mkdir(parents=True,exist_ok=True); out.write_text(json.dumps(payload,ensure_ascii=False,indent=2,sort_keys=True,default=str)+'\n',encoding='utf-8')
    print(json.dumps(payload,ensure_ascii=False,indent=2,sort_keys=True,default=str)); return 0


if __name__=='__main__': raise SystemExit(main())
