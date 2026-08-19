from __future__ import annotations

import argparse, hashlib, json, os, unicodedata
from collections import Counter, defaultdict
from pathlib import Path

import psycopg2
from psycopg2.extras import Json, RealDictCursor

GAME='yugioh'
ACCEPTED=('accepted','mapped','exact')
METHOD='cardmarket_ocg_certified_near_exact_singleton_cohort_v1'
CONFIRM='APPLY_YUGIOH_OCG_NEAR_EXACT_SINGLETON302_V1'
EXPECTED_JA=36426
EXPECTED_CAPTURE='2026-08-18 21:09:20.740891+00:00'
STABLE_IDENTITY_SHA256='61e2bfbbe68fd199e32eb0561db755c68753600c2d652dc9564c5c560f409284'
TARGETS={
 'YU':{'exp':'4918','products':48,'physical':47,'logical':47,'singletons':47,'outside':('735717',)},
 'SD33':{'exp':'4631','products':42,'physical':41,'logical':41,'singletons':41,'outside':('679706',)},
 '301':{'exp':'4908','products':61,'physical':60,'logical':55,'singletons':50,'outside':('735087',)},
 'CYHO':{'exp':'4620','products':119,'physical':118,'logical':79,'singletons':63,'outside':('676354',)},
 'SPFE':{'exp':'4654','products':75,'physical':74,'logical':44,'singletons':14,'outside':('688022',)},
 'LGB1':{'exp':'4566','products':63,'physical':61,'logical':49,'singletons':37,'outside':('661867','661869')},
 'SD37':{'exp':'4567','products':54,'physical':52,'logical':48,'singletons':44,'outside':('662009','662010')},
 'RC02':{'exp':'4626','products':141,'physical':138,'logical':50,'singletons':6,'outside':('677179','677180','677181')},
}
EXPECTED_TOTAL=sum(x['singletons'] for x in TARGETS.values())
IDENTITY_FIELDS=('set_code','idExpansion','external_product_id','idProduct','idMetacard','print_id','card_id','card_name','collector_number','canonical_rarity','canonical_variant')

def norm(v):
    t=unicodedata.normalize('NFKD',str(v or '')).casefold(); return ''.join(ch for ch in t if ch.isalnum())

def connect(readonly: bool):
    url=os.getenv('DATABASE_URL_UNPOOLED') or os.getenv('DATABASE_URL')
    if not url: raise RuntimeError('DATABASE URL required')
    c=psycopg2.connect(url,connect_timeout=30,application_name='dontripit_ygo_ocg_near_exact_singleton302_v1'); c.set_session(readonly=readonly,autocommit=False); return c

