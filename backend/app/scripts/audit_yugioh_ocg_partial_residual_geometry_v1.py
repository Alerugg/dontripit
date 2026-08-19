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
 'ROTA':{'idExpansion':'5840','physical':132},
 'BLVO':{'idExpansion':'4546','physical':126},
 'CHIM':{'idExpansion':'4577','physical':119},
 'ETCO':{'idExpansion':'4563','physical':119},
 'CIBR':{'idExpansion':'4640','physical':103},
 'EXFO':{'idExpansion':'4634','physical':103},
 'FLOD':{'idExpansion':'4627','physical':103},
 'INOV':{'idExpansion':'4660','physical':103},
 'RATE':{'idExpansion':'4655','physical':103},
 'TDIL':{'idExpansion':'4666','physical':103},
 'CSOC':{'idExpansion':'4809','physical':87},
}


def norm(v: object)->str:
    t=unicodedata.normalize('NFKD',str(v or '')).casefold()
    return ''.join(ch for ch in t if ch.isalnum())


def hist(values):
    return {str(k):int(v) for k,v in sorted(Counter(values).items())}


def main()->int:
    url=os.getenv('DATABASE_URL_UNPOOLED') or os.getenv('DATABASE_URL')
    if not url: raise RuntimeError('DATABASE URL required')
    conn=psycopg2.connect(url,connect_timeout=30,application_name='dontripit_ygo_ocg_partial_residual_geometry_v1')
    conn.set_session(readonly=True,autocommit=False)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT id FROM games WHERE slug=%s LIMIT 1",(GAME,)); gid=int(cur.fetchone()['id'])
            cur.execute("SELECT max(last_seen_at) capture FROM external_catalog_products WHERE source='cardmarket' AND game_id=%s",(gid,)); capture=cur.fetchone()['capture']
            if str(capture)!=EXPECTED_CAPTURE: raise RuntimeError({'capture_drift':str(capture)})
            cur.execute("SELECT count(*) n FROM prints p JOIN cards c ON c.id=p.card_id WHERE c.game_id=%s AND lower(coalesce(p.language,''))='ja'",(gid,)); ja=int(cur.fetchone()['n'])
            if ja!=EXPECTED_JA: raise RuntimeError({'ja_baseline_drift':ja})

            # All accepted historical metacard -> logical card evidence.
            cur.execute("""SELECT e.metacard_external_id,p.card_id,count(*) n
                FROM external_catalog_print_links l JOIN external_catalog_products e ON e.id=l.external_product_id
                JOIN prints p ON p.id=l.print_id
                WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single'
                  AND e.metacard_external_id IS NOT NULL AND l.link_status=ANY(%s)
                GROUP BY e.metacard_external_id,p.card_id""",(gid,list(ACCEPTED)))
            meta=defaultdict(set); meta_evidence=Counter()
            for r in cur.fetchall():
                m=str(r['metacard_external_id']); cid=int(r['card_id']); meta[m].add(cid); meta_evidence[(m,cid)]+=int(r['n'] or 0)

            # All accepted product/print claims, with their resolved canonical context.
            cur.execute("""SELECT l.external_product_id,l.print_id,e.external_id id_product,e.expansion_external_id,
                       p.card_id,p.language,p.collector_number,p.rarity,p.variant,s.code set_code,
                       l.mapping_method,l.confidence,l.reviewed
                FROM external_catalog_print_links l JOIN external_catalog_products e ON e.id=l.external_product_id
                JOIN prints p ON p.id=l.print_id JOIN sets s ON s.id=p.set_id
                WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single' AND l.link_status=ANY(%s)""",(gid,list(ACCEPTED)))
            by_product=defaultdict(list); by_print=defaultdict(list)
            for r in cur.fetchall():
                row=dict(r); by_product[int(row['external_product_id'])].append(row); by_print[int(row['print_id'])].append(row)

            reports=[]; deterministic=[]; unsafe_samples=[]
            for code,cfg in TARGETS.items():
                exp=str(cfg['idExpansion']); expected=int(cfg['physical'])
                cur.execute("""SELECT e.id external_product_id,e.external_id id_product,e.name,e.metacard_external_id
                    FROM external_catalog_products e
                    WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single'
                      AND e.expansion_external_id=%s AND e.last_seen_at=%s
                    ORDER BY e.metacard_external_id,e.external_id::bigint""",(gid,exp,capture))
                products=[dict(r) for r in cur.fetchall()]
                cur.execute("""SELECT p.id print_id,p.card_id,p.collector_number,p.rarity,p.variant,p.is_foil,c.name card_name
                    FROM prints p JOIN cards c ON c.id=p.card_id JOIN sets s ON s.id=p.set_id
                    WHERE c.game_id=%s AND upper(coalesce(s.code,''))=%s AND lower(coalesce(p.language,''))='ja'
                    ORDER BY p.card_id,p.id""",(gid,code))
                prints=[dict(r) for r in cur.fetchall()]
                if len(products)!=expected or len(prints)!=expected:
                    raise RuntimeError({'surface_count_drift':code,'products':len(products),'prints':len(prints),'expected':expected})

                product_by_card=defaultdict(list); product_by_meta=defaultdict(list); unresolved=[]; ambiguous_meta=[]
                for p in products:
                    m=str(p.get('metacard_external_id') or '')
                    product_by_meta[m].append(p)
                    cards=meta.get(m,set()) if m else set()
                    if not m or not cards:
                        unresolved.append(str(p['id_product'])); continue
                    if len(cards)!=1:
                        ambiguous_meta.append({'idProduct':str(p['id_product']),'idMetacard':m,'cards':sorted(cards)}); continue
                    cid=next(iter(cards)); p['resolved_card_id']=cid; product_by_card[cid].append(p)
                print_by_card=defaultdict(list)
                for pr in prints: print_by_card[int(pr['card_id'])].append(pr)

                cards_union=sorted(set(product_by_card)|set(print_by_card))
                set_stats={
                    'set_code':code,'idExpansion':exp,'products':len(products),'canonical_ja_prints':len(prints),
                    'unique_metacards':len([m for m in product_by_meta if m]),
                    'resolved_unique_cards':len(product_by_card),'canonical_unique_cards':len(print_by_card),
                    'unresolved_products':len(unresolved),'ambiguous_metacard_products':len(ambiguous_meta),
                    'product_group_size_histogram':hist(len(v) for v in product_by_card.values()),
                    'canonical_group_size_histogram':hist(len(v) for v in print_by_card.values()),
                    'existing_target_product_claims':0,'existing_target_print_claims':0,
                    'existing_foreign_product_claims':0,'existing_foreign_print_claims':0,
                    'groups_product_count_equals_print_count':0,'groups_product_count_differs':0,
                    'groups_unclaimed_balance':0,'groups_unclaimed_differs':0,
                    'deterministic_complement_1x1_groups':0,'deterministic_complement_pairs':0,
                    'ambiguous_residual_groups':0,'ambiguous_residual_products':0,
                    'fully_claimed_groups':0,'group_count':len(cards_union),
                    'unresolved_product_samples':unresolved[:12],
                    'ambiguous_metacard_samples':ambiguous_meta[:8],
                    'mismatch_samples':[],
                    'deterministic_samples':[],
                }

                for cid in cards_union:
                    gp=product_by_card.get(cid,[]); cp=print_by_card.get(cid,[])
                    target_product_claims=[]; foreign_product_claims=[]
                    for p in gp:
                        claims=by_product.get(int(p['external_product_id']),[])
                        for r in claims:
                            if int(r['card_id'])==cid and str(r['set_code']).upper()==code and str(r['language']).lower()=='ja': target_product_claims.append((p,r))
                            else: foreign_product_claims.append((p,r))
                    target_print_claims=[]; foreign_print_claims=[]
                    for pr in cp:
                        claims=by_print.get(int(pr['print_id']),[])
                        for r in claims:
                            if int(r['card_id'])==cid and str(r['set_code']).upper()==code and str(r['language']).lower()=='ja': target_print_claims.append((pr,r))
                            else: foreign_print_claims.append((pr,r))
                    set_stats['existing_target_product_claims']+=len({int(p['external_product_id']) for p,_ in target_product_claims})
                    set_stats['existing_target_print_claims']+=len({int(pr['print_id']) for pr,_ in target_print_claims})
                    set_stats['existing_foreign_product_claims']+=len({int(p['external_product_id']) for p,_ in foreign_product_claims})
                    set_stats['existing_foreign_print_claims']+=len({int(pr['print_id']) for pr,_ in foreign_print_claims})

                    if len(gp)==len(cp): set_stats['groups_product_count_equals_print_count']+=1
                    else: set_stats['groups_product_count_differs']+=1
                    claimed_product_ids={int(p['external_product_id']) for p,_ in target_product_claims+foreign_product_claims}
                    claimed_print_ids={int(pr['print_id']) for pr,_ in target_print_claims+foreign_print_claims}
                    up=[p for p in gp if int(p['external_product_id']) not in claimed_product_ids]
                    upr=[pr for pr in cp if int(pr['print_id']) not in claimed_print_ids]
                    if len(up)==len(upr): set_stats['groups_unclaimed_balance']+=1
                    else: set_stats['groups_unclaimed_differs']+=1

                    card_name=str(cp[0]['card_name']) if cp else (str(gp[0]['name']) if gp else '')
                    names_ok=bool(gp and cp) and all(norm(p['name'])==norm(card_name) for p in gp)
                    safe_context=(not foreign_product_claims and not foreign_print_claims and names_ok)
                    if safe_context and len(up)==1 and len(upr)==1:
                        p=up[0]; pr=upr[0]
                        row={'set_code':code,'idExpansion':exp,'card_id':cid,'card_name':card_name,
                             'idMetacard':str(p.get('metacard_external_id') or ''),'external_product_id':int(p['external_product_id']),
                             'idProduct':str(p['id_product']),'print_id':int(pr['print_id']),'collector_number':str(pr['collector_number']),
                             'canonical_rarity':pr.get('rarity'),'canonical_variant':pr.get('variant'),
                             'product_group_size':len(gp),'canonical_group_size':len(cp),
                             'target_claimed_products_in_group':len({int(x['external_product_id']) for x,_ in target_product_claims}),
                             'target_claimed_prints_in_group':len({int(x['print_id']) for x,_ in target_print_claims}),
                             'metacard_evidence_links':int(meta_evidence.get((str(p.get('metacard_external_id') or ''),cid),0))}
                        deterministic.append(row); set_stats['deterministic_complement_1x1_groups']+=1; set_stats['deterministic_complement_pairs']+=1
                        if len(set_stats['deterministic_samples'])<10: set_stats['deterministic_samples'].append(row)
                    elif not up and not upr and safe_context:
                        set_stats['fully_claimed_groups']+=1
                    elif up or upr:
                        set_stats['ambiguous_residual_groups']+=1; set_stats['ambiguous_residual_products']+=len(up)

                    if (len(gp)!=len(cp) or len(up)!=len(upr) or foreign_product_claims or foreign_print_claims) and len(set_stats['mismatch_samples'])<15:
                        sample={'card_id':cid,'card_name':card_name,'product_count':len(gp),'print_count':len(cp),
                                'unclaimed_products':len(up),'unclaimed_prints':len(upr),
                                'idProducts':[str(p['id_product']) for p in gp],
                                'print_rows':[{'print_id':int(pr['print_id']),'collector_number':str(pr['collector_number']),'rarity':pr.get('rarity'),'variant':pr.get('variant')} for pr in cp],
                                'foreign_product_claims':[{'idProduct':str(p['id_product']),'claim_set':str(r['set_code']),'claim_card_id':int(r['card_id']),'claim_method':str(r['mapping_method'])} for p,r in foreign_product_claims[:5]],
                                'foreign_print_claims':[{'print_id':int(pr['print_id']),'claim_idProduct':str(r['id_product']),'claim_expansion':str(r.get('expansion_external_id') or ''),'claim_method':str(r['mapping_method'])} for pr,r in foreign_print_claims[:5]]}
                        set_stats['mismatch_samples'].append(sample)
                        if len(unsafe_samples)<80: unsafe_samples.append({'set_code':code,**sample})
                reports.append(set_stats)
            conn.rollback()
    finally:
        conn.close()

    # Global deterministic complement candidates must still be one-to-one and evidence-backed.
    unique_products=len({x['external_product_id'] for x in deterministic}); unique_prints=len({x['print_id'] for x in deterministic})
    payload={'status':'pass','mode':'read_only','production_writes':0,'cardmarket_capture':str(capture),'ja_baseline':ja,
             'screened_sets':len(TARGETS),'deterministic_complement_pairs':len(deterministic),
             'deterministic_unique_products':unique_products,'deterministic_unique_prints':unique_prints,
             'deterministic_all_metacard_evidence':all(x['metacard_evidence_links']>0 for x in deterministic),
             'sets':reports,'deterministic_pairs':deterministic,'unsafe_samples':unsafe_samples}
    out=Path(os.getenv('YGO_OCG_PARTIAL_RESIDUAL_GEOMETRY_OUTPUT','/tmp/ygo-ocg-partial-residual-geometry-v1.json'))
    out.parent.mkdir(parents=True,exist_ok=True); out.write_text(json.dumps(payload,ensure_ascii=False,indent=2,sort_keys=True,default=str)+'\n',encoding='utf-8')
    print(json.dumps(payload,ensure_ascii=False,indent=2,sort_keys=True,default=str)); return 0

if __name__=='__main__': raise SystemExit(main())
