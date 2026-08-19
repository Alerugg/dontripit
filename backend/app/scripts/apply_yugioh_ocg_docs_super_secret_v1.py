from __future__ import annotations

import argparse
import json
import os
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

import psycopg2
from psycopg2.extras import Json, RealDictCursor

from app.scripts.audit_yugioh_ocg_docs_super_secret_v1 import evidence_sha256
from app.scripts.yugioh_ocg_docs_super_secret_manifest_v1 import (
    EVIDENCE_SHA256,
    EXPECTED_GROUPS,
    EXPECTED_METACARDS,
    EXPECTED_PAIRS,
    PAIR_MANIFEST_SHA256,
    manifest_sha256,
    pairs as frozen_pairs,
)

GAME='yugioh'
SET_CODE='DOCS'
ID_EXPANSION='4680'
ACCEPTED=('accepted','mapped','exact')
METHOD='cardmarket_ocg_certified_public_super_secret_contract_v1'
CONFIRM='APPLY_YUGIOH_OCG_DOCS_SUPER_SECRET_V1'
EXPECTED_JA=36426
EXPECTED_PRODUCTS=108
EXPECTED_PRINTS=108
ACCEPTED_BEFORE=88
ACCEPTED_AFTER=108


def norm(value: object)->str:
    text=unicodedata.normalize('NFKD',str(value or '')).casefold()
    return ''.join(ch for ch in text if ch.isalnum())


def connect(*,readonly: bool):
    url=os.getenv('DATABASE_URL_UNPOOLED') or os.getenv('DATABASE_URL')
    if not url: raise RuntimeError('DATABASE URL required')
    conn=psycopg2.connect(url,connect_timeout=30,application_name='dontripit_ygo_ocg_docs_super_secret_apply_v1')
    conn.set_session(readonly=readonly,autocommit=False); return conn


def guard_manifest()->list[dict]:
    if evidence_sha256()!=EVIDENCE_SHA256:
        raise RuntimeError({'evidence_sha256_drift':{'expected':EVIDENCE_SHA256,'actual':evidence_sha256()}})
    if manifest_sha256()!=PAIR_MANIFEST_SHA256:
        raise RuntimeError({'manifest_sha256_drift':{'expected':PAIR_MANIFEST_SHA256,'actual':manifest_sha256()}})
    rows=frozen_pairs()
    if len(rows)!=EXPECTED_PAIRS: raise RuntimeError({'manifest_pair_count_drift':len(rows)})
    if set(str(r['idMetacard']) for r in rows)!=set(EXPECTED_METACARDS): raise RuntimeError('manifest metacard set drift')
    if Counter(str(r['idMetacard']) for r in rows)!=Counter({m:2 for m in EXPECTED_METACARDS}): raise RuntimeError('manifest metacard cardinality drift')
    if len({int(r['external_product_id']) for r in rows})!=EXPECTED_PAIRS or len({str(r['idProduct']) for r in rows})!=EXPECTED_PAIRS or len({int(r['print_id']) for r in rows})!=EXPECTED_PAIRS:
        raise RuntimeError('manifest not globally one-to-one')
    return rows