def derive(cur):
    cur.execute("SELECT id FROM games WHERE slug=%s LIMIT 1",(GAME,)); gid=int(cur.fetchone()['id'])
    cur.execute("SELECT max(last_seen_at) capture FROM external_catalog_products WHERE source='cardmarket' AND game_id=%s",(gid,)); capture=cur.fetchone()['capture']
    if str(capture)!=EXPECTED_CAPTURE: raise RuntimeError({'capture_drift':str(capture)})
    cur.execute("SELECT count(*) n FROM prints p JOIN cards c ON c.id=p.card_id WHERE c.game_id=%s AND lower(coalesce(p.language,''))='ja'",(gid,)); ja=int(cur.fetchone()['n'])
    if ja!=EXPECTED_JA: raise RuntimeError({'ja_baseline_drift':ja})

    cur.execute("""SELECT e.metacard_external_id,p.card_id,count(*) evidence_links FROM external_catalog_print_links l
      JOIN external_catalog_products e ON e.id=l.external_product_id JOIN prints p ON p.id=l.print_id
      WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single' AND e.metacard_external_id IS NOT NULL
        AND l.link_status=ANY(%s) GROUP BY e.metacard_external_id,p.card_id""",(gid,list(ACCEPTED)))
    meta=defaultdict(set); evidence=Counter()
    for r in cur.fetchall(): m=str(r['metacard_external_id']); cid=int(r['card_id']); meta[m].add(cid); evidence[(m,cid)]+=int(r['evidence_links'] or 0)

    cur.execute("""SELECT e.id external_product_id,e.external_id id_product,e.expansion_external_id,e.name,e.metacard_external_id
      FROM external_catalog_products e WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single'
        AND e.last_seen_at=%s AND e.expansion_external_id IS NOT NULL ORDER BY e.expansion_external_id,e.external_id::bigint""",(gid,capture))
    allp=defaultdict(list)
    for r in cur.fetchall(): allp[str(r['expansion_external_id'])].append(dict(r))

    cur.execute("""SELECT l.external_product_id,l.print_id,l.mapping_method,l.confidence,l.reviewed,l.link_status,e.external_id id_product
      FROM external_catalog_print_links l JOIN external_catalog_products e ON e.id=l.external_product_id
      WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single' AND l.link_status=ANY(%s)""",(gid,list(ACCEPTED)))
    pclaims=defaultdict(list); rclaims=defaultdict(list)
    for r in cur.fetchall(): row=dict(r); pclaims[int(r['external_product_id'])].append(row); rclaims[int(r['print_id'])].append(row)

    proposal=[]; existing=[]; identities=[]; reports=[]
    for code,cfg in TARGETS.items():
        exp=cfg['exp']; products=allp.get(exp,[])
        if len(products)!=cfg['products']: raise RuntimeError({'product_count_drift':code,'got':len(products),'expected':cfg['products']})
        cur.execute("""SELECT p.id print_id,p.card_id,p.collector_number,p.rarity,p.variant,c.name card_name
          FROM prints p JOIN cards c ON c.id=p.card_id JOIN sets s ON s.id=p.set_id
          WHERE c.game_id=%s AND upper(coalesce(s.code,''))=%s AND lower(coalesce(p.language,''))='ja' ORDER BY p.card_id,p.id""",(gid,code))
        prints=[dict(r) for r in cur.fetchall()]; bycard=defaultdict(list)
        for r in prints: bycard[int(r['card_id'])].append(r)
        if len(prints)!=cfg['physical'] or len(bycard)!=cfg['logical']: raise RuntimeError({'canonical_surface_drift':code,'physical':len(prints),'logical':len(bycard)})
        canonical=Counter(int(r['card_id']) for r in prints); canonical_ids=set(canonical); canonical_names=Counter(norm(r['card_name']) for r in prints)

        groups=defaultdict(list); outside=[]; unresolved=[]; in_names=Counter()
        for p in products:
            m=str(p.get('metacard_external_id') or ''); cards=meta.get(m,set()) if m else set()
            if not m or len(cards)!=1: unresolved.append(str(p['id_product'])); continue
            cid=next(iter(cards))
            if cid not in canonical_ids: outside.append(str(p['id_product'])); continue
            if norm(p['name'])!=norm(bycard[cid][0]['card_name']): raise RuntimeError({'name_drift':code,'idProduct':str(p['id_product'])})
            groups[cid].append({**p,'idMetacard':m}); in_names[norm(p['name'])]+=1
        if unresolved: raise RuntimeError({'unresolved_products':code,'ids':unresolved})
        if tuple(sorted(outside,key=int))!=tuple(sorted(cfg['outside'],key=int)): raise RuntimeError({'outside_surface_drift':code,'ids':sorted(outside,key=int)})
        if Counter({cid:len(g) for cid,g in groups.items()})!=canonical or in_names!=canonical_names: raise RuntimeError({'in_set_physical_bijection_drift':code})

        competitors=[]
        for oexp,other in allp.items():
            if oexp==exp or len(other)<cfg['physical']: continue
            oc=Counter(); onames=Counter()
            for p in other:
                m=str(p.get('metacard_external_id') or ''); cards=meta.get(m,set()) if m else set()
                if not m or len(cards)!=1: continue
                cid=next(iter(cards))
                if cid in canonical_ids: oc[cid]+=1; onames[norm(p['name'])]+=1
            if oc==canonical and onames==canonical_names: competitors.append(oexp)
        if competitors: raise RuntimeError({'competing_complete_in_set_surface':code,'competitors':sorted(competitors)})

        set_pairs=[]; ex=new=0; variant_groups=variant_physical=0
        for cid in sorted(canonical_ids):
            gp=groups[cid]; cp=bycard[cid]
            if len(gp)==1 and len(cp)==1:
                p=gp[0]; pr=cp[0]; eid=int(p['external_product_id']); pid=int(pr['print_id']); pc=pclaims.get(eid,[]); rc=rclaims.get(pid,[])
                if any(int(r['print_id'])!=pid for r in pc) or any(int(r['external_product_id'])!=eid for r in rc): raise RuntimeError({'accepted_identity_conflict':code,'idProduct':str(p['id_product']),'print_id':pid})
                same=[r for r in pc if int(r['print_id'])==pid]
                if same:
                    r=same[0]
                    if len(same)!=1 or len(pc)!=1 or len(rc)!=1 or str(r.get('mapping_method') or '')!=METHOD or str(r.get('confidence') or '')!='exact' or not bool(r.get('reviewed')): raise RuntimeError({'unexpected_existing_pair':code,'idProduct':str(p['id_product'])})
                    ex+=1
                else:
                    if pc or rc: raise RuntimeError({'unexpected_singleton_claim':code,'idProduct':str(p['id_product'])})
                    new+=1
                ident={'set_code':code,'idExpansion':exp,'external_product_id':eid,'idProduct':str(p['id_product']),'idMetacard':str(p['idMetacard']),'print_id':pid,'card_id':cid,'card_name':str(pr['card_name']),'collector_number':str(pr['collector_number']),'canonical_rarity':pr.get('rarity'),'canonical_variant':pr.get('variant')}
                row={**ident,'metacard_evidence_links':int(evidence.get((str(p['idMetacard']),cid),0)),'already_same':bool(same)}
                identities.append(ident); set_pairs.append(row); (existing if same else proposal).append(row)
            else:
                variant_groups+=1; variant_physical+=len(gp)
                for p in gp:
                    if pclaims.get(int(p['external_product_id'])): raise RuntimeError({'variant_product_claimed':code,'idProduct':str(p['id_product'])})
                for pr in cp:
                    if rclaims.get(int(pr['print_id'])): raise RuntimeError({'variant_print_claimed':code,'print_id':int(pr['print_id'])})
        if len(set_pairs)!=cfg['singletons'] or (ex,new) not in ((0,cfg['singletons']),(cfg['singletons'],0)): raise RuntimeError({'singleton_surface_drift':code,'pairs':len(set_pairs),'existing':ex,'new':new})
        reports.append({'set_code':code,'idExpansion':exp,'pairs':cfg['singletons'],'existing_same':ex,'new_ready':new,'outside_products':len(outside),'variant_groups':variant_groups,'variant_physical':variant_physical})

    stable=hashlib.sha256(json.dumps([{k:x.get(k) for k in IDENTITY_FIELDS} for x in identities],sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()
    if stable!=STABLE_IDENTITY_SHA256: raise RuntimeError({'stable_identity_hash_drift':stable})
    if len(identities)!=EXPECTED_TOTAL or len({x['external_product_id'] for x in identities})!=EXPECTED_TOTAL or len({x['print_id'] for x in identities})!=EXPECTED_TOTAL: raise RuntimeError({'global_singleton_bijection_failed':len(identities)})
    if (len(existing),len(proposal)) not in ((0,EXPECTED_TOTAL),(EXPECTED_TOTAL,0)): raise RuntimeError({'global_partial_state':{'existing':len(existing),'new':len(proposal)}})
    if not all(x['metacard_evidence_links']>0 for x in existing+proposal): raise RuntimeError('pair_without_metacard_evidence')
    return {'gid':gid,'capture':capture,'ja':ja,'proposal':proposal,'existing':existing,'sets':reports,'stable':stable}

def run(apply=False,confirm=''):
    if apply and confirm!=CONFIRM: raise RuntimeError(f'--apply requires --confirm {CONFIRM}')
    c=connect(not apply)
    try:
      with c.cursor(cursor_factory=RealDictCursor) as cur:
        s=derive(cur); report={'status':'pass','mode':'apply' if apply else 'dry_run','production_writes':0,'mapping_method':METHOD,'cardmarket_capture':str(s['capture']),'ja_baseline':s['ja'],'stable_identity_sha256':s['stable'],'certified_pairs':EXPECTED_TOTAL,'already_accepted_same_pair':len(s['existing']),'new_links_ready':len(s['proposal']),'sets':s['sets']}
        if not apply: c.rollback(); return report
        writes=0
        for x in s['proposal']:
            ev={'source':'current_cardmarket_near_exact_physical_bijection+accepted_metacard_bridge+singleton_partition','identity_basis':['pinned_current_capture','exact_in_set_physical_card_multiset','strict_name_multiset','explicit_resolved_foreign_products_frozen','no_competing_complete_in_set_surface','one_product_for_logical_card','one_JA_print_for_logical_card','variant_groups_left_unclaimed','stable_identity_hash'],'stable_identity_sha256':STABLE_IDENTITY_SHA256,'idExpansion':x['idExpansion'],'canonical_set':x['set_code'],'idProduct':x['idProduct'],'idMetacard':x['idMetacard'],'collector_number':x['collector_number'],'canonical_rarity':x['canonical_rarity'],'canonical_variant':x['canonical_variant'],'metacard_evidence_links_at_write':x['metacard_evidence_links']}
            cur.execute("""INSERT INTO external_catalog_print_links(external_product_id,print_id,mapping_method,confidence,link_status,reviewed,evidence) VALUES(%s,%s,%s,'exact','accepted',true,%s) ON CONFLICT(external_product_id,print_id) DO NOTHING""",(x['external_product_id'],x['print_id'],METHOD,Json(ev)))
            if cur.rowcount!=1: raise RuntimeError({'insert_failed':x['idProduct']})
            writes+=1
        report['production_writes']=writes; c.commit(); return report
    except Exception: c.rollback(); raise
    finally: c.close()

def main():
    p=argparse.ArgumentParser(); p.add_argument('--apply',action='store_true'); p.add_argument('--confirm',default=''); p.add_argument('--report',type=Path,default=Path('/tmp/ygo-ocg-near-exact-singleton302-v1.json')); a=p.parse_args(); payload=run(a.apply,a.confirm); a.report.parent.mkdir(parents=True,exist_ok=True); text=json.dumps(payload,ensure_ascii=False,indent=2,sort_keys=True,default=str)+'\n'; a.report.write_text(text,encoding='utf-8'); print(text,end=''); return 0

if __name__=='__main__': raise SystemExit(main())
