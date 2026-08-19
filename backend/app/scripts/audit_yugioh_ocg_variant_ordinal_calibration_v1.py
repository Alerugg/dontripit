from __future__ import annotations

import json
import os
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

import psycopg2
from psycopg2.extras import RealDictCursor

from app.scripts.apply_yugioh_ocg_singleton_heavy_cohort_v1 import TARGETS, EXPECTED_CAPTURE, EXPECTED_JA, norm

GAME='yugioh'
ACCEPTED=('accepted','mapped','exact')
MIN_GROUP_SUPPORT=2


def rarity(v: object)->str:
    x=norm(v)
    aliases={
        'commonrare':'common','common':'common',
        'commonparallelrare':'commonparallel','commonparallel':'commonparallel',
        'superrare':'super','super':'super',
        'ultrarare':'ultra','ultra':'ultra',
        'secretrare':'secret','secret':'secret',
        'collectorsrare':'collectors','collectors':'collectors',
        'ultimaterare':'ultimate','ultimate':'ultimate',
        'ultraparallelrare':'ultraparallel','ultraparallel':'ultraparallel',
        'superparallelrare':'superparallel','superparallel':'superparallel',
        'secretparallelrare':'secretparallel','secretparallel':'secretparallel',
    }
    return aliases.get(x,x)


def signature(rows):
    return tuple(sorted(rarity(r['rarity']) for r in rows))


def seqkey(rows):
    return tuple(rarity(r['rarity']) for r in rows)


