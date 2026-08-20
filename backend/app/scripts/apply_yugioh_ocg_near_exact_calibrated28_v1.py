from __future__ import annotations

import argparse, hashlib, json, os
from collections import Counter, defaultdict
from pathlib import Path

import psycopg2
from psycopg2.extras import Json, RealDictCursor

from app.scripts.audit_yugioh_ocg_variant_ordinal_calibration_v1 import rarity, signature, seqkey
from app.scripts.apply_yugioh_ocg_singleton_heavy_cohort_v1 import EXPECTED_CAPTURE, EXPECTED_JA, norm

GAME='yugioh'
ACCEPTED=('accepted','mapped','exact')
METHOD='cardmarket_ocg_certified_independent_ordinal_rarity_near_exact_v1'
CONFIRM='APPLY_YUGIOH_OCG_NEAR_EXACT_CALIBRATED28_V1'
STABLE_IDENTITY_SHA256='82e3d0d449477c95c63fe2a97c380b3b55915607657a2b8bb9e2e0f54c72f65d'
TARGETS={'STSP':('6485',20),'SD37':('4567',8)}
ALLOWED={
 ('prismaticsecret','ultra'):('ultra','prismaticsecret'),
 ('secret','super'):('super','secret'),
 ('secret','ultra'):('ultra','secret'),
}
EXPECTED_TOTAL=28
MIN_SUPPORT=2
FIELDS=('set_code','idExpansion','idMetacard','card_id','card_name','external_product_id','idProduct','product_ordinal','calibrated_rarity','print_id','collector_number','canonical_rarity','canonical_variant','rarity_signature')

def connect(readonly: bool):
    u=os.getenv('DATABASE_URL_UNPOOLED') or os.getenv('DATABASE_URL')
    if not u: raise RuntimeError('DATABASE URL required')
    c=psycopg2.connect(u,connect_timeout=30,application_name='dontripit_ygo_ocg_near_exact_calibrated28_v1'); c.set_session(readonly=readonly,autocommit=False); return c

