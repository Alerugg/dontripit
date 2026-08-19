from __future__ import annotations

import json
import os
import re
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

import psycopg2
from psycopg2.extras import RealDictCursor

GAME='yugioh'
ACCEPTED=('accepted','mapped','exact')
SURFACES={
    'DOCS':{'idExpansion':'4680','products':108,'prints':108,'accepted':69},
    'LTGY':{'idExpansion':'4725','products':86,'prints':86,'accepted':75},
    'CSOC':{'idExpansion':'4809','products':87,'prints':87,'accepted':74},
}
VERSION_RE=re.compile(r'^(?P<base>.*?)\s*\(\s*V\.?\s*(?P<ordinal>\d+)\s*-\s*(?P<label>[^)]+?)\s*\)\s*$',re.I)

RARITY_LABELS={
    'common':'common',
    'rare':'rare',
    'superrare':'super',
    'ultrarare':'ultra',
    'secretrare':'secret',
    'ultimaterare':'ultimate',
    'prismaticsecretrare':'prismaticsecret',
    'quartercenturysecretrare':'quartercenturysecret',
    'starlightrare':'starlight',
    'holographicrare':'ghost',
    'ghostrare':'ghost',
    'collectorsrare':'collectors',
    'collectorraRE':'collectors',
    'parallelrare':'parallel',
}


def norm(v: object)->str:
    text=unicodedata.normalize('NFKD',str(v or '')).casefold()
    return ''.join(ch for ch in text if ch.isalnum())


def rarity_norm(v: object)->str:
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


def parse_product_name(name: object)->dict:
    raw=str(name or '').strip()
    m=VERSION_RE.match(raw)
    if not m:
        return {'raw':raw,'versioned':False,'base_name':raw,'ordinal':None,'label':None,'label_rarity':None}
    label=m.group('label').strip()
    return {
        'raw':raw,
        'versioned':True,
        'base_name':m.group('base').strip(),
        'ordinal':int(m.group('ordinal')),
        'label':label,
        'label_rarity':RARITY_LABELS.get(norm(label),rarity_norm(label)),
    }


