from __future__ import annotations

import json
import os
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

import psycopg2
from psycopg2.extras import RealDictCursor

GAME='yugioh'
ACCEPTED=('accepted','mapped','exact')
EXPECTED_JA=36426
EXPECTED_CAPTURE='2026-08-18 21:09:20.740891+00:00'
TARGETS={
    'SK2': {'idExpansion':'4889','physical':57},
    'PE': {'idExpansion':'4910','physical':55},
    'ST14': {'idExpansion':'4708','physical':48},
    'TDS2': {'idExpansion':'5894','physical':46},
    'SD32': {'idExpansion':'4641','physical':43},
    'ST17': {'idExpansion':'4645','physical':43},
    'YSD6': {'idExpansion':'4763','physical':43},
    'SR05': {'idExpansion':'4636','physical':42},
    'SR06': {'idExpansion':'4624','physical':42},
    'SR10': {'idExpansion':'4711','physical':41},
    'SD25': {'idExpansion':'4721','physical':40},
    'SD18': {'idExpansion':'4789','physical':38},
    'SD20': {'idExpansion':'4769','physical':38},
    'SD4': {'idExpansion':'1017','physical':32},
    'DP07': {'idExpansion':'4822','physical':30},
    'SD2': {'idExpansion':'4871','physical':28},
    'SSD2': {'idExpansion':'4599','physical':21},
    'EN01': {'idExpansion':'4686','physical':20},
    'JF09': {'idExpansion':'4805','physical':10},
    'PP13': {'idExpansion':'4762','physical':10},
    'PP14': {'idExpansion':'4749','physical':10},
    'PP15': {'idExpansion':'4729','physical':10},
}


def norm(value: object)->str:
    text=unicodedata.normalize('NFKD',str(value or '')).casefold()
    return ''.join(ch for ch in text if ch.isalnum())