def derive(cur):
    cur.execute("SELECT id FROM games WHERE slug=%s LIMIT 1",(GAME,)); gid=int(cur.fetchone()['id'])
    cur.execute("SELECT max(last_seen_at) capture FROM external_catalog_products WHERE source='cardmarket' AND game_id=%s",(gid,)); capture=cur.fetchone()['capture']
    if str(capture)!=EXPECTED_CAPTURE: raise RuntimeError({'capture_drift':str(capture)})
    cur.execute("SELECT count(*) n FROM prints p JOIN cards c ON c.id=p.card_id WHERE c.game_id=%s AND lower(coalesce(p.language,''))='ja'",(gid,)); ja=int(cur.fetchone()['n'])
    if ja!=EXPECTED_JA: raise RuntimeError({'ja_baseline_drift':ja})

    cur.execute("""SELECT e.id external_product_id,e.external_id id_product,e.expansion_external_id,e.metacard_external_id,e.name
      FROM external_catalog_products e WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single' AND e.last_seen_at=%s
        AND e.expansion_external_id IS NOT NULL AND e.metacard_external_id IS NOT NULL
      ORDER BY e.expansion_external_id,e.metacard_external_id,e.external_id::bigint""",(gid,capture))
    pg=defaultdict(list)
    for r in cur.fetchall(): row=dict(r); pg[(str(row['expansion_external_id']),str(row['metacard_external_id']))].append(row)

    # Non-circular calibration: every method containing 'ordinal' is excluded.
    cur.execute("""SELECT e.id external_product_id,e.external_id id_product,e.expansion_external_id,e.metacard_external_id,e.name,l.mapping_method,
      p.id print_id,p.card_id,p.rarity,p.variant,p.collector_number,c.name card_name,s.code set_code
      FROM external_catalog_print_links l JOIN external_catalog_products e ON e.id=l.external_product_id
      JOIN prints p ON p.id=l.print_id JOIN cards c ON c.id=p.card_id JOIN sets s ON s.id=p.set_id
      WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single' AND e.last_seen_at=%s
        AND e.metacard_external_id IS NOT NULL AND l.link_status=ANY(%s) AND lower(coalesce(p.language,''))='ja'
        AND l.confidence='exact' AND l.reviewed=true AND lower(coalesce(l.mapping_method,'')) NOT LIKE '%%ordinal%%'
      ORDER BY e.expansion_external_id,e.metacard_external_id,e.external_id::bigint""",(gid,capture,list(ACCEPTED)))
    ag=defaultdict(list)
    for r in cur.fetchall(): row=dict(r); ag[(str(row['expansion_external_id']),str(row['metacard_external_id']))].append(row)

    cal=defaultdict(Counter); calsets=defaultdict(lambda:defaultdict(set)); calmethods=defaultdict(lambda:defaultdict(Counter))
    for key,gp in pg.items():
      if len(gp)<=1: continue
      rows=ag.get(key,[])
      if len(rows)!=len(gp) or len({int(r['external_product_id']) for r in rows})!=len(gp) or len({int(r['print_id']) for r in rows})!=len(gp): continue
      if len({int(r['card_id']) for r in rows})!=1 or len({str(r['set_code']).upper() for r in rows})!=1 or any(norm(r['name'])!=norm(r['card_name']) for r in rows): continue
      ordered=sorted(rows,key=lambda r:int(r['id_product'])); sig=signature(ordered); seq=seqkey(ordered)
      if len(sig)!=len(set(sig)): continue
      cal[sig][seq]+=1; calsets[sig][seq].add(str(ordered[0]['set_code']).upper()); calmethods[sig][seq].update(str(r['mapping_method']) for r in ordered)
    for sig,seq in ALLOWED.items():
      if len(cal[sig])!=1 or cal[sig].get(seq,0)<MIN_SUPPORT: raise RuntimeError({'independent_calibration_drift':list(sig),'sequences':{str(k):v for k,v in cal[sig].items()}})

    cur.execute("""SELECT e.id external_product_id,e.external_id id_product,e.metacard_external_id,l.mapping_method,l.confidence,l.reviewed,p.id print_id,p.card_id
      FROM external_catalog_print_links l JOIN external_catalog_products e ON e.id=l.external_product_id JOIN prints p ON p.id=l.print_id
      WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single' AND l.link_status=ANY(%s)""",(gid,list(ACCEPTED)))
    meta=defaultdict(set); bp=defaultdict(list); br=defaultdict(list)
    for r in cur.fetchall():
      row=dict(r); bp[int(row['external_product_id'])].append(row); br[int(row['print_id'])].append(row)
      if row.get('metacard_external_id') is not None: meta[str(row['metacard_external_id'])].add(int(row['card_id']))

    proposal=[]; existing=[]; identities=[]; reports=[]
    for code,(exp,expected) in TARGETS.items():
      cur.execute("""SELECT p.id print_id,p.card_id,p.collector_number,p.rarity,p.variant,c.name card_name FROM prints p
        JOIN cards c ON c.id=p.card_id JOIN sets s ON s.id=p.set_id
        WHERE c.game_id=%s AND upper(coalesce(s.code,''))=%s AND lower(coalesce(p.language,''))='ja' ORDER BY p.card_id,p.id""",(gid,code))
      bycard=defaultdict(list)
      for r in cur.fetchall(): row=dict(r); bycard[int(row['card_id'])].append(row)
      pairs=[]; ex=new=0
      for (gexp,m),gproducts in pg.items():
        if gexp!=exp or len(gproducts)<=1: continue
        cards=meta.get(m,set())
        if len(cards)!=1: continue
        cid=next(iter(cards)); cprints=bycard.get(cid,[])
        if len(cprints)!=len(gproducts): continue
        sig=signature(cprints); seq=ALLOWED.get(sig)
        if not seq: continue
        if any(norm(p['name'])!=norm(cprints[0]['card_name']) for p in gproducts): raise RuntimeError({'name_drift':code,'idMetacard':m})
        byrar=defaultdict(list)
        for pr in cprints: byrar[rarity(pr['rarity'])].append(pr)
        if any(len(byrar[r])!=1 for r in seq): raise RuntimeError({'rarity_not_bijective':code,'idMetacard':m})
        for ordinal,(prod,rar) in enumerate(zip(sorted(gproducts,key=lambda x:int(x['id_product'])),seq),1):
          pr=byrar[rar][0]; eid=int(prod['external_product_id']); pid=int(pr['print_id']); pc=bp.get(eid,[]); rc=br.get(pid,[])
          if any(int(r['print_id'])!=pid for r in pc) or any(int(r['external_product_id'])!=eid for r in rc): raise RuntimeError({'accepted_identity_conflict':code,'idProduct':str(prod['id_product']),'print_id':pid})
          same=[r for r in pc if int(r['print_id'])==pid]
          if same:
            r=same[0]
            if len(same)!=1 or len(pc)!=1 or len(rc)!=1 or str(r['mapping_method'])!=METHOD or str(r['confidence'])!='exact' or not bool(r['reviewed']): raise RuntimeError({'unexpected_existing_pair':code,'idProduct':str(prod['id_product'])})
            ex+=1
          else:
            if pc or rc: raise RuntimeError({'unexpected_claim_state':code,'idProduct':str(prod['id_product'])})
            new+=1
          ident={'set_code':code,'idExpansion':exp,'idMetacard':m,'card_id':cid,'card_name':str(pr['card_name']),'external_product_id':eid,'idProduct':str(prod['id_product']),'product_ordinal':ordinal,'calibrated_rarity':rar,'print_id':pid,'collector_number':str(pr['collector_number']),'canonical_rarity':str(pr['rarity']),'canonical_variant':str(pr.get('variant') or ''),'rarity_signature':list(sig)}
          identities.append(ident); pairs.append({**ident,'already_same':bool(same),'calibration_support_groups':int(cal[sig][seq]),'calibration_support_sets':sorted(calsets[sig][seq]),'calibration_methods':dict(calmethods[sig][seq])})
      if len(pairs)!=expected or (ex,new) not in ((0,expected),(expected,0)): raise RuntimeError({'target_pair_count_drift':code,'pairs':len(pairs),'existing':ex,'new':new})
      proposal.extend(x for x in pairs if not x['already_same']); existing.extend(x for x in pairs if x['already_same']); reports.append({'set_code':code,'idExpansion':exp,'pairs':expected,'existing_same':ex,'new_ready':new})

    stable=hashlib.sha256(json.dumps([{k:x.get(k) for k in FIELDS} for x in identities],sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()
    if stable!=STABLE_IDENTITY_SHA256: raise RuntimeError({'stable_identity_hash_drift':stable})
    if len(identities)!=EXPECTED_TOTAL or len({x['external_product_id'] for x in identities})!=EXPECTED_TOTAL or len({x['print_id'] for x in identities})!=EXPECTED_TOTAL: raise RuntimeError({'global_bijection_failed':len(identities)})
    if (len(existing),len(proposal)) not in ((0,EXPECTED_TOTAL),(EXPECTED_TOTAL,0)): raise RuntimeError({'global_partial_state':{'existing':len(existing),'new':len(proposal)}})
    return {'capture':capture,'ja':ja,'proposal':proposal,'existing':existing,'sets':reports,'stable':stable}

def run(apply=False,confirm=''):
    if apply and confirm!=CONFIRM: raise RuntimeError(f'--apply requires --confirm {CONFIRM}')
    c=connect(not apply)
    try:
      with c.cursor(cursor_factory=RealDictCursor) as cur:
        s=derive(cur); report={'mode':'apply' if apply else 'dry_run','status':'pass','production_writes':0,'mapping_method':METHOD,'cardmarket_capture':str(s['capture']),'ja_baseline':s['ja'],'stable_identity_sha256':s['stable'],'certified_pairs':EXPECTED_TOTAL,'already_accepted_same_pair':len(s['existing']),'new_links_ready':len(s['proposal']),'sets':s['sets']}
        if not apply: c.rollback(); return report
        writes=0
        for x in s['proposal']:
          ev={'source':'independent_current_exact_non_ordinal_multiversion_calibration+target_exact_set_geometry','identity_basis':['pinned_current_capture','accepted_metacard_identity_bridge','strict_name_match','all_ordinal_methods_excluded_from_calibration','unique_supported_ordinal_rarity_sequence','rarity_bijective','stable_identity_hash'],'stable_identity_sha256':STABLE_IDENTITY_SHA256,'idExpansion':x['idExpansion'],'canonical_set':x['set_code'],'idProduct':x['idProduct'],'idMetacard':x['idMetacard'],'product_ordinal':x['product_ordinal'],'calibrated_rarity':x['calibrated_rarity'],'calibration_support_groups':x['calibration_support_groups'],'calibration_support_sets':x['calibration_support_sets'],'calibration_methods':x['calibration_methods'],'collector_number':x['collector_number'],'canonical_rarity':x['canonical_rarity'],'canonical_variant':x['canonical_variant']}
          cur.execute("""INSERT INTO external_catalog_print_links(external_product_id,print_id,mapping_method,confidence,link_status,reviewed,evidence) VALUES(%s,%s,%s,'exact','accepted',true,%s) ON CONFLICT(external_product_id,print_id) DO NOTHING""",(x['external_product_id'],x['print_id'],METHOD,Json(ev)))
          if cur.rowcount!=1: raise RuntimeError({'insert_failed':x['idProduct']})
          writes+=1
        report['production_writes']=writes; c.commit(); return report
    except Exception: c.rollback(); raise
    finally: c.close()

def main():
    p=argparse.ArgumentParser(); p.add_argument('--apply',action='store_true'); p.add_argument('--confirm',default=''); p.add_argument('--report',type=Path,default=Path('/tmp/ygo-ocg-near-exact-calibrated28-v1.json')); a=p.parse_args(); payload=run(a.apply,a.confirm); a.report.parent.mkdir(parents=True,exist_ok=True); text=json.dumps(payload,ensure_ascii=False,indent=2,sort_keys=True,default=str)+'\n'; a.report.write_text(text,encoding='utf-8'); print(text,end=''); return 0

if __name__=='__main__': raise SystemExit(main())
