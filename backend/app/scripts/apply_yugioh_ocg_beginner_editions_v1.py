from __future__ import annotations

import argparse
import json
import os
from collections import Counter, defaultdict
from pathlib import Path

import psycopg2
from psycopg2.extras import Json, RealDictCursor

from app.scripts.audit_yugioh_ocg_beginner_editions_bijection_v1 import TARGETS, contract_sha256, norm

GAME='yugioh'
ACCEPTED=('accepted','mapped','exact')
METHOD='cardmarket_ocg_certified_full_logical_bijection_v1'
CONFIRM='APPLY_YUGIOH_OCG_BEGINNER_EDITIONS_V1'
EXPECTED_JA=36426
EXPECTED_CAPTURE='2026-08-18 21:09:20.740891+00:00'
EXPECTED_CONTRACT='9453b70741f5a1c5ca168f447d25389d06fa3e2e97c1e5427a3cd861f05a7eaf'
EXPECTED_TOTAL=sum(x['physical'] for x in TARGETS.values())


def connect(readonly: bool):
    url=os.getenv('DATABASE_URL_UNPOOLED') or os.getenv('DATABASE_URL')
    if not url: raise RuntimeError('DATABASE URL required')
    conn=psycopg2.connect(url,connect_timeout=30,application_name='dontripit_ygo_ocg_beginner_editions_apply_v1')
    conn.set_session(readonly=readonly,autocommit=False)
    return conn


def derive(cur):
    if contract_sha256()!=EXPECTED_CONTRACT: raise RuntimeError('Beginner Edition target contract drift')
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

    cur.execute("""SELECT e.external_id id_product,l.external_product_id,l.print_id,l.mapping_method,l.confidence,l.reviewed,l.link_status
        FROM external_catalog_print_links l JOIN external_catalog_products e ON e.id=l.external_product_id
        WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single' AND l.link_status=ANY(%s)""",(gid,list(ACCEPTED)))
    by_product=defaultdict(list); by_print=defaultdict(list)
    for r in cur.fetchall():
        row=dict(r); by_product[int(r['external_product_id'])].append(row); by_print[int(r['print_id'])].append(row)

    proposal=[]; existing=[]; reports=[]
    for code,cfg in TARGETS.items():
        cur.execute("""SELECT e.id external_product_id,e.external_id id_product,e.name,e.metacard_external_id
            FROM external_catalog_products e
            WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single'
              AND e.expansion_external_id=%s AND e.last_seen_at=%s
            ORDER BY e.external_id::bigint""",(gid,cfg['idExpansion'],capture)); products=[dict(r) for r in cur.fetchall()]
        cur.execute("""SELECT p.id print_id,p.card_id,p.collector_number,p.rarity,p.variant,c.name card_name,s.name set_name
            FROM prints p JOIN cards c ON c.id=p.card_id JOIN sets s ON s.id=p.set_id
            WHERE c.game_id=%s AND upper(coalesce(s.code,''))=%s AND lower(coalesce(p.language,''))='ja'
            ORDER BY p.card_id,p.id""",(gid,code)); prints=[dict(r) for r in cur.fetchall()]
        if len(products)!=cfg['physical'] or len(prints)!=cfg['physical']:
            raise RuntimeError({'surface_count_drift':code,'products':len(products),'prints':len(prints)})
        card_ids={int(x['card_id']) for x in prints}
        if len(card_ids)!=cfg['logical'] or cfg['logical']!=cfg['physical']:
            raise RuntimeError({'canonical_not_bijection':code})
        if {str(x['set_name']) for x in prints}!={cfg['set_name']}:
            raise RuntimeError({'canonical_set_name_drift':code})
        prints_by_card=defaultdict(list)
        for x in prints: prints_by_card[int(x['card_id'])].append(x)
        if any(len(v)!=1 for v in prints_by_card.values()): raise RuntimeError({'canonical_duplicate_card_prints':code})

        pairs=[]; product_cards=[]; ex=new=0
        for prod in products:
            meta=str(prod.get('metacard_external_id') or '')
            cards=meta_cards.get(meta,set())
            if not meta or len(cards)!=1:
                raise RuntimeError({'metacard_resolution_drift':code,'idProduct':str(prod['id_product']),'cards':sorted(cards)})
            cid=next(iter(cards)); product_cards.append(cid)
            if cid not in prints_by_card:
                raise RuntimeError({'resolved_card_outside_target_set':code,'idProduct':str(prod['id_product']),'card_id':cid})
            pr=prints_by_card[cid][0]
            if norm(prod['name'])!=norm(pr['card_name']):
                raise RuntimeError({'name_drift':code,'idProduct':str(prod['id_product'])})
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
                new+=1
            pair={'set_code':code,'idExpansion':str(cfg['idExpansion']),'external_product_id':eid,'idProduct':str(prod['id_product']),'idMetacard':meta,'print_id':pid,'card_id':cid,'card_name':str(pr['card_name']),'collector_number':str(pr['collector_number']),'canonical_rarity':pr.get('rarity'),'canonical_variant':pr.get('variant'),'metacard_evidence_links':int(evidence.get((meta,cid),0)),'already_same':bool(same)}
            pairs.append(pair)

        if len(product_cards)!=cfg['logical'] or len(set(product_cards))!=cfg['logical'] or set(product_cards)!=card_ids:
            raise RuntimeError({'logical_card_bijection_failed':code})
        if Counter(norm(x['name']) for x in products)!=Counter(norm(x['card_name']) for x in prints):
            raise RuntimeError({'normalized_name_multiset_drift':code})
        expected=cfg['physical']
        if len(pairs)!=expected or len({x['external_product_id'] for x in pairs})!=expected or len({x['print_id'] for x in pairs})!=expected:
            raise RuntimeError({'physical_bijection_failed':code})
        if (ex,new) not in ((0,expected),(expected,0)):
            raise RuntimeError({'partial_set_state':code,'existing':ex,'new':new})
        proposal.extend(x for x in pairs if not x['already_same']); existing.extend(x for x in pairs if x['already_same'])
        reports.append({'set_code':code,'idExpansion':str(cfg['idExpansion']),'pairs':expected,'existing_same':ex,'new_ready':new})

    if len(proposal)+len(existing)!=EXPECTED_TOTAL or (len(existing),len(proposal)) not in ((0,EXPECTED_TOTAL),(EXPECTED_TOTAL,0)):
        raise RuntimeError({'global_partial_state':{'existing':len(existing),'new':len(proposal)}})
    if not all(x['metacard_evidence_links']>0 for x in proposal+existing):
        raise RuntimeError('pair without accepted metacard evidence')
    return {'gid':gid,'capture':capture,'ja':ja,'proposal':proposal,'existing':existing,'sets':reports}


