from __future__ import annotations

import argparse
import hashlib
import json
import os
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

import psycopg2
from psycopg2.extras import Json, RealDictCursor

GAME='yugioh'
ACCEPTED=('accepted','mapped','exact')
METHOD='cardmarket_ocg_certified_full_logical_bijection_cohort_v1'
CONFIRM='APPLY_YUGIOH_OCG_FULL_BIJECTION_COHORT_V1'
EXPECTED_JA=36426
EXPECTED_CAPTURE='2026-08-18 21:09:20.740891+00:00'
FROZEN_PROPOSAL_SHA256='ef0ee909fa46752d37df46f91df473be96ebdf3ef67a54e9be93fd7cbac57d03'
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
    'DP07': {'idExpansion':'4822','physical':30},
    'SD2': {'idExpansion':'4871','physical':28},
    'SSD2': {'idExpansion':'4599','physical':21},
    'EN01': {'idExpansion':'4686','physical':20},
    'JF09': {'idExpansion':'4805','physical':10},
    'PP13': {'idExpansion':'4762','physical':10},
    'PP14': {'idExpansion':'4749','physical':10},
    'PP15': {'idExpansion':'4729','physical':10},
}
EXPECTED_TOTAL=sum(x['physical'] for x in TARGETS.values())


def norm(value: object)->str:
    text=unicodedata.normalize('NFKD',str(value or '')).casefold()
    return ''.join(ch for ch in text if ch.isalnum())


def connect(readonly: bool):
    url=os.getenv('DATABASE_URL_UNPOOLED') or os.getenv('DATABASE_URL')
    if not url: raise RuntimeError('DATABASE URL required')
    conn=psycopg2.connect(url,connect_timeout=30,application_name='dontripit_ygo_ocg_full_bijection_cohort_apply_v1')
    conn.set_session(readonly=readonly,autocommit=False)
    return conn