def main()->int:
    url=os.getenv('DATABASE_URL_UNPOOLED') or os.getenv('DATABASE_URL')
    if not url: raise RuntimeError('DATABASE URL required')
    conn=psycopg2.connect(url,connect_timeout=30,application_name='dontripit_ygo_ocg_full_bijection_cohort_v1')
    conn.set_session(readonly=True,autocommit=False)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT id FROM games WHERE slug=%s LIMIT 1",(GAME,)); gid=int(cur.fetchone()['id'])
            cur.execute("SELECT max(last_seen_at) capture FROM external_catalog_products WHERE source='cardmarket' AND game_id=%s",(gid,)); capture=cur.fetchone()['capture']
            if str(capture)!=EXPECTED_CAPTURE: raise RuntimeError({'capture_drift':str(capture)})
            cur.execute("SELECT count(*) n FROM prints p JOIN cards c ON c.id=p.card_id WHERE c.game_id=%s AND lower(coalesce(p.language,''))='ja'",(gid,)); ja=int(cur.fetchone()['n'])
            if ja!=EXPECTED_JA: raise RuntimeError({'ja_baseline_drift':ja})

            cur.execute("""SELECT e.metacard_external_id,p.card_id,count(*) evidence_links
                FROM external_catalog_print_links l JOIN external_catalog_products e ON e.id=l.external_product_id
                JOIN prints p ON p.id=l.print_id
                WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single'
                  AND e.metacard_external_id IS NOT NULL AND l.link_status=ANY(%s)
                GROUP BY e.metacard_external_id,p.card_id""",(gid,list(ACCEPTED)))
            meta_cards=defaultdict(set); evidence=Counter()
            for r in cur.fetchall():
                meta=str(r['metacard_external_id']); cid=int(r['card_id'])
                meta_cards[meta].add(cid); evidence[(meta,cid)]+=int(r['evidence_links'] or 0)

            cur.execute("""SELECT l.external_product_id,l.print_id
                FROM external_catalog_print_links l JOIN external_catalog_products e ON e.id=l.external_product_id
                WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single' AND l.link_status=ANY(%s)""",(gid,list(ACCEPTED)))
            claimed_products=set(); claimed_prints=set()
            for r in cur.fetchall(): claimed_products.add(int(r['external_product_id'])); claimed_prints.add(int(r['print_id']))

            cur.execute("""SELECT e.id external_product_id,e.external_id id_product,e.expansion_external_id,e.name,e.metacard_external_id
                FROM external_catalog_products e
                WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single' AND e.last_seen_at=%s AND e.expansion_external_id IS NOT NULL
                ORDER BY e.expansion_external_id,e.external_id::bigint""",(gid,capture))
            all_products=defaultdict(list)
            for r in cur.fetchall(): all_products[str(r['expansion_external_id'])].append(dict(r))

            certified=[]; ambiguous=[]; proposal=[]
            for code,cfg in TARGETS.items():
                exp=str(cfg['idExpansion']); expected=int(cfg['physical']); products=all_products.get(exp,[])
                cur.execute("""SELECT p.id print_id,p.card_id,p.collector_number,p.rarity,p.variant,c.name card_name
                    FROM prints p JOIN cards c ON c.id=p.card_id JOIN sets s ON s.id=p.set_id
                    WHERE c.game_id=%s AND upper(coalesce(s.code,''))=%s AND lower(coalesce(p.language,''))='ja'
                    ORDER BY p.card_id,p.id""",(gid,code)); prints=[dict(r) for r in cur.fetchall()]
                card_ids={int(x['card_id']) for x in prints}; by_card=defaultdict(list)
                for x in prints: by_card[int(x['card_id'])].append(x)
                if len(products)!=expected or len(prints)!=expected or len(card_ids)!=expected or any(len(v)!=1 for v in by_card.values()):
                    raise RuntimeError({'target_surface_drift':code,'products':len(products),'prints':len(prints),'logical':len(card_ids),'expected':expected})
                product_cards=[]; pairs=[]
                for prod in products:
                    meta=str(prod.get('metacard_external_id') or ''); cards=meta_cards.get(meta,set())
                    if not meta or len(cards)!=1: raise RuntimeError({'target_metacard_resolution_drift':code,'idProduct':str(prod['id_product']),'cards':sorted(cards)})
                    cid=next(iter(cards)); product_cards.append(cid)
                    if cid not in by_card: raise RuntimeError({'target_card_outside_set':code,'idProduct':str(prod['id_product']),'card_id':cid})
                    pr=by_card[cid][0]
                    if norm(prod['name'])!=norm(pr['card_name']): raise RuntimeError({'target_name_drift':code,'idProduct':str(prod['id_product'])})
                    eid=int(prod['external_product_id']); pid=int(pr['print_id'])
                    if eid in claimed_products or pid in claimed_prints: raise RuntimeError({'target_existing_claim':code,'idProduct':str(prod['id_product']),'print_id':pid})
                    pairs.append({'set_code':code,'idExpansion':exp,'external_product_id':eid,'idProduct':str(prod['id_product']),'idMetacard':meta,'print_id':pid,'card_id':cid,'card_name':str(pr['card_name']),'collector_number':str(pr['collector_number']),'canonical_rarity':pr.get('rarity'),'canonical_variant':pr.get('variant'),'metacard_evidence_links':int(evidence.get((meta,cid),0))})
                if len(set(product_cards))!=expected or set(product_cards)!=card_ids or Counter(norm(x['name']) for x in products)!=Counter(norm(x['card_name']) for x in prints):
                    raise RuntimeError({'target_full_bijection_failed':code})

                competitors=[]
                canonical_names=Counter(norm(x['card_name']) for x in prints)
                for other_exp,other in all_products.items():
                    if other_exp==exp or len(other)!=expected: continue
                    resolved=[]; ok=True
                    for prod in other:
                        meta=str(prod.get('metacard_external_id') or ''); cards=meta_cards.get(meta,set())
                        if not meta or len(cards)!=1: ok=False; break
                        cid=next(iter(cards))
                        if cid not in card_ids: ok=False; break
                        resolved.append(cid)
                    if ok and len(set(resolved))==expected and set(resolved)==card_ids and Counter(norm(x['name']) for x in other)==canonical_names:
                        competitors.append(other_exp)
                if competitors:
                    ambiguous.append({'set_code':code,'target_idExpansion':exp,'physical':expected,'competing_full_bijection_expansions':sorted(competitors,key=lambda x:int(x) if x.isdigit() else x)})
                else:
                    certified.append({'set_code':code,'idExpansion':exp,'pairs':expected})
                    proposal.extend(pairs)
            conn.rollback()
    finally: conn.close()

    if len({x['external_product_id'] for x in proposal})!=len(proposal) or len({x['print_id'] for x in proposal})!=len(proposal): raise RuntimeError('global certified cohort not one-to-one')
    if not all(x['metacard_evidence_links']>0 for x in proposal): raise RuntimeError('certified pair without accepted metacard evidence')
    report={'status':'pass','mode':'read_only','production_writes':0,'cardmarket_capture':str(capture),'ja_baseline':ja,'screened_targets':len(TARGETS),'certified_targets':len(certified),'ambiguous_targets':len(ambiguous),'certified_pairs':len(proposal),'certified':certified,'ambiguous':ambiguous,'proposal':proposal}
    out=Path(os.getenv('YGO_OCG_FULL_BIJECTION_COHORT_OUTPUT','/tmp/ygo-ocg-full-bijection-cohort-v1.json')); out.parent.mkdir(parents=True,exist_ok=True); out.write_text(json.dumps(report,ensure_ascii=False,indent=2,sort_keys=True,default=str)+'\n',encoding='utf-8'); print(json.dumps(report,ensure_ascii=False,indent=2,sort_keys=True,default=str)); return 0

if __name__=='__main__': raise SystemExit(main())
