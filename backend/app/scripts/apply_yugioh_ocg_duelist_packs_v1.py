from __future__ import annotations

import argparse
import json
import os
from collections import Counter, defaultdict
from pathlib import Path

import psycopg2
from psycopg2.extras import Json, RealDictCursor

from app.scripts.audit_yugioh_ocg_duelist_packs_public_code_v1 import TARGETS,evidence_sha256,norm

GAME='yugioh'
ACCEPTED=('accepted','mapped','exact')
METHOD='cardmarket_ocg_certified_public_code_singleton_v1'
CONFIRM='APPLY_YUGIOH_OCG_DUELIST_PACKS_V1'
EXPECTED_JA=36426
EXPECTED_TOTAL=219
EXPECTED_CAPTURE='2026-08-18 21:09:20.740891+00:00'
EXPECTED={'DP23':56,'DP22':56,'DP21':56,'DP19':51}
EVIDENCE_SHA256='8c8a39536d03b5f8e2feeeae05ef12c80a0610227c856c0b0721f7f583e3ee26'


def connect(*,readonly: bool):
    url=os.getenv('DATABASE_URL_UNPOOLED') or os.getenv('DATABASE_URL')
    if not url: raise RuntimeError('DATABASE URL required')
    conn=psycopg2.connect(url,connect_timeout=30,application_name='dontripit_ygo_ocg_duelist_packs_apply_v1')
    conn.set_session(readonly=readonly,autocommit=False); return conn


