from __future__ import annotations

import json
import os
from collections import Counter, defaultdict
from decimal import Decimal
from pathlib import Path

import psycopg2
from psycopg2.extras import RealDictCursor

GAME='yugioh'
ACCEPTED=('accepted','mapped','exact')
EXPECTED_JA=36426


def positive(v):
    try:
        return v is not None and Decimal(str(v))>0
    except Exception:
        return False


def meaningful(r):
    return any(positive(r.get(k)) for k in ('price_low','price_mid','price_market','price_last'))


def price_variant(r):
    v=str(r.get('variant') or '').lower()
    if 'etched' in v or 'glossy' in v:
        return None
    return 'foil' if bool(r.get('is_foil')) else 'nonfoil'


def main()->int:
    url=os.getenv('DATABASE_URL_UNPOOLED') or os.getenv('DATABASE_URL')
    if not url:
        raise RuntimeError('DATABASE URL required')
    conn=psycopg2.connect(url,connect_timeout=30,application_name='dontripit_ygo_ocg_production_coverage_v2')
    conn.set_session(readonly=True,autocommit=False)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT id FROM games WHERE slug=%s LIMIT 1",(GAME,)); gid=int(cur.fetchone()['id'])
            cur.execute("SELECT max(last_seen_at) ts FROM external_catalog_products WHERE source='cardmarket' AND game_id=%s",(gid,)); capture=cur.fetchone()['ts']
            cur.execute("""SELECT max(mp.as_of) ts FROM external_market_price_snapshots mp JOIN external_catalog_products e ON e.id=mp.external_product_id WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single'""",(gid,)); asof=cur.fetchone()['ts']

            cur.execute("""SELECT s.id set_id,s.code,s.name,p.id print_id,p.card_id,p.collector_number,p.variant,p.rarity,p.is_foil
                FROM prints p JOIN cards c ON c.id=p.card_id JOIN sets s ON s.id=p.set_id
                WHERE c.game_id=%s AND lower(coalesce(p.language,''))='ja'
                ORDER BY s.code,p.id""",(gid,))
            ja=[dict(r) for r in cur.fetchall()]
            if len(ja)!=EXPECTED_JA:
                raise RuntimeError({'ja_baseline_drift':{'expected':EXPECTED_JA,'actual':len(ja)}})
            set_prints=defaultdict(list); set_cards=defaultdict(set); set_meta={}
            for r in ja:
                sid=int(r['set_id']); set_prints[sid].append(r); set_cards[sid].add(int(r['card_id'])); set_meta[sid]={'code':str(r['code'] or ''),'name':str(r['name'] or '')}

            cur.execute("""SELECT l.external_product_id,l.print_id,l.mapping_method,l.confidence,l.reviewed,l.link_status,
                       e.external_id id_product,e.metacard_external_id,e.expansion_external_id,e.last_seen_at,
                       p.card_id,p.set_id,p.language,p.variant,p.is_foil,s.code set_code
                FROM external_catalog_print_links l JOIN external_catalog_products e ON e.id=l.external_product_id
                JOIN prints p ON p.id=l.print_id JOIN sets s ON s.id=p.set_id JOIN cards c ON c.id=p.card_id
                WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single'
                  AND l.link_status=ANY(%s) AND lower(coalesce(p.language,''))='ja'""",(gid,list(ACCEPTED)))
            links=[dict(r) for r in cur.fetchall()]
            by_product=defaultdict(list); by_print=defaultdict(list); by_set=defaultdict(list)
            for r in links:
                by_product[int(r['external_product_id'])].append(r); by_print[int(r['print_id'])].append(r); by_set[int(r['set_id'])].append(r)

            product_conflicts={str(k):v for k,v in by_product.items() if len({int(x['print_id']) for x in v})>1}
            print_conflicts={str(k):v for k,v in by_print.items() if len({int(x['external_product_id']) for x in v})>1}
            if product_conflicts or print_conflicts:
                raise RuntimeError({'accepted_ja_identity_conflicts':{'products':list(product_conflicts)[:20],'prints':list(print_conflicts)[:20]}})

            per_set=[]
            for sid,rows in set_prints.items():
                accepted=by_set.get(sid,[])
                accepted_products=len({int(x['external_product_id']) for x in accepted})
                accepted_prints=len({int(x['print_id']) for x in accepted})
                if len(accepted)!=accepted_products or len(accepted)!=accepted_prints:
                    raise RuntimeError({'set_not_one_to_one':sid})
                physical=len(rows); linked=len(accepted)
                per_set.append({
                    'set_id':sid,'set_code':set_meta[sid]['code'],'set_name':set_meta[sid]['name'],
                    'ja_physical':physical,'ja_logical_cards':len(set_cards[sid]),
                    'accepted_cardmarket_ja_links':linked,'residual_ja_prints':physical-linked,
                    'coverage_pct':round(100*linked/physical,4) if physical else 0,
                    'status':'complete' if linked==physical else ('partial' if linked else 'unmapped'),
                })
            per_set.sort(key=lambda r:(-r['residual_ja_prints'],-r['ja_physical'],r['set_code']))

            method_counts=Counter(str(r.get('mapping_method') or '') for r in links)
            reviewed_exact=sum(str(r.get('confidence') or '')=='exact' and bool(r.get('reviewed')) for r in links)
            stale=sum(capture is not None and r['last_seen_at']!=capture for r in links)

            # Current-price correctness for every accepted JA identity.
            extids=[int(r['external_product_id']) for r in links]; printids=[int(r['print_id']) for r in links]
            ext={}
            if asof and extids:
                cur.execute("""SELECT external_product_id,price_variant,price_low,price_mid,price_market,price_last FROM external_market_price_snapshots WHERE external_product_id=ANY(%s) AND currency='EUR' AND as_of=%s""",(extids,asof))
                for r in cur.fetchall(): ext[(int(r['external_product_id']),str(r['price_variant']))]=dict(r)
            canon=defaultdict(list)
            if asof and printids:
                cur.execute("""SELECT ps.entity_id print_id,ps.price_low,ps.price_mid,ps.price_market,ps.price_last,ps.raw_json FROM price_snapshots ps JOIN price_sources src ON src.id=ps.source_id WHERE src.name='cardmarket' AND ps.entity_type='print' AND ps.entity_id=ANY(%s) AND ps.currency='EUR' AND ps.as_of=%s""",(printids,asof))
                for r in cur.fetchall(): canon[int(r['print_id'])].append(dict(r))
            priceable=missing_external=unsupported_finish=canonical_exact=missing_canonical=wrong_product=0
            for r in links:
                pv=price_variant(r); er=None if pv is None else ext.get((int(r['external_product_id']),pv))
                if pv is None: unsupported_finish+=1
                elif not er or not meaningful(er): missing_external+=1
                else: priceable+=1
                cr=canon.get(int(r['print_id']),[])
                exact=[x for x in cr if str((x.get('raw_json') or {}).get('idProduct') or '')==str(r['id_product'])]
                mismatch=[x for x in cr if str((x.get('raw_json') or {}).get('idProduct') or '') not in ('',str(r['id_product']))]
                wrong_product+=bool(mismatch)
                if exact and any(meaningful(x) for x in exact): canonical_exact+=1
                else: missing_canonical+=1

            # Build a fresh accepted metacard -> logical Card bridge.
            cur.execute("""SELECT e.metacard_external_id,p.card_id,count(*) evidence_links FROM external_catalog_print_links l
                JOIN external_catalog_products e ON e.id=l.external_product_id JOIN prints p ON p.id=l.print_id
                WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single'
                  AND e.metacard_external_id IS NOT NULL AND l.link_status=ANY(%s)
                GROUP BY e.metacard_external_id,p.card_id""",(gid,list(ACCEPTED)))
            meta_cards=defaultdict(set)
            for r in cur.fetchall(): meta_cards[str(r['metacard_external_id'])].add(int(r['card_id']))
            unique_meta={m:next(iter(cards)) for m,cards in meta_cards.items() if len(cards)==1}

            # Score all current Cardmarket expansions against canonical JA sets by resolved logical-card membership.
            cur.execute("""SELECT e.expansion_external_id,e.metacard_external_id,e.external_id id_product FROM external_catalog_products e
                WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single' AND e.last_seen_at=%s AND e.expansion_external_id IS NOT NULL""",(gid,capture))
            expansion_products=defaultdict(list)
            for r in cur.fetchall(): expansion_products[str(r['expansion_external_id'])].append(dict(r))
            set_by_card=defaultdict(set)
            for sid,cards in set_cards.items():
                for cid in cards: set_by_card[cid].add(sid)
            candidate_scores=[]
            for exp,products in expansion_products.items():
                resolved_cards=[]
                unresolved=ambiguous=0
                for p in products:
                    meta=str(p.get('metacard_external_id') or '')
                    if meta in unique_meta: resolved_cards.append(unique_meta[meta])
                    elif meta and meta in meta_cards: ambiguous+=1
                    else: unresolved+=1
                if not resolved_cards: continue
                counts=Counter()
                for cid in set(resolved_cards):
                    for sid in set_by_card.get(cid,()): counts[sid]+=1
                for sid,shared in counts.most_common(3):
                    logical=len(set_cards[sid]); physical=len(set_prints[sid]); prod_n=len(products)
                    logical_cov=shared/logical if logical else 0
                    count_ratio=min(prod_n,physical)/max(prod_n,physical) if prod_n and physical else 0
                    if shared>=10 and logical_cov>=0.70 and count_ratio>=0.70:
                        candidate_scores.append({
                            'idExpansion':exp,'product_count':prod_n,'resolved_product_cards':len(resolved_cards),
                            'unresolved_products':unresolved,'ambiguous_products':ambiguous,
                            'set_id':sid,'set_code':set_meta[sid]['code'],'set_name':set_meta[sid]['name'],
                            'ja_physical':physical,'ja_logical_cards':logical,'shared_resolved_cards':shared,
                            'logical_coverage_pct':round(100*logical_cov,3),'physical_count_compat_pct':round(100*count_ratio,3),
                            'existing_accepted_ja_links':len(by_set.get(sid,[])),'residual_ja_prints':physical-len(by_set.get(sid,[])),
                        })
            candidate_scores.sort(key=lambda r:(-r['logical_coverage_pct'],-r['physical_count_compat_pct'],-r['shared_resolved_cards'],r['set_code']))
            conn.rollback()
    finally: conn.close()

    total_links=len(links); complete=sum(r['status']=='complete' for r in per_set); partial=sum(r['status']=='partial' for r in per_set); unmapped=sum(r['status']=='unmapped' for r in per_set)
    report={
        'status':'pass','production_writes':0,'game':GAME,'catalog_capture':str(capture),'price_guide_as_of':str(asof),
        'ja_physical_baseline':len(ja),'ja_set_count':len(per_set),
        'accepted_cardmarket_ja_links':total_links,'accepted_unique_products':len(by_product),'accepted_unique_prints':len(by_print),
        'accepted_reviewed_exact_links':reviewed_exact,'accepted_stale_links':stale,
        'coverage_pct':round(100*total_links/len(ja),4),
        'complete_sets':complete,'partial_sets':partial,'unmapped_sets':unmapped,
        'mapping_methods':dict(method_counts),
        'pricing':{
            'externally_priceable_links':priceable,'missing_external_current_price':missing_external,'unsupported_finish':unsupported_finish,
            'canonical_current_exact_idProduct_prices':canonical_exact,'missing_canonical_current_price':missing_canonical,'canonical_current_wrong_idProduct':wrong_product,
        },
        'per_set':per_set,
        'top_residual_sets':per_set[:100],
        'high_confidence_surface_candidates':candidate_scores[:200],
    }
    if wrong_product:
        raise RuntimeError({'canonical_wrong_idProduct':wrong_product})
    if canonical_exact!=priceable:
        report['pricing']['price_projection_gap']=priceable-canonical_exact
    out=Path(os.getenv('YGO_OCG_PRODUCTION_COVERAGE_V2_OUTPUT','/tmp/yugioh-ocg-production-coverage-v2.json'))
    out.parent.mkdir(parents=True,exist_ok=True); out.write_text(json.dumps(report,ensure_ascii=False,indent=2,sort_keys=True,default=str)+'\n',encoding='utf-8'); print(json.dumps(report,ensure_ascii=False,indent=2,sort_keys=True,default=str)); return 0

if __name__=='__main__': raise SystemExit(main())