def derive(cur)->dict:
    rows=guard_manifest()
    cur.execute("SELECT id FROM games WHERE slug=%s LIMIT 1",(GAME,)); game=cur.fetchone()
    if not game: raise RuntimeError('Yu-Gi-Oh game missing')
    gid=int(game['id'])
    cur.execute("SELECT max(last_seen_at) capture FROM external_catalog_products WHERE source='cardmarket' AND game_id=%s",(gid,)); capture=cur.fetchone()['capture']
    if capture is None: raise RuntimeError('Cardmarket capture missing')
    cur.execute("""SELECT count(*) n FROM prints p JOIN cards c ON c.id=p.card_id WHERE c.game_id=%s AND lower(coalesce(p.language,''))='ja'""",(gid,)); ja=int(cur.fetchone()['n'])
    if ja!=EXPECTED_JA: raise RuntimeError({'ja_baseline_drift':ja})

    cur.execute("""SELECT count(*) n FROM external_catalog_products e WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single' AND e.expansion_external_id=%s AND e.last_seen_at=%s""",(gid,ID_EXPANSION,capture)); products_total=int(cur.fetchone()['n'])
    cur.execute("""SELECT count(*) n FROM prints p JOIN cards c ON c.id=p.card_id JOIN sets s ON s.id=p.set_id WHERE c.game_id=%s AND upper(coalesce(s.code,''))=%s AND lower(coalesce(p.language,''))='ja'""",(gid,SET_CODE)); prints_total=int(cur.fetchone()['n'])
    if (products_total,prints_total)!=(EXPECTED_PRODUCTS,EXPECTED_PRINTS): raise RuntimeError({'DOCS_surface_drift':(products_total,prints_total)})

    extids=[int(r['external_product_id']) for r in rows]
    cur.execute("""SELECT e.id external_product_id,e.external_id id_product,e.name,e.metacard_external_id,e.expansion_external_id,e.last_seen_at FROM external_catalog_products e WHERE e.id=ANY(%s) AND e.source='cardmarket' AND e.game_id=%s AND e.product_group='single'""",(extids,gid)); products={int(r['external_product_id']):dict(r) for r in cur.fetchall()}
    if len(products)!=EXPECTED_PAIRS: raise RuntimeError({'manifest_products_missing':EXPECTED_PAIRS-len(products)})
    printids=[int(r['print_id']) for r in rows]
    cur.execute("""SELECT p.id print_id,p.card_id,p.collector_number,p.rarity,p.variant,p.language,s.code set_code,c.name card_name FROM prints p JOIN cards c ON c.id=p.card_id JOIN sets s ON s.id=p.set_id WHERE p.id=ANY(%s) AND c.game_id=%s""",(printids,gid)); prints={int(r['print_id']):dict(r) for r in cur.fetchall()}
    if len(prints)!=EXPECTED_PAIRS: raise RuntimeError({'manifest_prints_missing':EXPECTED_PAIRS-len(prints)})

    cur.execute("""SELECT e.external_id id_product,l.external_product_id,l.print_id,l.mapping_method,l.confidence,l.reviewed,l.link_status FROM external_catalog_print_links l JOIN external_catalog_products e ON e.id=l.external_product_id WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single' AND l.link_status=ANY(%s)""",(gid,list(ACCEPTED))); accepted=[dict(r) for r in cur.fetchall()]
    by_product=defaultdict(list); by_print=defaultdict(list)
    for r in accepted: by_product[int(r['external_product_id'])].append(r); by_print[int(r['print_id'])].append(r)

    cur.execute("""SELECT count(*) n,count(DISTINCT l.external_product_id) products,count(DISTINCT l.print_id) prints FROM external_catalog_print_links l JOIN external_catalog_products e ON e.id=l.external_product_id JOIN prints p ON p.id=l.print_id JOIN sets s ON s.id=p.set_id WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single' AND e.expansion_external_id=%s AND l.link_status=ANY(%s) AND lower(coalesce(p.language,''))='ja' AND upper(coalesce(s.code,''))=%s""",(gid,ID_EXPANSION,list(ACCEPTED),SET_CODE)); surf=cur.fetchone(); regional=int(surf['n'])
    if regional not in (ACCEPTED_BEFORE,ACCEPTED_AFTER): raise RuntimeError({'DOCS_accepted_surface_drift':regional})
    if (int(surf['products']),int(surf['prints']))!=(regional,regional): raise RuntimeError('DOCS accepted surface not one-to-one')

    groups=defaultdict(list)
    for r in rows: groups[str(r['idMetacard'])].append(r)
    for meta,group in groups.items():
        expected_products=sorted(str(r['idProduct']) for r in group)
        cur.execute("""SELECT e.external_id id_product FROM external_catalog_products e WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single' AND e.expansion_external_id=%s AND e.last_seen_at=%s AND e.metacard_external_id=%s ORDER BY e.external_id::bigint""",(gid,ID_EXPANSION,capture,meta)); actual_products=[str(r['id_product']) for r in cur.fetchall()]
        if actual_products!=sorted(expected_products,key=int): raise RuntimeError({'complete_metacard_product_surface_drift':meta,'expected':expected_products,'actual':actual_products})
        card_ids={int(r['card_id']) for r in group}
        if len(card_ids)!=1: raise RuntimeError({'manifest_group_multiple_cards':meta})
        cid=next(iter(card_ids)); expected_prints=sorted(int(r['print_id']) for r in group)
        cur.execute("""SELECT p.id print_id FROM prints p JOIN sets s ON s.id=p.set_id WHERE p.card_id=%s AND upper(coalesce(s.code,''))=%s AND lower(coalesce(p.language,''))='ja' ORDER BY p.id""",(cid,SET_CODE)); actual_prints=sorted(int(r['print_id']) for r in cur.fetchall())
        if actual_prints!=expected_prints: raise RuntimeError({'complete_card_print_surface_drift':meta,'expected':expected_prints,'actual':actual_prints})

    proposal=[]; existing=[]
    for f in rows:
        prod=products[int(f['external_product_id'])]; pr=prints[int(f['print_id'])]
        checks={
            'idProduct':str(prod['id_product'])==str(f['idProduct']),
            'idExpansion':str(prod['expansion_external_id'])==ID_EXPANSION,
            'idMetacard':str(prod['metacard_external_id'])==str(f['idMetacard']),
            'capture':prod['last_seen_at']==capture,
            'card_id':int(pr['card_id'])==int(f['card_id']),
            'language':str(pr['language'] or '').lower()=='ja',
            'set':str(pr['set_code'] or '').upper()==SET_CODE,
            'collector':str(pr['collector_number'] or '')==str(f['collector_number']),
            'rarity':str(pr['rarity'] or '').casefold()==str(f['canonical_rarity']).casefold(),
            'variant':str(pr['variant'] or '')==str(f['canonical_variant']),
            'contract_rarity':str(pr['rarity'] or '').casefold()==str(f['contract_rarity']).casefold(),
            'name':norm(prod['name'])==norm(pr['card_name']),
        }
        failed=[k for k,v in checks.items() if not v]
        if failed: raise RuntimeError({'manifest_live_identity_drift':{'idProduct':f['idProduct'],'print_id':f['print_id'],'failed':failed}})
        eid=int(f['external_product_id']); pid=int(f['print_id']); pclaims=by_product.get(eid,[]); rclaims=by_print.get(pid,[])
        competing_p=[r for r in pclaims if int(r['print_id'])!=pid]; competing_r=[r for r in rclaims if int(r['external_product_id'])!=eid]
        if competing_p or competing_r: raise RuntimeError({'accepted_identity_conflict':{'idProduct':f['idProduct'],'print_id':pid}})
        if pclaims or rclaims:
            same=[r for r in pclaims if int(r['print_id'])==pid and str(r.get('mapping_method') or '')==METHOD and str(r.get('confidence') or '')=='exact' and bool(r.get('reviewed')) and str(r.get('link_status') or '') in ACCEPTED]
            if len(same)!=1 or len(pclaims)!=1 or len(rclaims)!=1: raise RuntimeError({'unexpected_existing_pair':{'idProduct':f['idProduct'],'print_id':pid,'product_claims':pclaims,'print_claims':rclaims}})
            existing.append(f)
        else: proposal.append(f)

    if (len(existing),len(proposal)) not in ((0,EXPECTED_PAIRS),(EXPECTED_PAIRS,0)): raise RuntimeError({'partial_state_blocked':{'existing':len(existing),'new':len(proposal)}})
    if proposal and regional!=ACCEPTED_BEFORE: raise RuntimeError({'preapply_regional_baseline_drift':regional})
    if not proposal and regional!=ACCEPTED_AFTER: raise RuntimeError({'postapply_regional_baseline_drift':regional})
    return {'gid':gid,'capture':capture,'ja':ja,'regional':regional,'proposal':proposal,'existing':existing}


