from __future__ import annotations

import argparse, hashlib, json, os
from collections import Counter, defaultdict
from pathlib import Path

import psycopg2
from psycopg2.extras import Json, RealDictCursor

from app.scripts.audit_yugioh_ocg_full_bijection_cohort_v1 import norm

GAME='yugioh'
ACCEPTED=('accepted','mapped','exact')
METHOD='cardmarket_ocg_certified_logical_subset_bijection_be01_v1'
CONFIRM='APPLY_YUGIOH_OCG_BE01_205_V1'
EXPECTED_JA=36426
EXPECTED_CAPTURE='2026-08-18 21:09:20.740891+00:00'
SET_CODE='BE01'
ID_EXPANSION='4760'
EXPECTED_PRODUCTS=210
EXPECTED_CANONICAL=205
EXPECTED_TOTAL=205
EXPECTED_OUTSIDE_IDPRODUCTS=('711248','711275','711277','711292','711392')
STABLE_IDENTITY_SHA256='0f6afeb05d84a6e7e22d13275093997e15bdbb479e90d8ffab23b290bf05470e'
IDENTITY_FIELDS=('label','set_code','idExpansion','external_product_id','idProduct','idMetacard','print_id','card_id','card_name','collector_number','canonical_rarity','canonical_variant')

def connect(readonly: bool):
    url=os.getenv('DATABASE_URL_UNPOOLED') or os.getenv('DATABASE_URL')
    if not url: raise RuntimeError('DATABASE URL required')
    c=psycopg2.connect(url,connect_timeout=30,application_name='dontripit_ygo_ocg_be01_205_v1'); c.set_session(readonly=readonly,autocommit=False); return c