def main()->int:
    url=os.getenv('DATABASE_URL_UNPOOLED') or os.getenv('DATABASE_URL')
    if not url: raise RuntimeError('DATABASE URL required')
    conn=psycopg2.connect(url,connect_timeout=30,application_name='dontripit_ygo_ocg_variant_ordinal_calibration_v1')
    conn.set_session(readonly=True,autocommit=False)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT id FROM games WHERE slug=%s LIMIT 1",(GAME,)); gid=int(cur.fetchone()['id'])
            cur.execute("SELECT max(last_seen_at) capture FROM external_catalog_products WHERE source='cardmarket' AND game_id=%s",(gid,)); capture=cur.fetchone()['capture']
            if str(capture)!=EXPECTED_CAPTURE: raise RuntimeError({'capture_drift':str(capture)})
            cur.execute("SELECT count(*) n FROM prints p JOIN cards c ON c.id=p.card_id WHERE c.game_id=%s AND lower(coalesce(p.language,''))='ja'",(gid,)); ja=int(cur.fetchone()['n'])
            if ja!=EXPECTED_JA: raise RuntimeError({'ja_baseline_drift':ja})

            # Current Cardmarket physical product surface.
            cur.execute("""SELECT e.id external_product_id,e.external_id id_product,e.expansion_external_id,e.metacard_external_id,e.name
                FROM external_catalog_products e
                WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single' AND e.last_seen_at=%s
                  AND e.expansion_external_id IS NOT NULL AND e.metacard_external_id IS NOT NULL
                ORDER BY e.expansion_external_id,e.metacard_external_id,e.external_id::bigint""",(gid,capture))
            products=[dict(r) for r in cur.fetchall()]
            product_groups=defaultdict(list)
            for r in products: product_groups[(str(r['expansion_external_id']),str(r['metacard_external_id']))].append(r)

            # Exact reviewed current accepted JA mappings, excluding the older ordinal-derived method
            # so calibration is independent of the assumption being tested here.
            cur.execute("""SELECT e.id external_product_id,e.external_id id_product,e.expansion_external_id,e.metacard_external_id,e.name,
                       l.mapping_method,l.confidence,l.reviewed,p.id print_id,p.card_id,p.rarity,p.variant,p.collector_number,
                       c.name card_name,s.code set_code
                FROM external_catalog_print_links l JOIN external_catalog_products e ON e.id=l.external_product_id
                JOIN prints p ON p.id=l.print_id JOIN cards c ON c.id=p.card_id JOIN sets s ON s.id=p.set_id
                WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single' AND e.last_seen_at=%s
                  AND e.metacard_external_id IS NOT NULL AND l.link_status=ANY(%s)
                  AND lower(coalesce(p.language,''))='ja' AND l.confidence='exact' AND l.reviewed=true
                  AND l.mapping_method NOT LIKE '%%version_ordinal%%'
                ORDER BY e.expansion_external_id,e.metacard_external_id,e.external_id::bigint""",(gid,capture,list(ACCEPTED)))
            accepted=[dict(r) for r in cur.fetchall()]
            accepted_by_group=defaultdict(list); claims_product=defaultdict(list); claims_print=defaultdict(list)
            for r in accepted:
                accepted_by_group[(str(r['expansion_external_id']),str(r['metacard_external_id']))].append(r)
                claims_product[int(r['external_product_id'])].append(r); claims_print[int(r['print_id'])].append(r)

            # Build independent empirical calibration from already-certified multi-version groups.
            calibration=defaultdict(lambda: {'sequences':Counter(),'examples':defaultdict(list),'sets':defaultdict(set),'methods':defaultdict(Counter)})
            calibration_groups=0
            for key,group_products in product_groups.items():
                if len(group_products)<=1: continue
                rows=accepted_by_group.get(key,[])
                if len(rows)!=len(group_products): continue
                if len({int(r['external_product_id']) for r in rows})!=len(group_products): continue
                if len({int(r['print_id']) for r in rows})!=len(group_products): continue
                if len({int(r['card_id']) for r in rows})!=1 or len({str(r['set_code']).upper() for r in rows})!=1: continue
                if any(norm(r['name'])!=norm(r['card_name']) for r in rows): continue
                ordered=sorted(rows,key=lambda r:int(r['id_product']))
                sig=signature(ordered); seq=seqkey(ordered)
                if len(sig)!=len(set(sig)): continue  # rarity must identify prints bijectively.
                calibration[sig]['sequences'][seq]+=1
                calibration[sig]['sets'][seq].add(str(ordered[0]['set_code']).upper())
                calibration[sig]['methods'][seq].update(str(r['mapping_method']) for r in ordered)
                if len(calibration[sig]['examples'][seq])<8:
                    calibration[sig]['examples'][seq].append({'set_code':str(ordered[0]['set_code']).upper(),'idExpansion':key[0],'idMetacard':key[1],'idProducts':[str(r['id_product']) for r in ordered],'sequence':list(seq),'methods':[str(r['mapping_method']) for r in ordered]})
                calibration_groups+=1

            # All accepted claims, including ordinal methods, are used only as a conflict guard on target residuals.
            cur.execute("""SELECT l.external_product_id,l.print_id FROM external_catalog_print_links l
                JOIN external_catalog_products e ON e.id=l.external_product_id
                WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single' AND l.link_status=ANY(%s)""",(gid,list(ACCEPTED)))
            all_product_claims=set(); all_print_claims=set()
            for r in cur.fetchall(): all_product_claims.add(int(r['external_product_id'])); all_print_claims.add(int(r['print_id']))

            certifiable=[]; unresolved=[]; proposal=[]
            target_variant_groups=0; target_variant_physical=0
            for code,cfg in TARGETS.items():
                exp=str(cfg['idExpansion'])
                cur.execute("""SELECT p.id print_id,p.card_id,p.collector_number,p.rarity,p.variant,c.name card_name
                    FROM prints p JOIN cards c ON c.id=p.card_id JOIN sets s ON s.id=p.set_id
                    WHERE c.game_id=%s AND upper(coalesce(s.code,''))=%s AND lower(coalesce(p.language,''))='ja'
                    ORDER BY p.card_id,p.id""",(gid,code))
                prints=[dict(r) for r in cur.fetchall()]; by_card=defaultdict(list)
                for r in prints: by_card[int(r['card_id'])].append(r)

                for (gexp,meta),group_products in product_groups.items():
                    if gexp!=exp or len(group_products)<=1: continue
                    # Resolve card id from existing exact metacard evidence anywhere.
                    cur.execute("""SELECT DISTINCT p.card_id FROM external_catalog_print_links l
                        JOIN external_catalog_products e ON e.id=l.external_product_id JOIN prints p ON p.id=l.print_id
                        WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single'
                          AND e.metacard_external_id=%s AND l.link_status=ANY(%s)""",(gid,meta,list(ACCEPTED)))
                    cards={int(r['card_id']) for r in cur.fetchall()}
                    if len(cards)!=1: raise RuntimeError({'target_metacard_resolution_drift':code,'idMetacard':meta,'cards':sorted(cards)})
                    cid=next(iter(cards)); cprints=by_card.get(cid,[])
                    if len(cprints)!=len(group_products): continue
                    # Only residual unclaimed variant groups are in scope.
                    if any(int(p['external_product_id']) in all_product_claims for p in group_products): continue
                    if any(int(pr['print_id']) in all_print_claims for pr in cprints): continue
                    if any(norm(p['name'])!=norm(cprints[0]['card_name']) for p in group_products): raise RuntimeError({'target_name_drift':code,'idMetacard':meta})
                    target_variant_groups+=1; target_variant_physical+=len(group_products)
                    sig=signature(cprints)
                    cal=calibration.get(sig)
                    sequences=[] if not cal else list(cal['sequences'].items())
                    valid=[(seq,n) for seq,n in sequences if n>=MIN_GROUP_SUPPORT]
                    # Require one unique supported sequence and no competing observed sequence at any support.
                    if len(valid)!=1 or (cal and len(cal['sequences'])!=1):
                        unresolved.append({'set_code':code,'idExpansion':exp,'idMetacard':meta,'card_id':cid,'card_name':str(cprints[0]['card_name']),'idProducts':[str(p['id_product']) for p in sorted(group_products,key=lambda x:int(x['id_product']))],'rarity_signature':list(sig),'observed_sequences':[] if not cal else [{'sequence':list(seq),'support_groups':int(n),'support_sets':sorted(cal['sets'][seq]),'methods':dict(cal['methods'][seq])} for seq,n in cal['sequences'].items()]})
                        continue
                    seq,support=valid[0]
                    by_rarity=defaultdict(list)
                    for pr in cprints: by_rarity[rarity(pr['rarity'])].append(pr)
                    if any(len(by_rarity[r])!=1 for r in seq):
                        unresolved.append({'set_code':code,'idExpansion':exp,'idMetacard':meta,'card_id':cid,'card_name':str(cprints[0]['card_name']),'rarity_signature':list(sig),'reason':'canonical_rarity_not_bijective'})
                        continue
                    ordered=sorted(group_products,key=lambda x:int(x['id_product'])); pairs=[]
                    for ordinal,(prod,rar) in enumerate(zip(ordered,seq),1):
                        pr=by_rarity[rar][0]
                        row={'set_code':code,'idExpansion':exp,'idMetacard':meta,'card_id':cid,'card_name':str(pr['card_name']),'external_product_id':int(prod['external_product_id']),'idProduct':str(prod['id_product']),'product_ordinal':ordinal,'calibrated_rarity':rar,'print_id':int(pr['print_id']),'collector_number':str(pr['collector_number']),'canonical_rarity':str(pr['rarity']),'canonical_variant':str(pr.get('variant') or ''),'rarity_signature':list(sig),'calibration_support_groups':int(support),'calibration_support_sets':sorted(cal['sets'][seq]),'calibration_methods':dict(cal['methods'][seq])}
                        pairs.append(row); proposal.append(row)
                    certifiable.append({'set_code':code,'idExpansion':exp,'idMetacard':meta,'card_id':cid,'card_name':str(cprints[0]['card_name']),'rarity_signature':list(sig),'sequence':list(seq),'support_groups':int(support),'support_sets':sorted(cal['sets'][seq]),'pairs':pairs})
            conn.rollback()
    finally:
        conn.close()

    if len({x['external_product_id'] for x in proposal})!=len(proposal) or len({x['print_id'] for x in proposal})!=len(proposal):
        raise RuntimeError('calibrated proposal is not globally one-to-one')
    cal_summary=[]
    for sig,cal in sorted(calibration.items(),key=lambda kv:(len(kv[0]),kv[0])):
        cal_summary.append({'rarity_signature':list(sig),'sequences':[{'sequence':list(seq),'support_groups':int(n),'support_sets':sorted(cal['sets'][seq]),'methods':dict(cal['methods'][seq]),'examples':cal['examples'][seq]} for seq,n in cal['sequences'].items()]})
    payload={'status':'pass','mode':'read_only','production_writes':0,'cardmarket_capture':str(capture),'ja_baseline':ja,'minimum_group_support':MIN_GROUP_SUPPORT,'independent_calibration_groups':calibration_groups,'target_variant_groups':target_variant_groups,'target_variant_physical':target_variant_physical,'certifiable_groups':len(certifiable),'certifiable_pairs':len(proposal),'unresolved_groups':len(unresolved),'calibration':cal_summary,'certifiable':certifiable,'unresolved':unresolved,'proposal':proposal}
    out=Path(os.getenv('YGO_OCG_VARIANT_ORDINAL_CALIBRATION_OUTPUT','/tmp/ygo-ocg-variant-ordinal-calibration-v1.json'))
    out.parent.mkdir(parents=True,exist_ok=True); out.write_text(json.dumps(payload,ensure_ascii=False,indent=2,sort_keys=True,default=str)+'\n',encoding='utf-8'); print(json.dumps(payload,ensure_ascii=False,indent=2,sort_keys=True,default=str)); return 0

if __name__=='__main__': raise SystemExit(main())