def main()->int:
    url=os.getenv('DATABASE_URL_UNPOOLED') or os.getenv('DATABASE_URL')
    if not url:
        raise RuntimeError('DATABASE URL required')
    conn=psycopg2.connect(url,connect_timeout=30,application_name='dontripit_ygo_ocg_version_labels_v1')
    conn.set_session(readonly=True,autocommit=False)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT id FROM games WHERE slug=%s LIMIT 1",(GAME,)); gid=int(cur.fetchone()['id'])
            cur.execute("SELECT max(last_seen_at) capture FROM external_catalog_products WHERE source='cardmarket' AND game_id=%s",(gid,)); capture=cur.fetchone()['capture']
            if capture is None: raise RuntimeError('Cardmarket capture missing')

            # Stable global metacard -> logical Card bridge from already accepted evidence.
            cur.execute("""SELECT e.metacard_external_id,p.card_id,count(*) evidence
                FROM external_catalog_print_links l
                JOIN external_catalog_products e ON e.id=l.external_product_id
                JOIN prints p ON p.id=l.print_id
                WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single'
                  AND e.metacard_external_id IS NOT NULL AND l.link_status=ANY(%s)
                GROUP BY e.metacard_external_id,p.card_id""",(gid,list(ACCEPTED)))
            meta_cards=defaultdict(set); evidence=Counter()
            for r in cur.fetchall():
                meta=str(r['metacard_external_id']); cid=int(r['card_id']); meta_cards[meta].add(cid); evidence[(meta,cid)]+=int(r['evidence'] or 0)

            # Every accepted Cardmarket product/print claim is a hard global exclusion.
            cur.execute("""SELECT e.external_id id_product,l.external_product_id,l.print_id,l.mapping_method,l.confidence,l.reviewed
                FROM external_catalog_print_links l JOIN external_catalog_products e ON e.id=l.external_product_id
                WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single' AND l.link_status=ANY(%s)""",(gid,list(ACCEPTED)))
            accepted=[dict(r) for r in cur.fetchall()]
            by_product=defaultdict(list); by_print=defaultdict(list)
            for r in accepted:
                by_product[str(r['id_product'])].append(r); by_print[int(r['print_id'])].append(r)

            reports=[]; proposed=[]; blocked=[]
            for code,cfg in SURFACES.items():
                cur.execute("""SELECT e.id external_product_id,e.external_id id_product,e.name,e.metacard_external_id,e.expansion_external_id
                    FROM external_catalog_products e
                    WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single'
                      AND e.expansion_external_id=%s AND e.last_seen_at=%s
                    ORDER BY e.metacard_external_id,e.external_id::bigint""",(gid,cfg['idExpansion'],capture))
                products=[dict(r) for r in cur.fetchall()]
                cur.execute("""SELECT p.id print_id,p.card_id,p.collector_number,p.rarity,p.variant,c.name card_name
                    FROM prints p JOIN cards c ON c.id=p.card_id JOIN sets s ON s.id=p.set_id
                    WHERE c.game_id=%s AND upper(coalesce(s.code,''))=%s AND lower(coalesce(p.language,''))='ja'
                    ORDER BY p.card_id,p.collector_number,p.id""",(gid,code))
                prints=[dict(r) for r in cur.fetchall()]
                if (len(products),len(prints))!=(cfg['products'],cfg['prints']):
                    raise RuntimeError({'surface_drift':code,'products':len(products),'prints':len(prints),'expected':cfg})
                current_links=[]
                pids={int(x['external_product_id']) for x in products}; printids={int(x['print_id']) for x in prints}
                for r in accepted:
                    if int(r['external_product_id']) in pids and int(r['print_id']) in printids:
                        current_links.append(r)
                if len(current_links)!=cfg['accepted']:
                    raise RuntimeError({'accepted_surface_drift':code,'actual':len(current_links),'expected':cfg['accepted']})

                products_by_meta=defaultdict(list)
                prints_by_card=defaultdict(list)
                names_by_card={}
                for x in products: products_by_meta[str(x.get('metacard_external_id') or '')].append(x)
                for x in prints:
                    cid=int(x['card_id']); prints_by_card[cid].append(x); names_by_card[cid]=str(x['card_name'])

                set_groups=[]; set_proposed=[]
                for meta,group in products_by_meta.items():
                    if not meta: continue
                    globals_=meta_cards.get(meta,set())
                    if len(globals_)!=1:
                        set_groups.append({'idMetacard':meta,'status':'blocked','reason':'metacard_not_globally_unique','product_count':len(group),'global_card_ids':sorted(globals_)})
                        continue
                    cid=next(iter(globals_)); cprints=prints_by_card.get(cid,[])
                    if not cprints:
                        set_groups.append({'idMetacard':meta,'status':'blocked','reason':'resolved_card_has_no_JA_print_in_exact_set','product_count':len(group),'card_id':cid})
                        continue
                    parsed=[(x,parse_product_name(x['name'])) for x in sorted(group,key=lambda z:int(z['id_product']))]
                    base_names=sorted({p['base_name'] for _,p in parsed})
                    canonical_name=names_by_card[cid]
                    residual_products=[x for x,p in parsed if not by_product.get(str(x['id_product']))]
                    residual_prints=[x for x in cprints if not by_print.get(int(x['print_id']))]
                    versioned=all(p['versioned'] for _,p in parsed)
                    ordinals=[p['ordinal'] for _,p in parsed if p['versioned']]
                    all_labels=[p['label'] for _,p in parsed if p['versioned']]
                    canon_rarities=[rarity_norm(x['rarity']) for x in cprints]
                    group_report={
                        'idMetacard':meta,'card_id':cid,'card_name':canonical_name,
                        'product_count':len(group),'canonical_print_count':len(cprints),
                        'residual_products':len(residual_products),'residual_prints':len(residual_prints),
                        'product_names':[str(x['name']) for x,_ in parsed],
                        'base_names':base_names,'versioned_all':versioned,'ordinals':ordinals,
                        'version_labels':all_labels,'canonical_rarities':canon_rarities,
                        'metacard_evidence_links':int(evidence.get((meta,cid),0)),
                    }
                    if len(group)==1:
                        group_report.update({'status':'already_singleton_or_not_target','reason':'single_product_group'}); set_groups.append(group_report); continue
                    if len(group)!=len(cprints):
                        group_report.update({'status':'blocked','reason':'product_print_cardinality_mismatch'}); set_groups.append(group_report); blocked.append({'set_code':code,**group_report}); continue
                    if not residual_products and not residual_prints:
                        group_report.update({'status':'already_complete','reason':'all_products_and_prints_claimed'}); set_groups.append(group_report); continue
                    if len(residual_products)!=len(residual_prints):
                        group_report.update({'status':'blocked','reason':'residual_cardinality_mismatch'}); set_groups.append(group_report); blocked.append({'set_code':code,**group_report}); continue
                    if not versioned or ordinals!=list(range(1,len(group)+1)):
                        group_report.update({'status':'blocked','reason':'cardmarket_version_labels_incomplete_or_nonsequential'}); set_groups.append(group_report); blocked.append({'set_code':code,**group_report}); continue
                    if any(norm(p['base_name'])!=norm(canonical_name) for _,p in parsed):
                        group_report.update({'status':'blocked','reason':'version_base_name_mismatch'}); set_groups.append(group_report); blocked.append({'set_code':code,**group_report}); continue
                    # Label-based physical mapping is accepted only where each Cardmarket label and canonical rarity is unique.
                    product_by_rarity=defaultdict(list)
                    for x,p in parsed: product_by_rarity[p['label_rarity']].append((x,p))
                    print_by_rarity=defaultdict(list)
                    for x in cprints: print_by_rarity[rarity_norm(x['rarity'])].append(x)
                    if set(product_by_rarity)!=set(print_by_rarity):
                        group_report.update({'status':'blocked','reason':'version_label_rarity_set_mismatch','product_rarities':sorted(product_by_rarity),'print_rarities':sorted(print_by_rarity)}); set_groups.append(group_report); blocked.append({'set_code':code,**group_report}); continue
                    if any(len(product_by_rarity[k])!=1 or len(print_by_rarity[k])!=1 for k in product_by_rarity):
                        group_report.update({'status':'blocked','reason':'rarity_not_bijective_within_group'}); set_groups.append(group_report); blocked.append({'set_code':code,**group_report}); continue
                    pairs=[]; conflict=False
                    for rarity in sorted(product_by_rarity):
                        prod,parsed_name=product_by_rarity[rarity][0]; pr=print_by_rarity[rarity][0]
                        pid=str(prod['id_product']); print_id=int(pr['print_id'])
                        pclaims=by_product.get(pid,[]); rclaims=by_print.get(print_id,[])
                        if pclaims or rclaims:
                            same=any(int(r['print_id'])==print_id for r in pclaims)
                            if not same or any(str(r['id_product'])!=pid for r in rclaims):
                                conflict=True; break
                            continue
                        pairs.append({
                            'set_code':code,'idExpansion':cfg['idExpansion'],'idProduct':pid,
                            'external_product_id':int(prod['external_product_id']),'idMetacard':meta,
                            'product_name':str(prod['name']),'version_ordinal':int(parsed_name['ordinal']),
                            'version_label':str(parsed_name['label']),'label_rarity':rarity,
                            'print_id':print_id,'card_id':cid,'card_name':canonical_name,
                            'collector_number':str(pr['collector_number']),'canonical_rarity':str(pr['rarity']),
                            'canonical_variant':str(pr['variant'] or ''),
                        })
                    if conflict:
                        group_report.update({'status':'blocked','reason':'accepted_identity_conflict'}); set_groups.append(group_report); blocked.append({'set_code':code,**group_report}); continue
                    if len(pairs)!=len(residual_products):
                        group_report.update({'status':'blocked','reason':'candidate_pair_count_not_equal_residual'}); set_groups.append(group_report); blocked.append({'set_code':code,**group_report}); continue
                    group_report.update({'status':'version_label_bijection_candidate','candidate_pairs':len(pairs),'pair_preview':pairs}); set_groups.append(group_report); set_proposed.extend(pairs); proposed.extend(pairs)

                reports.append({
                    'set_code':code,'idExpansion':cfg['idExpansion'],'products':len(products),'canonical_ja_prints':len(prints),
                    'accepted_links':len(current_links),'residual_products':len(products)-len(current_links),'residual_prints':len(prints)-len(current_links),
                    'version_label_candidate_pairs':len(set_proposed),
                    'version_label_candidate_groups':sum(g.get('status')=='version_label_bijection_candidate' for g in set_groups),
                    'groups':set_groups,
                })
            conn.rollback()
    finally:
        conn.close()

    if len({p['idProduct'] for p in proposed})!=len(proposed) or len({p['print_id'] for p in proposed})!=len(proposed):
        raise RuntimeError('global candidate surface is not one-to-one')
    payload={
        'status':'pass','mode':'read_only','production_writes':0,'cardmarket_capture':str(capture),
        'method':'explicit_cardmarket_version_label_to_unique_canonical_rarity_bijection',
        'candidate_pairs':len(proposed),'candidate_products':len({p['idProduct'] for p in proposed}),
        'candidate_prints':len({p['print_id'] for p in proposed}),'sets':reports,
        'proposal':proposed,'blocked_groups':blocked,
    }
    out=Path(os.getenv('YGO_OCG_VERSION_LABELS_OUTPUT','/tmp/yugioh-ocg-version-labels-v1.json'))
    out.parent.mkdir(parents=True,exist_ok=True); out.write_text(json.dumps(payload,ensure_ascii=False,indent=2,sort_keys=True,default=str)+'\n',encoding='utf-8')
    print(json.dumps(payload,ensure_ascii=False,indent=2,sort_keys=True,default=str)); return 0


if __name__=='__main__': raise SystemExit(main())