def derive(cur)->dict:
    if evidence_sha256()!=EVIDENCE_SHA256: raise RuntimeError({'identity_evidence_sha256_drift':{'expected':EVIDENCE_SHA256,'actual':evidence_sha256()}})
    cur.execute("SELECT id FROM games WHERE slug=%s LIMIT 1",(GAME,)); row=cur.fetchone()
    if not row: raise RuntimeError('Yu-Gi-Oh game missing')
    gid=int(row['id'])
    cur.execute("SELECT max(last_seen_at) capture FROM external_catalog_products WHERE source='cardmarket' AND game_id=%s",(gid,)); capture=cur.fetchone()['capture']
    if capture is None: raise RuntimeError('Cardmarket capture missing')
    if str(capture)!=EXPECTED_CAPTURE: raise RuntimeError({'cardmarket_capture_drift':{'expected':EXPECTED_CAPTURE,'actual':str(capture)}})
    cur.execute("""SELECT count(*) n FROM prints p JOIN cards c ON c.id=p.card_id WHERE c.game_id=%s AND lower(coalesce(p.language,''))='ja'""",(gid,)); ja=int(cur.fetchone()['n'])
    if ja!=EXPECTED_JA: raise RuntimeError({'ja_baseline_drift':ja})

    cur.execute("""SELECT e.metacard_external_id,p.card_id,count(*) evidence_links FROM external_catalog_print_links l
        JOIN external_catalog_products e ON e.id=l.external_product_id JOIN prints p ON p.id=l.print_id
        WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single' AND e.metacard_external_id IS NOT NULL
          AND l.link_status=ANY(%s) GROUP BY e.metacard_external_id,p.card_id""",(gid,list(ACCEPTED)))
    meta_cards=defaultdict(set); evidence=Counter()
    for r in cur.fetchall():
        meta=str(r['metacard_external_id']); cid=int(r['card_id']); meta_cards[meta].add(cid); evidence[(meta,cid)]+=int(r['evidence_links'] or 0)

    cur.execute("""SELECT e.external_id id_product,l.external_product_id,l.print_id,l.mapping_method,l.confidence,l.reviewed,l.link_status
        FROM external_catalog_print_links l JOIN external_catalog_products e ON e.id=l.external_product_id
        WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single' AND l.link_status=ANY(%s)""",(gid,list(ACCEPTED)))
    accepted=[dict(r) for r in cur.fetchall()]; by_product=defaultdict(list); by_print=defaultdict(list)
    for r in accepted:
        by_product[int(r['external_product_id'])].append(r); by_print[int(r['print_id'])].append(r)

    proposal=[]; existing=[]; reports=[]
    for code,cfg in TARGETS.items():
        expected=EXPECTED[code]
        cur.execute("""SELECT e.id external_product_id,e.external_id id_product,e.name,e.metacard_external_id,e.expansion_external_id,e.last_seen_at
            FROM external_catalog_products e WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single'
              AND e.expansion_external_id=%s AND e.last_seen_at=%s ORDER BY e.metacard_external_id,e.external_id::bigint""",(gid,cfg['idExpansion'],capture)); products=[dict(r) for r in cur.fetchall()]
        cur.execute("""SELECT p.id print_id,p.card_id,p.collector_number,p.rarity,p.variant,p.language,c.name card_name,s.code set_code
            FROM prints p JOIN cards c ON c.id=p.card_id JOIN sets s ON s.id=p.set_id
            WHERE c.game_id=%s AND upper(coalesce(s.code,''))=%s AND lower(coalesce(p.language,''))='ja'
            ORDER BY p.card_id,p.collector_number,p.id""",(gid,code)); prints=[dict(r) for r in cur.fetchall()]
        if (len(products),len(prints))!=(expected,expected): raise RuntimeError({'surface_drift':code,'products':len(products),'prints':len(prints)})
        pc=defaultdict(list)
        for pr in prints: pc[int(pr['card_id'])].append(pr)
        pairs=[]; ex=0; new=0
        for prod in products:
            meta=str(prod.get('metacard_external_id') or ''); cards=meta_cards.get(meta,set())
            if not meta or len(cards)!=1: raise RuntimeError({'metacard_resolution_drift':code,'idProduct':str(prod['id_product']),'cards':sorted(cards)})
            cid=next(iter(cards)); cprints=pc.get(cid,[])
            if len(cprints)!=1: raise RuntimeError({'canonical_print_not_singleton':code,'idProduct':str(prod['id_product']),'card_id':cid,'print_count':len(cprints)})
            pr=cprints[0]
            if norm(prod['name'])!=norm(pr['card_name']): raise RuntimeError({'name_drift':code,'idProduct':str(prod['id_product']),'product':str(prod['name']),'card':str(pr['card_name'])})
            eid=int(prod['external_product_id']); pid=int(pr['print_id']); pclaims=by_product.get(eid,[]); rclaims=by_print.get(pid,[])
            competing_p=[r for r in pclaims if int(r['print_id'])!=pid]; competing_r=[r for r in rclaims if int(r['external_product_id'])!=eid]
            if competing_p or competing_r: raise RuntimeError({'accepted_identity_conflict':code,'idProduct':str(prod['id_product']),'print_id':pid})
            same=[r for r in pclaims if int(r['print_id'])==pid]
            if same:
                if len(same)!=1 or len(pclaims)!=1 or len(rclaims)!=1: raise RuntimeError({'duplicate_existing_pair':code,'idProduct':str(prod['id_product'])})
                r=same[0]
                if str(r.get('mapping_method') or '')!=METHOD or str(r.get('confidence') or '')!='exact' or not bool(r.get('reviewed')) or str(r.get('link_status') or '') not in ACCEPTED: raise RuntimeError({'unexpected_existing_pair':code,'idProduct':str(prod['id_product']),'existing':r})
                ex+=1
            else:
                new+=1
            pairs.append({'set_code':code,'idExpansion':str(cfg['idExpansion']),'external_product_id':eid,'idProduct':str(prod['id_product']),'idMetacard':meta,'print_id':pid,'card_id':cid,'card_name':str(pr['card_name']),'collector_number':str(pr['collector_number']),'canonical_rarity':pr.get('rarity'),'canonical_variant':pr.get('variant'),'metacard_evidence_links':int(evidence.get((meta,cid),0)),'already_same':bool(same)})
        if len(pairs)!=expected or len({x['external_product_id'] for x in pairs})!=expected or len({x['print_id'] for x in pairs})!=expected: raise RuntimeError({'not_one_to_one':code})
        if (ex,new) not in ((0,expected),(expected,0)): raise RuntimeError({'partial_set_state_blocked':code,'existing':ex,'new':new})
        proposal.extend(x for x in pairs if not x['already_same']); existing.extend(x for x in pairs if x['already_same']); reports.append({'set_code':code,'idExpansion':str(cfg['idExpansion']),'products':len(products),'canonical_ja_prints':len(prints),'pairs':len(pairs),'existing_same':ex,'new_ready':new})

    if len(proposal)+len(existing)!=EXPECTED_TOTAL or len({x['external_product_id'] for x in proposal+existing})!=EXPECTED_TOTAL or len({x['print_id'] for x in proposal+existing})!=EXPECTED_TOTAL: raise RuntimeError('global surface not 219 one-to-one')
    if (len(existing),len(proposal)) not in ((0,EXPECTED_TOTAL),(EXPECTED_TOTAL,0)): raise RuntimeError({'global_partial_state_blocked':{'existing':len(existing),'new':len(proposal)}})
    return {'gid':gid,'capture':capture,'ja':ja,'proposal':proposal,'existing':existing,'sets':reports}