def run(apply: bool=False, confirm: str=''):
    if apply and confirm!=CONFIRM: raise RuntimeError(f'--apply requires --confirm {CONFIRM}')
    conn=connect(not apply)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            state=derive(cur)
            report={'mode':'apply' if apply else 'dry_run','status':'pass','production_writes':0,'mapping_method':METHOD,'cardmarket_capture':str(state['capture']),'ja_baseline':state['ja'],'contract_sha256':EXPECTED_CONTRACT,'certified_pairs':EXPECTED_TOTAL,'already_accepted_same_pair':len(state['existing']),'new_links_ready':len(state['proposal']),'sets':state['sets']}
            if not apply:
                conn.rollback(); return report
            writes=0
            for x in state['proposal']:
                ev={'source':'current_cardmarket_catalog+accepted_metacard_bridge+yugioh_canonical_JA_full_set_bijection','identity_basis':['pinned_current_cardmarket_capture','complete_expansion_product_count_equals_exact_JA_physical_count','canonical_physical_equals_logical_cardinality','unique_accepted_metacard_to_logical_card','resolved_logical_card_set_equals_full_canonical_set','strict_normalized_name_match','global_product_and_print_unclaimed','global_one_to_one'],'contract_sha256':EXPECTED_CONTRACT,'idExpansion':x['idExpansion'],'canonical_set':x['set_code'],'idProduct':x['idProduct'],'idMetacard':x['idMetacard'],'collector_number':x['collector_number'],'canonical_variant':x['canonical_variant'],'canonical_rarity':x['canonical_rarity'],'metacard_evidence_links':x['metacard_evidence_links']}
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
    p=argparse.ArgumentParser(); p.add_argument('--apply',action='store_true'); p.add_argument('--confirm',default=''); p.add_argument('--report',type=Path,default=Path('/tmp/yugioh-ocg-beginner-editions-apply-v1.json')); a=p.parse_args()
    payload=run(a.apply,a.confirm); a.report.parent.mkdir(parents=True,exist_ok=True); text=json.dumps(payload,ensure_ascii=False,indent=2,sort_keys=True,default=str)+'\n'; a.report.write_text(text,encoding='utf-8'); print(text,end=''); return 0

if __name__=='__main__': raise SystemExit(main())