def derive(cur):
    cur.execute("SELECT id FROM games WHERE slug=%s LIMIT 1",(GAME,)); row=cur.fetchone()
    if not row: raise RuntimeError('Yu-Gi-Oh game missing')
    gid=int(row['id'])
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

    cur.execute("""SELECT e.external_id id_product,l.external_product_id,l.print_id,l.mapping_method,l.confidence,l.reviewed,l.link_status
        FROM external_catalog_print_links l JOIN external_catalog_products e ON e.id=l.external_product_id
        WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single' AND l.link_status=ANY(%s)""",(gid,list(ACCEPTED)))
    by_product=defaultdict(list); by_print=defaultdict(list)
    for r in cur.fetchall():
        row=dict(r); by_product[int(r['external_product_id'])].append(row); by_print[int(r['print_id'])].append(row)

    cur.execute("""SELECT e.id external_product_id,e.external_id id_product,e.expansion_external_id,e.name,e.metacard_external_id
        FROM external_catalog_products e
        WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single' AND e.last_seen_at=%s AND e.expansion_external_id IS NOT NULL
        ORDER BY e.expansion_external_id,e.external_id::bigint""",(gid,capture))
    all_products=defaultdict(list)
    for r in cur.fetchall(): all_products[str(r['expansion_external_id'])].append(dict(r))

    all_pairs=[]; proposal=[]; existing=[]; reports=[]
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

        pairs=[]; product_cards=[]; ex=new=0
        for prod in products:
            meta=str(prod.get('metacard_external_id') or ''); cards=meta_cards.get(meta,set())
            if not meta or len(cards)!=1: raise RuntimeError({'metacard_resolution_drift':code,'idProduct':str(prod['id_product']),'cards':sorted(cards)})
            cid=next(iter(cards)); product_cards.append(cid)
            if cid not in by_card: raise RuntimeError({'resolved_card_outside_target_set':code,'idProduct':str(prod['id_product']),'card_id':cid})
            pr=by_card[cid][0]
            if norm(prod['name'])!=norm(pr['card_name']): raise RuntimeError({'name_drift':code,'idProduct':str(prod['id_product'])})
            eid=int(prod['external_product_id']); pid=int(pr['print_id'])
            pclaims=by_product.get(eid,[]); rclaims=by_print.get(pid,[])
            if any(int(r['print_id'])!=pid for r in pclaims) or any(int(r['external_product_id'])!=eid for r in rclaims):
                raise RuntimeError({'accepted_identity_conflict':code,'idProduct':str(prod['id_product']),'print_id':pid})
            same=[r for r in pclaims if int(r['print_id'])==pid]
            if same:
                r=same[0]
                if len(same)!=1 or len(pclaims)!=1 or len(rclaims)!=1 or str(r.get('mapping_method') or '')!=METHOD or str(r.get('confidence') or '')!='exact' or not bool(r.get('reviewed')):
                    raise RuntimeError({'unexpected_existing_pair':code,'idProduct':str(prod['id_product'])})
                ex+=1
            else:
                if pclaims or rclaims: raise RuntimeError({'unexpected_claim_state':code,'idProduct':str(prod['id_product']),'print_id':pid})
                new+=1
            ident={'set_code':code,'idExpansion':exp,'external_product_id':eid,'idProduct':str(prod['id_product']),'idMetacard':meta,'print_id':pid,'card_id':cid,'card_name':str(pr['card_name']),'collector_number':str(pr['collector_number']),'canonical_rarity':pr.get('rarity'),'canonical_variant':pr.get('variant'),'metacard_evidence_links':int(evidence.get((meta,cid),0))}
            pairs.append({**ident,'already_same':bool(same)}); all_pairs.append(ident)

        if len(set(product_cards))!=expected or set(product_cards)!=card_ids or Counter(norm(x['name']) for x in products)!=Counter(norm(x['card_name']) for x in prints):
            raise RuntimeError({'full_bijection_failed':code})

        canonical_names=Counter(norm(x['card_name']) for x in prints); competitors=[]
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
        if competitors: raise RuntimeError({'competing_full_bijection_expansion':code,'target':exp,'competitors':competitors})
        if (ex,new) not in ((0,expected),(expected,0)): raise RuntimeError({'partial_set_state':code,'existing':ex,'new':new})
        proposal.extend(x for x in pairs if not x['already_same']); existing.extend(x for x in pairs if x['already_same'])
        reports.append({'set_code':code,'idExpansion':exp,'pairs':expected,'existing_same':ex,'new_ready':new})

    raw=json.dumps(all_pairs,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode('utf-8')
    fingerprint=hashlib.sha256(raw).hexdigest()
    if fingerprint!=FROZEN_PROPOSAL_SHA256: raise RuntimeError({'frozen_proposal_hash_drift':fingerprint})
    if len(all_pairs)!=EXPECTED_TOTAL or len({x['external_product_id'] for x in all_pairs})!=EXPECTED_TOTAL or len({x['print_id'] for x in all_pairs})!=EXPECTED_TOTAL:
        raise RuntimeError({'global_bijection_failed':len(all_pairs)})
    if len(proposal)+len(existing)!=EXPECTED_TOTAL or (len(existing),len(proposal)) not in ((0,EXPECTED_TOTAL),(EXPECTED_TOTAL,0)):
        raise RuntimeError({'global_partial_state':{'existing':len(existing),'new':len(proposal)}})
    if not all(x['metacard_evidence_links']>0 for x in all_pairs): raise RuntimeError('pair without accepted metacard evidence')
    return {'gid':gid,'capture':capture,'ja':ja,'proposal':proposal,'existing':existing,'sets':reports,'fingerprint':fingerprint}


def run(apply: bool=False, confirm: str=''):
    if apply and confirm!=CONFIRM: raise RuntimeError(f'--apply requires --confirm {CONFIRM}')
    conn=connect(not apply)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            state=derive(cur)
            report={'mode':'apply' if apply else 'dry_run','status':'pass','production_writes':0,'mapping_method':METHOD,'cardmarket_capture':str(state['capture']),'ja_baseline':state['ja'],'frozen_proposal_sha256':state['fingerprint'],'certified_pairs':EXPECTED_TOTAL,'already_accepted_same_pair':len(state['existing']),'new_links_ready':len(state['proposal']),'sets':state['sets']}
            if not apply:
                conn.rollback(); return report
            writes=0
            for x in state['proposal']:
                ev={'source':'fresh_global_OCG_inventory+accepted_metacard_bridge+canonical_JA_full_bijection','identity_basis':['pinned_current_cardmarket_capture','complete_expansion_product_count_equals_exact_JA_physical_count','canonical_physical_equals_logical_cardinality','unique_accepted_metacard_to_logical_card','resolved_logical_card_set_equals_full_canonical_set','strict_normalized_name_match','no_competing_full_bijection_expansion','global_product_and_print_unclaimed','frozen_audited_proposal_hash'],'frozen_proposal_sha256':FROZEN_PROPOSAL_SHA256,'idExpansion':x['idExpansion'],'canonical_set':x['set_code'],'idProduct':x['idProduct'],'idMetacard':x['idMetacard'],'collector_number':x['collector_number'],'canonical_variant':x['canonical_variant'],'canonical_rarity':x['canonical_rarity'],'metacard_evidence_links':x['metacard_evidence_links']}
                cur.execute("""INSERT INTO external_catalog_print_links(external_product_id,print_id,mapping_method,confidence,link_status,reviewed,evidence)
                    VALUES(%s,%s,%s,'exact','accepted',true,%s) ON CONFLICT(external_product_id,print_id) DO NOTHING""",(x['external_product_id'],x['print_id'],METHOD,Json(ev)))
                if cur.rowcount!=1: raise RuntimeError({'insert_failed':x['idProduct']})
                writes+=1
            report['production_writes']=writes
            conn.commit(); return report
    except Exception:
        conn.rollback(); raise
    finally:
        conn.close()


def main()->int:
    p=argparse.ArgumentParser(); p.add_argument('--apply',action='store_true'); p.add_argument('--confirm',default=''); p.add_argument('--report',type=Path,default=Path('/tmp/yugioh-ocg-full-bijection-cohort-apply-v1.json')); a=p.parse_args()
    payload=run(a.apply,a.confirm); a.report.parent.mkdir(parents=True,exist_ok=True); text=json.dumps(payload,ensure_ascii=False,indent=2,sort_keys=True,default=str)+'\n'; a.report.write_text(text,encoding='utf-8'); print(text,end=''); return 0

if __name__=='__main__': raise SystemExit(main())