def run(*,apply: bool=False,confirm: str='')->dict:
    if apply and confirm!=CONFIRM: raise RuntimeError(f'--apply requires --confirm {CONFIRM}')
    conn=connect(readonly=not apply)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            state=derive(cur)
            report={'mode':'apply' if apply else 'dry_run','status':'pass','production_writes':0,'mapping_method':METHOD,'cardmarket_capture':str(state['capture']),'ja_baseline':state['ja'],'identity_evidence_sha256':EVIDENCE_SHA256,'certified_pairs':EXPECTED_TOTAL,'already_accepted_same_pair':len(state['existing']),'new_links_ready':len(state['proposal']),'sets':state['sets']}
            if not apply: conn.rollback(); return report
            writes=0
            for x in state['proposal']:
                ev={'source':'cardmarket_first_party_public_duelist_pack_code+current_cardmarket_product_catalog+yugioh_canonical_physical_identity','identity_basis':['first_party_public_Duelist_Pack_code','pinned_current_cardmarket_capture','complete_regional_product_to_exact_JA_name_multiset','unique_metacard_per_product','accepted_metacard_to_logical_card_bridge','one_canonical_JA_print_per_resolved_card_in_exact_set','strict_normalized_name_match','global_product_and_print_unclaimed','global_one_to_one'],'identity_evidence_sha256':EVIDENCE_SHA256,'idExpansion':x['idExpansion'],'canonical_set':x['set_code'],'idProduct':x['idProduct'],'idMetacard':x['idMetacard'],'collector_number':x['collector_number'],'canonical_variant':x['canonical_variant'],'canonical_rarity':x['canonical_rarity'],'metacard_evidence_links':x['metacard_evidence_links']}
                cur.execute("""INSERT INTO external_catalog_print_links(external_product_id,print_id,mapping_method,confidence,link_status,reviewed,evidence)
                    VALUES(%s,%s,%s,'exact','accepted',true,%s) ON CONFLICT(external_product_id,print_id) DO NOTHING""",(x['external_product_id'],x['print_id'],METHOD,Json(ev)))
                if cur.rowcount!=1: raise RuntimeError({'insert_failed':x['idProduct'],'print_id':x['print_id']})
                writes+=1
            if writes!=len(state['proposal']): raise RuntimeError({'write_count_drift':writes})
            report['production_writes']=writes; conn.commit(); return report
    except Exception:
        conn.rollback(); raise
    finally: conn.close()


def main()->int:
    p=argparse.ArgumentParser(); p.add_argument('--apply',action='store_true'); p.add_argument('--confirm',default=''); p.add_argument('--report',type=Path,default=Path('/tmp/yugioh-ocg-duelist-packs-apply-v1.json')); a=p.parse_args()
    payload=run(apply=a.apply,confirm=a.confirm); a.report.parent.mkdir(parents=True,exist_ok=True); text=json.dumps(payload,ensure_ascii=False,indent=2,sort_keys=True,default=str)+'\n'; a.report.write_text(text,encoding='utf-8'); print(text,end=''); return 0

if __name__=='__main__': raise SystemExit(main())