def derive(cur):
    cur.execute("SELECT id FROM games WHERE slug=%s LIMIT 1",(GAME,)); gid=int(cur.fetchone()['id'])
    cur.execute("SELECT max(last_seen_at) capture FROM external_catalog_products WHERE source='cardmarket' AND game_id=%s",(gid,)); capture=cur.fetchone()['capture']
    if str(capture)!=EXPECTED_CAPTURE: raise RuntimeError({'capture_drift':str(capture)})
    cur.execute("SELECT count(*) n FROM prints p JOIN cards c ON c.id=p.card_id WHERE c.game_id=%s AND lower(coalesce(p.language,''))='ja'",(gid,)); ja=int(cur.fetchone()['n'])
    if ja!=EXPECTED_JA: raise RuntimeError({'ja_baseline_drift':ja})
    cur.execute("""SELECT e.metacard_external_id,p.card_id,count(*) evidence_links FROM external_catalog_print_links l JOIN external_catalog_products e ON e.id=l.external_product_id JOIN prints p ON p.id=l.print_id WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single' AND e.metacard_external_id IS NOT NULL AND l.link_status=ANY(%s) GROUP BY e.metacard_external_id,p.card_id""",(gid,list(ACCEPTED)))
    meta_cards=defaultdict(set); evidence=Counter()
    for r in cur.fetchall(): m=str(r['metacard_external_id']); cid=int(r['card_id']); meta_cards[m].add(cid); evidence[(m,cid)]+=int(r['evidence_links'] or 0)
    cur.execute("""SELECT e.id external_product_id,e.external_id id_product,e.expansion_external_id,e.name,e.metacard_external_id FROM external_catalog_products e WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single' AND e.last_seen_at=%s AND e.expansion_external_id IS NOT NULL ORDER BY e.expansion_external_id,e.external_id::bigint""",(gid,capture))
    all_products=defaultdict(list)
    for r in cur.fetchall(): all_products[str(r['expansion_external_id'])].append(dict(r))
    products=all_products.get(ID_EXPANSION,[])
    if len(products)!=EXPECTED_PRODUCTS: raise RuntimeError({'product_count_drift':len(products)})
    cur.execute("""SELECT p.id print_id,p.card_id,p.collector_number,p.rarity,p.variant,c.name card_name FROM prints p JOIN cards c ON c.id=p.card_id JOIN sets s ON s.id=p.set_id WHERE c.game_id=%s AND upper(coalesce(s.code,''))=%s AND lower(coalesce(p.language,''))='ja' ORDER BY p.card_id,p.id""",(gid,SET_CODE))
    prints=[dict(r) for r in cur.fetchall()]; by_card=defaultdict(list)
    for r in prints: by_card[int(r['card_id'])].append(r)
    if len(prints)!=EXPECTED_CANONICAL or len(by_card)!=EXPECTED_CANONICAL or any(len(v)!=1 for v in by_card.values()): raise RuntimeError({'canonical_surface_drift':{'physical':len(prints),'logical':len(by_card)}})
    canonical_ids=set(by_card)
    in_set=defaultdict(list); outside=[]; unresolved=[]
    for p in products:
        m=str(p.get('metacard_external_id') or ''); cards=meta_cards.get(m,set()) if m else set()
        if not m or len(cards)!=1: unresolved.append({'idProduct':str(p['id_product']),'idMetacard':m,'cards':sorted(cards)}); continue
        cid=next(iter(cards))
        if cid not in canonical_ids: outside.append({'idProduct':str(p['id_product']),'idMetacard':m,'card_id':cid,'name':str(p['name'])}); continue
        if norm(p['name'])!=norm(by_card[cid][0]['card_name']): raise RuntimeError({'name_drift':str(p['id_product'])})
        in_set[cid].append({**p,'idMetacard':m})
    if unresolved: raise RuntimeError({'unresolved_products':unresolved})
    if set(in_set)!=canonical_ids or any(len(v)!=1 for v in in_set.values()): raise RuntimeError({'in_set_bijection_drift':{'covered':len(in_set),'multiplicities':sorted(Counter(len(v) for v in in_set.values()).items())}})
    outside_ids=tuple(sorted((str(x['idProduct']) for x in outside),key=int))
    if outside_ids!=tuple(sorted(EXPECTED_OUTSIDE_IDPRODUCTS,key=int)): raise RuntimeError({'outside_surface_drift':outside_ids})
    competitors=[]
    for oexp,other in all_products.items():
        if oexp==ID_EXPANSION or len(other)<EXPECTED_CANONICAL: continue
        covered=set(); bad=False
        for p in other:
            m=str(p.get('metacard_external_id') or ''); cards=meta_cards.get(m,set()) if m else set()
            if not m or len(cards)!=1: bad=True; break
            cid=next(iter(cards))
            if cid in canonical_ids: covered.add(cid)
        if not bad and covered==canonical_ids: competitors.append(oexp)
    if competitors: raise RuntimeError({'competing_complete_logical_surface':sorted(competitors)})
    cur.execute("""SELECT l.external_product_id,l.print_id,l.mapping_method,l.confidence,l.reviewed,l.link_status,e.external_id id_product FROM external_catalog_print_links l JOIN external_catalog_products e ON e.id=l.external_product_id WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single' AND l.link_status=ANY(%s)""",(gid,list(ACCEPTED)))
    product_claims=defaultdict(list); print_claims=defaultdict(list)
    for r in cur.fetchall(): row=dict(r); product_claims[int(r['external_product_id'])].append(row); print_claims[int(r['print_id'])].append(row)
    proposal=[]; existing=[]; identities=[]
    for cid in sorted(canonical_ids):
        p=in_set[cid][0]; pr=by_card[cid][0]; eid=int(p['external_product_id']); pid=int(pr['print_id']); pclaims=product_claims.get(eid,[]); rclaims=print_claims.get(pid,[])
        if any(int(r['print_id'])!=pid for r in pclaims) or any(int(r['external_product_id'])!=eid for r in rclaims): raise RuntimeError({'accepted_identity_conflict':{'idProduct':str(p['id_product']),'print_id':pid}})
        same=[r for r in pclaims if int(r['print_id'])==pid]
        if same:
            r=same[0]
            if len(same)!=1 or len(pclaims)!=1 or len(rclaims)!=1 or str(r.get('mapping_method') or '')!=METHOD or str(r.get('confidence') or '')!='exact' or not bool(r.get('reviewed')): raise RuntimeError({'unexpected_existing_pair':str(p['id_product'])})
        elif pclaims or rclaims: raise RuntimeError({'unexpected_claim_state':str(p['id_product'])})
        ident={'label':'BE01','set_code':SET_CODE,'idExpansion':ID_EXPANSION,'external_product_id':eid,'idProduct':str(p['id_product']),'idMetacard':str(p['idMetacard']),'print_id':pid,'card_id':cid,'card_name':str(pr['card_name']),'collector_number':str(pr['collector_number']),'canonical_rarity':pr.get('rarity'),'canonical_variant':pr.get('variant')}
        identities.append(ident); row={**ident,'metacard_evidence_links':int(evidence.get((str(p['idMetacard']),cid),0)),'already_same':bool(same)}; (existing if same else proposal).append(row)
    stable=hashlib.sha256(json.dumps([{k:x.get(k) for k in IDENTITY_FIELDS} for x in identities],sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()
    if stable!=STABLE_IDENTITY_SHA256: raise RuntimeError({'stable_identity_hash_drift':stable})
    if len(identities)!=EXPECTED_TOTAL or len({x['external_product_id'] for x in identities})!=EXPECTED_TOTAL or len({x['print_id'] for x in identities})!=EXPECTED_TOTAL: raise RuntimeError('global_bijection_failed')
    if (len(existing),len(proposal)) not in ((0,EXPECTED_TOTAL),(EXPECTED_TOTAL,0)): raise RuntimeError({'partial_state':{'existing':len(existing),'new':len(proposal)}})
    if not all(x['metacard_evidence_links']>0 for x in existing+proposal): raise RuntimeError('pair_without_metacard_evidence')
    return {'gid':gid,'capture':capture,'ja':ja,'outside':outside,'proposal':proposal,'existing':existing,'stable':stable}

def run(apply=False,confirm=''):
    if apply and confirm!=CONFIRM: raise RuntimeError(f'--apply requires --confirm {CONFIRM}')
    c=connect(not apply)
    try:
        with c.cursor(cursor_factory=RealDictCursor) as cur:
            s=derive(cur); report={'status':'pass','mode':'apply' if apply else 'dry_run','production_writes':0,'mapping_method':METHOD,'cardmarket_capture':str(s['capture']),'ja_baseline':s['ja'],'stable_identity_sha256':s['stable'],'certified_pairs':EXPECTED_TOTAL,'already_accepted_same_pair':len(s['existing']),'new_links_ready':len(s['proposal']),'outside_products':s['outside']}
            if not apply: c.rollback(); return report
            writes=0
            for x in s['proposal']:
                ev={'source':'current_cardmarket_complete_logical_subset_bijection+accepted_metacard_bridge','identity_basis':['pinned_current_capture','exact_BE01_canonical_205','complete_in_set_logical_coverage','one_product_per_BE01_card','one_JA_print_per_BE01_card','five_foreign_products_explicitly_frozen','no_competing_complete_logical_surface','stable_identity_hash'],'stable_identity_sha256':STABLE_IDENTITY_SHA256,'idExpansion':ID_EXPANSION,'canonical_set':SET_CODE,'idProduct':x['idProduct'],'idMetacard':x['idMetacard'],'collector_number':x['collector_number'],'canonical_rarity':x['canonical_rarity'],'canonical_variant':x['canonical_variant'],'metacard_evidence_links_at_write':x['metacard_evidence_links']}
                cur.execute("""INSERT INTO external_catalog_print_links(external_product_id,print_id,mapping_method,confidence,link_status,reviewed,evidence) VALUES(%s,%s,%s,'exact','accepted',true,%s) ON CONFLICT(external_product_id,print_id) DO NOTHING""",(x['external_product_id'],x['print_id'],METHOD,Json(ev)))
                if cur.rowcount!=1: raise RuntimeError({'insert_failed':x['idProduct']})
                writes+=1
            report['production_writes']=writes; c.commit(); return report
    except Exception: c.rollback(); raise
    finally: c.close()

def main():
    p=argparse.ArgumentParser(); p.add_argument('--apply',action='store_true'); p.add_argument('--confirm',default=''); p.add_argument('--report',type=Path,default=Path('/tmp/ygo-ocg-be01-205-v1.json')); a=p.parse_args(); payload=run(a.apply,a.confirm); a.report.parent.mkdir(parents=True,exist_ok=True); text=json.dumps(payload,ensure_ascii=False,indent=2,sort_keys=True,default=str)+'\n'; a.report.write_text(text,encoding='utf-8'); print(text,end=''); return 0

if __name__=='__main__': raise SystemExit(main())