def run(*,apply: bool=False,confirm: str='')->dict:
    if apply and confirm!=CONFIRM: raise RuntimeError(f'--apply requires --confirm {CONFIRM}')
    conn=connect(readonly=not apply)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            state=derive(cur)
            report={'mode':'apply' if apply else 'dry_run','status':'pass','production_writes':0,'mapping_method':METHOD,'cardmarket_capture':str(state['capture']),'ja_baseline':state['ja'],'evidence_sha256':EVIDENCE_SHA256,'pair_manifest_sha256':PAIR_MANIFEST_SHA256,'certified_pairs':EXPECTED_PAIRS,'candidate_groups':EXPECTED_GROUPS,'already_accepted_same_pair':len(state['existing']),'new_links_ready':len(state['proposal']),'regional_accepted_before_or_after':state['regional']}
            if not apply: conn.rollback(); return report
            writes=0
            for r in state['proposal']:
                ev={'source':'cardmarket_first_party_public_docs_super_secret_contract+current_cardmarket_product_catalog+yugioh_canonical_physical_identity','identity_basis':['certified_DOCS_OCG_regional_expansion','first_party_DOCS_V1_super_V2_secret_contract','complete_current_two_product_metacard_surface','accepted_metacard_to_logical_card_bridge','complete_exact_DOCS_JA_two_print_surface','strict_normalized_name_match','frozen_product_ordinal_to_rarity_contract','global_product_and_print_unclaimed','global_one_to_one'],'evidence_sha256':EVIDENCE_SHA256,'pair_manifest_sha256':PAIR_MANIFEST_SHA256,'idExpansion':ID_EXPANSION,'idProduct':str(r['idProduct']),'idMetacard':str(r['idMetacard']),'collector_number':str(r['collector_number']),'canonical_variant':str(r['canonical_variant']),'canonical_rarity':str(r['canonical_rarity']),'product_ordinal':int(r['product_ordinal']),'contract_rarity':str(r['contract_rarity'])}
                cur.execute("""INSERT INTO external_catalog_print_links(external_product_id,print_id,mapping_method,confidence,link_status,reviewed,evidence) VALUES(%s,%s,%s,'exact','accepted',true,%s) ON CONFLICT(external_product_id,print_id) DO NOTHING""",(int(r['external_product_id']),int(r['print_id']),METHOD,Json(ev)))
                if cur.rowcount!=1: raise RuntimeError({'insert_failed':{'idProduct':r['idProduct'],'print_id':r['print_id']}})
                writes+=1
            if writes!=len(state['proposal']): raise RuntimeError({'write_count_drift':writes})
            report['production_writes']=writes; conn.commit(); return report
    except Exception:
        conn.rollback(); raise
    finally: conn.close()


def main()->int:
    p=argparse.ArgumentParser(); p.add_argument('--apply',action='store_true'); p.add_argument('--confirm',default=''); p.add_argument('--report',type=Path,default=Path('/tmp/yugioh-ocg-docs-super-secret-apply-v1.json')); a=p.parse_args()
    payload=run(apply=a.apply,confirm=a.confirm); a.report.parent.mkdir(parents=True,exist_ok=True); text=json.dumps(payload,ensure_ascii=False,indent=2,sort_keys=True,default=str)+'\n'; a.report.write_text(text,encoding='utf-8'); print(text,end=''); return 0


if __name__=='__main__': raise SystemExit(main())
