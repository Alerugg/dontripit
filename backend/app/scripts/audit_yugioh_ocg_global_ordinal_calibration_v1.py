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
TARGETS={
    'DOCS':{'idExpansion':'4680','products':108,'prints':108,'accepted':69},
    'LTGY':{'idExpansion':'4725','products':86,'prints':86,'accepted':75},
    'CSOC':{'idExpansion':'4809','products':87,'prints':87,'accepted':74},
}
MIN_CONTROLS=5


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
        'prismaticsecretrare':'prismaticsecret','prismaticsecret':'prismaticsecret',
        'quartercenturysecretrare':'quartercenturysecret','quartercenturysecret':'quartercenturysecret',
        'starlightrare':'starlight','starlight':'starlight',
        'holographicrare':'ghost','ghostrare':'ghost','ghost':'ghost',
        'collectorsrare':'collectors','collectors':'collectors',
        'parallelrare':'parallel','parallel':'parallel',
        'common':'common','rare':'rare','shortprint':'shortprint',
    }
    return aliases.get(x,x)


def rarity_key(values)->str:
    return '|'.join(sorted(rarity(v) for v in values))


def main()->int:
    url=os.getenv('DATABASE_URL_UNPOOLED') or os.getenv('DATABASE_URL')
    if not url: raise RuntimeError('DATABASE URL required')
    conn=psycopg2.connect(url,connect_timeout=30,application_name='dontripit_ygo_ocg_global_ordinal_calibration_v1')
    conn.set_session(readonly=True,autocommit=False)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT id FROM games WHERE slug=%s LIMIT 1",(GAME,)); gid=int(cur.fetchone()['id'])
            cur.execute("SELECT max(last_seen_at) capture FROM external_catalog_products WHERE source='cardmarket' AND game_id=%s",(gid,)); capture=cur.fetchone()['capture']
            if capture is None: raise RuntimeError('Cardmarket capture missing')

            cur.execute("""SELECT e.id external_product_id,e.external_id id_product,e.name,e.metacard_external_id,e.expansion_external_id
                FROM external_catalog_products e
                WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single' AND e.last_seen_at=%s
                  AND e.metacard_external_id IS NOT NULL
                ORDER BY e.expansion_external_id,e.metacard_external_id,e.external_id::bigint""",(gid,capture))
            current_products=[dict(r) for r in cur.fetchall()]
            groups=defaultdict(list)
            for r in current_products:
                groups[(str(r['expansion_external_id'] or ''),str(r['metacard_external_id'] or ''))].append(r)

            cur.execute("""SELECT e.id external_product_id,e.external_id id_product,e.expansion_external_id,e.metacard_external_id,
                       l.print_id,l.mapping_method,l.confidence,l.reviewed,p.card_id,p.rarity,p.language,s.code set_code,c.name card_name
                FROM external_catalog_print_links l
                JOIN external_catalog_products e ON e.id=l.external_product_id
                JOIN prints p ON p.id=l.print_id JOIN sets s ON s.id=p.set_id JOIN cards c ON c.id=p.card_id
                WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single' AND l.link_status=ANY(%s)
                  AND e.last_seen_at=%s""",(gid,list(ACCEPTED),capture))
            accepted=[dict(r) for r in cur.fetchall()]
            exact_by_product=defaultdict(list); any_by_product=defaultdict(list); any_by_print=defaultdict(list)
            for r in accepted:
                pid=str(r['id_product']); any_by_product[pid].append(r); any_by_print[int(r['print_id'])].append(r)
                if str(r.get('confidence') or '')=='exact' and bool(r.get('reviewed')):
                    exact_by_product[pid].append(r)

            # Complete exact-reviewed multiversion groups become empirical Cardmarket ordinal controls.
            controls=[]; control_buckets=defaultdict(list)
            for (exp,meta),prods in groups.items():
                if len(prods)<2 or len(prods)>6: continue
                ordered=sorted(prods,key=lambda x:int(x['id_product']))
                links=[]; ok=True
                for prod in ordered:
                    rows=exact_by_product.get(str(prod['id_product']),[])
                    if len(rows)!=1:
                        ok=False; break
                    links.append(rows[0])
                if not ok: continue
                if len({int(r['print_id']) for r in links})!=len(links): continue
                if len({int(r['card_id']) for r in links})!=1: continue
                seq=tuple(rarity(r['rarity']) for r in links)
                key=rarity_key(seq)
                methods=sorted({str(r.get('mapping_method') or '') for r in links})
                row={
                    'idExpansion':exp,'idMetacard':meta,'group_size':len(ordered),
                    'rarity_key':key,'sequence':list(seq),'idProducts':[str(p['id_product']) for p in ordered],
                    'print_ids':[int(r['print_id']) for r in links],'methods':methods,
                    'language_set_pairs':sorted({f"{str(r['language']).lower()}:{str(r['set_code']).upper()}" for r in links}),
                    'card_name':str(links[0]['card_name']),
                }
                controls.append(row); control_buckets[(len(ordered),key)].append(row)

            calibrations={}
            for (size,key),rows in sorted(control_buckets.items()):
                seq_counts=Counter(tuple(r['sequence']) for r in rows)
                calibrations[f'{size}:{key}']={
                    'group_size':size,'rarity_key':key,'controls':len(rows),
                    'distinct_sequences':len(seq_counts),
                    'sequences':[{'sequence':list(seq),'controls':n} for seq,n in seq_counts.most_common()],
                    'method_counts':dict(Counter(m for r in rows for m in r['methods'])),
                    'sample_controls':rows[:10],
                }

            # Global stable metacard -> canonical Card evidence.
            meta_cards=defaultdict(set)
            for r in accepted:
                meta=str(r.get('metacard_external_id') or '')
                if meta: meta_cards[meta].add(int(r['card_id']))

            target_reports=[]; candidates=[]; blocked=[]
            for code,cfg in TARGETS.items():
                tprods=[r for r in current_products if str(r['expansion_external_id'])==cfg['idExpansion']]
                cur.execute("""SELECT p.id print_id,p.card_id,p.collector_number,p.rarity,p.variant,c.name card_name
                    FROM prints p JOIN cards c ON c.id=p.card_id JOIN sets s ON s.id=p.set_id
                    WHERE c.game_id=%s AND upper(coalesce(s.code,''))=%s AND lower(coalesce(p.language,''))='ja'
                    ORDER BY p.card_id,p.id""",(gid,code))
                tprints=[dict(r) for r in cur.fetchall()]
                if (len(tprods),len(tprints))!=(cfg['products'],cfg['prints']): raise RuntimeError({'target_surface_drift':code,'products':len(tprods),'prints':len(tprints)})
                target_product_ids={str(r['id_product']) for r in tprods}; target_print_ids={int(r['print_id']) for r in tprints}
                target_accepted=[r for r in accepted if str(r['id_product']) in target_product_ids and int(r['print_id']) in target_print_ids]
                if len(target_accepted)!=cfg['accepted']: raise RuntimeError({'target_accepted_drift':code,'actual':len(target_accepted),'expected':cfg['accepted']})
                pg=defaultdict(list); pc=defaultdict(list); cname={}
                for r in tprods: pg[str(r.get('metacard_external_id') or '')].append(r)
                for r in tprints: pc[int(r['card_id'])].append(r); cname[int(r['card_id'])]=str(r['card_name'])
                set_candidates=[]; group_rows=[]
                for meta,prods in pg.items():
                    if not meta or len(prods)<2: continue
                    cards=meta_cards.get(meta,set())
                    if len(cards)!=1:
                        x={'set_code':code,'idMetacard':meta,'reason':'metacard_not_globally_unique','product_count':len(prods),'card_ids':sorted(cards)}; blocked.append(x); group_rows.append(x); continue
                    cid=next(iter(cards)); prints=pc.get(cid,[])
                    residual_products=[r for r in prods if not any_by_product.get(str(r['id_product']))]
                    residual_prints=[r for r in prints if not any_by_print.get(int(r['print_id']))]
                    base={
                        'set_code':code,'idExpansion':cfg['idExpansion'],'idMetacard':meta,'card_id':cid,'card_name':cname.get(cid,''),
                        'product_count':len(prods),'print_count':len(prints),'residual_products':len(residual_products),'residual_prints':len(residual_prints),
                        'idProducts':[str(r['id_product']) for r in sorted(prods,key=lambda x:int(x['id_product']))],
                        'canonical_prints':[{'print_id':int(r['print_id']),'collector_number':str(r['collector_number']),'rarity':str(r['rarity']),'variant':str(r['variant'] or '')} for r in prints],
                    }
                    if len(prods)!=len(prints) or len(residual_products)!=len(residual_prints):
                        x={**base,'reason':'product_print_cardinality_mismatch'}; blocked.append(x); group_rows.append(x); continue
                    if not residual_products:
                        group_rows.append({**base,'status':'already_complete'}); continue
                    rkey=rarity_key(r['rarity'] for r in prints); cal=calibrations.get(f'{len(prods)}:{rkey}')
                    if not cal:
                        x={**base,'rarity_key':rkey,'reason':'no_global_exact_reviewed_ordinal_controls'}; blocked.append(x); group_rows.append(x); continue
                    if cal['controls']<MIN_CONTROLS or cal['distinct_sequences']!=1:
                        x={**base,'rarity_key':rkey,'calibration':cal,'reason':'ordinal_calibration_not_strong_enough'}; blocked.append(x); group_rows.append(x); continue
                    sequence=cal['sequences'][0]['sequence']
                    print_by_rarity=defaultdict(list)
                    for r in prints: print_by_rarity[rarity(r['rarity'])].append(r)
                    if any(len(print_by_rarity[x])!=1 for x in sequence) or len(sequence)!=len(prods):
                        x={**base,'rarity_key':rkey,'calibration':cal,'reason':'canonical_rarity_sequence_not_bijective'}; blocked.append(x); group_rows.append(x); continue
                    ordered=sorted(prods,key=lambda x:int(x['id_product'])); pairs=[]; conflict=False
                    for prod,rar in zip(ordered,sequence):
                        pr=print_by_rarity[rar][0]; pid=str(prod['id_product']); print_id=int(pr['print_id'])
                        pclaims=any_by_product.get(pid,[]); rclaims=any_by_print.get(print_id,[])
                        if pclaims or rclaims:
                            same=any(int(r['print_id'])==print_id for r in pclaims)
                            if not same or any(str(r['id_product'])!=pid for r in rclaims): conflict=True; break
                            continue
                        pairs.append({
                            'set_code':code,'idExpansion':cfg['idExpansion'],'idMetacard':meta,'card_id':cid,'card_name':cname[cid],
                            'idProduct':pid,'external_product_id':int(prod['external_product_id']),'product_ordinal':ordered.index(prod)+1,
                            'calibrated_rarity':rar,'print_id':print_id,'collector_number':str(pr['collector_number']),
                            'canonical_rarity':str(pr['rarity']),'canonical_variant':str(pr['variant'] or ''),
                            'calibration_key':f'{len(prods)}:{rkey}','calibration_controls':cal['controls'],
                        })
                    if conflict or len(pairs)!=len(residual_products):
                        x={**base,'rarity_key':rkey,'reason':'existing_claim_conflict_or_partial_candidate'}; blocked.append(x); group_rows.append(x); continue
                    x={**base,'status':'global_ordinal_candidate','rarity_key':rkey,'calibration_controls':cal['controls'],'sequence':sequence,'candidate_pairs':len(pairs),'pairs':pairs}
                    group_rows.append(x); set_candidates.extend(pairs); candidates.extend(pairs)
                target_reports.append({
                    'set_code':code,'idExpansion':cfg['idExpansion'],'products':len(tprods),'prints':len(tprints),'accepted':len(target_accepted),
                    'residual':len(tprods)-len(target_accepted),'candidate_pairs':len(set_candidates),'candidate_groups':sum(r.get('status')=='global_ordinal_candidate' for r in group_rows),
                    'groups':group_rows,
                })
            conn.rollback()
    finally: conn.close()

    if len({r['idProduct'] for r in candidates})!=len(candidates) or len({r['print_id'] for r in candidates})!=len(candidates):
        raise RuntimeError('candidate surface is not globally one-to-one')
    payload={
        'status':'pass','mode':'read_only','production_writes':0,'cardmarket_capture':str(capture),
        'method':'global_complete_exact_reviewed_multiversion_controls_by_rarity_multiset_and_idProduct_ordinal',
        'minimum_independent_controls':MIN_CONTROLS,
        'complete_control_groups':len(controls),'calibration_buckets':calibrations,
        'candidate_pairs':len(candidates),'candidate_products':len({r['idProduct'] for r in candidates}),'candidate_prints':len({r['print_id'] for r in candidates}),
        'targets':target_reports,'proposal':candidates,'blocked_groups':blocked,
    }
    out=Path(os.getenv('YGO_OCG_GLOBAL_ORDINAL_OUTPUT','/tmp/yugioh-ocg-global-ordinal-calibration-v1.json'))
    out.parent.mkdir(parents=True,exist_ok=True); out.write_text(json.dumps(payload,ensure_ascii=False,indent=2,sort_keys=True,default=str)+'\n',encoding='utf-8')
    print(json.dumps(payload,ensure_ascii=False,indent=2,sort_keys=True,default=str)); return 0


if __name__=='__main__': raise SystemExit(main())
