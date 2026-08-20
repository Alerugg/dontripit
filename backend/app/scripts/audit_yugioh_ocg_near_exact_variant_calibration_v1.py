from __future__ import annotations

import hashlib, json, os
from collections import Counter, defaultdict
from pathlib import Path

import psycopg2
from psycopg2.extras import RealDictCursor

from app.scripts.audit_yugioh_ocg_variant_ordinal_calibration_v1 import rarity, signature, seqkey
from app.scripts.apply_yugioh_ocg_singleton_heavy_cohort_v1 import EXPECTED_CAPTURE, EXPECTED_JA, norm

GAME='yugioh'
ACCEPTED=('accepted','mapped','exact')
MIN_GROUP_SUPPORT=2
TARGETS={
 'DC01':'4706','GS02':'4785','GS03':'4766','GS04':'4746','STSP':'6485',
 'YU':'4918','SD33':'4631','301':'4908','CYHO':'4620','SPFE':'4654',
 'LGB1':'4566','SD37':'4567','RC02':'4626','19PP':'4589','GP16':'4668',
}

def main()->int:
    url=os.getenv('DATABASE_URL_UNPOOLED') or os.getenv('DATABASE_URL')
    if not url: raise RuntimeError('DATABASE URL required')
    conn=psycopg2.connect(url,connect_timeout=30,application_name='dontripit_ygo_ocg_near_exact_variant_calibration_v1'); conn.set_session(readonly=True,autocommit=False)
    try:
      with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT id FROM games WHERE slug=%s LIMIT 1",(GAME,)); gid=int(cur.fetchone()['id'])
        cur.execute("SELECT max(last_seen_at) capture FROM external_catalog_products WHERE source='cardmarket' AND game_id=%s",(gid,)); capture=cur.fetchone()['capture']
        if str(capture)!=EXPECTED_CAPTURE: raise RuntimeError({'capture_drift':str(capture)})
        cur.execute("SELECT count(*) n FROM prints p JOIN cards c ON c.id=p.card_id WHERE c.game_id=%s AND lower(coalesce(p.language,''))='ja'",(gid,)); ja=int(cur.fetchone()['n'])
        if ja!=EXPECTED_JA: raise RuntimeError({'ja_baseline_drift':ja})

        cur.execute("""SELECT e.id external_product_id,e.external_id id_product,e.expansion_external_id,e.metacard_external_id,e.name
          FROM external_catalog_products e WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single'
            AND e.last_seen_at=%s AND e.expansion_external_id IS NOT NULL AND e.metacard_external_id IS NOT NULL
          ORDER BY e.expansion_external_id,e.metacard_external_id,e.external_id::bigint""",(gid,capture))
        products=[dict(r) for r in cur.fetchall()]; product_groups=defaultdict(list)
        for r in products: product_groups[(str(r['expansion_external_id']),str(r['metacard_external_id']))].append(r)

        # Calibration evidence must be independent of every mapping method whose identity relies on an ordinal assumption.
        cur.execute("""SELECT e.id external_product_id,e.external_id id_product,e.expansion_external_id,e.metacard_external_id,e.name,
               l.mapping_method,p.id print_id,p.card_id,p.rarity,p.variant,p.collector_number,c.name card_name,s.code set_code
          FROM external_catalog_print_links l JOIN external_catalog_products e ON e.id=l.external_product_id
          JOIN prints p ON p.id=l.print_id JOIN cards c ON c.id=p.card_id JOIN sets s ON s.id=p.set_id
          WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single' AND e.last_seen_at=%s
            AND e.metacard_external_id IS NOT NULL AND l.link_status=ANY(%s)
            AND lower(coalesce(p.language,''))='ja' AND l.confidence='exact' AND l.reviewed=true
            AND lower(coalesce(l.mapping_method,'')) NOT LIKE '%%ordinal%%'
          ORDER BY e.expansion_external_id,e.metacard_external_id,e.external_id::bigint""",(gid,capture,list(ACCEPTED)))
        independent=[dict(r) for r in cur.fetchall()]; independent_by_group=defaultdict(list)
        for r in independent: independent_by_group[(str(r['expansion_external_id']),str(r['metacard_external_id']))].append(r)

        calibration=defaultdict(lambda:{'sequences':Counter(),'sets':defaultdict(set),'methods':defaultdict(Counter),'examples':defaultdict(list)})
        calibration_groups=0
        for key,gp in product_groups.items():
          if len(gp)<=1: continue
          rows=independent_by_group.get(key,[])
          if len(rows)!=len(gp) or len({int(r['external_product_id']) for r in rows})!=len(gp) or len({int(r['print_id']) for r in rows})!=len(gp): continue
          if len({int(r['card_id']) for r in rows})!=1 or len({str(r['set_code']).upper() for r in rows})!=1: continue
          if any(norm(r['name'])!=norm(r['card_name']) for r in rows): continue
          ordered=sorted(rows,key=lambda r:int(r['id_product'])); sig=signature(ordered); seq=seqkey(ordered)
          if len(sig)!=len(set(sig)): continue
          calibration[sig]['sequences'][seq]+=1; calibration[sig]['sets'][seq].add(str(ordered[0]['set_code']).upper()); calibration[sig]['methods'][seq].update(str(r['mapping_method']) for r in ordered)
          if len(calibration[sig]['examples'][seq])<8: calibration[sig]['examples'][seq].append({'set_code':str(ordered[0]['set_code']).upper(),'idExpansion':key[0],'idMetacard':key[1],'idProducts':[str(r['id_product']) for r in ordered],'sequence':list(seq),'methods':[str(r['mapping_method']) for r in ordered]})
          calibration_groups+=1

        # All accepted links are used only for identity resolution and conflict guards.
        cur.execute("""SELECT e.id external_product_id,e.external_id id_product,e.metacard_external_id,l.mapping_method,l.confidence,l.reviewed,p.id print_id,p.card_id
          FROM external_catalog_print_links l JOIN external_catalog_products e ON e.id=l.external_product_id JOIN prints p ON p.id=l.print_id
          WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single' AND l.link_status=ANY(%s)""",(gid,list(ACCEPTED)))
        meta=defaultdict(set); claimed_products=set(); claimed_prints=set()
        for r in cur.fetchall():
          claimed_products.add(int(r['external_product_id'])); claimed_prints.add(int(r['print_id']))
          if r.get('metacard_external_id') is not None: meta[str(r['metacard_external_id'])].add(int(r['card_id']))

        certifiable=[]; unresolved=[]; proposal=[]; target_groups=target_physical=0; target_counts=Counter(); cert_counts=Counter()
        for code,exp in TARGETS.items():
          cur.execute("""SELECT p.id print_id,p.card_id,p.collector_number,p.rarity,p.variant,c.name card_name FROM prints p
            JOIN cards c ON c.id=p.card_id JOIN sets s ON s.id=p.set_id
            WHERE c.game_id=%s AND upper(coalesce(s.code,''))=%s AND lower(coalesce(p.language,''))='ja' ORDER BY p.card_id,p.id""",(gid,code))
          bycard=defaultdict(list)
          for r in cur.fetchall(): row=dict(r); bycard[int(row['card_id'])].append(row)
          for (gexp,m),gp in product_groups.items():
            if gexp!=exp or len(gp)<=1: continue
            cards=meta.get(m,set())
            if len(cards)!=1: continue
            cid=next(iter(cards)); cp=bycard.get(cid,[])
            if len(cp)!=len(gp): continue
            if any(int(p['external_product_id']) in claimed_products for p in gp): continue
            if any(int(pr['print_id']) in claimed_prints for pr in cp): continue
            if any(norm(p['name'])!=norm(cp[0]['card_name']) for p in gp): raise RuntimeError({'target_name_drift':code,'idMetacard':m})
            target_groups+=1; target_physical+=len(gp); target_counts[code]+=len(gp)
            sig=signature(cp); cal=calibration.get(sig); seqs=[] if not cal else list(cal['sequences'].items()); valid=[(seq,n) for seq,n in seqs if n>=MIN_GROUP_SUPPORT]
            if len(sig)!=len(set(sig)):
              unresolved.append({'set_code':code,'idExpansion':exp,'idMetacard':m,'card_id':cid,'card_name':str(cp[0]['card_name']),'idProducts':[str(p['id_product']) for p in sorted(gp,key=lambda x:int(x['id_product']))],'rarity_signature':list(sig),'reason':'canonical_rarity_not_bijective'}); continue
            if len(valid)!=1 or (cal and len(cal['sequences'])!=1):
              unresolved.append({'set_code':code,'idExpansion':exp,'idMetacard':m,'card_id':cid,'card_name':str(cp[0]['card_name']),'idProducts':[str(p['id_product']) for p in sorted(gp,key=lambda x:int(x['id_product']))],'rarity_signature':list(sig),'observed_sequences':[] if not cal else [{'sequence':list(seq),'support_groups':int(n),'support_sets':sorted(cal['sets'][seq]),'methods':dict(cal['methods'][seq])} for seq,n in cal['sequences'].items()]}); continue
            seq,support=valid[0]; byrar=defaultdict(list)
            for pr in cp: byrar[rarity(pr['rarity'])].append(pr)
            if any(len(byrar[r])!=1 for r in seq):
              unresolved.append({'set_code':code,'idExpansion':exp,'idMetacard':m,'card_id':cid,'card_name':str(cp[0]['card_name']),'rarity_signature':list(sig),'reason':'calibrated_rarity_not_bijective'}); continue
            ordered=sorted(gp,key=lambda x:int(x['id_product'])); pairs=[]
            for ordinal,(prod,rar) in enumerate(zip(ordered,seq),1):
              pr=byrar[rar][0]
              row={'set_code':code,'idExpansion':exp,'idMetacard':m,'card_id':cid,'card_name':str(pr['card_name']),'external_product_id':int(prod['external_product_id']),'idProduct':str(prod['id_product']),'product_ordinal':ordinal,'calibrated_rarity':rar,'print_id':int(pr['print_id']),'collector_number':str(pr['collector_number']),'canonical_rarity':str(pr['rarity']),'canonical_variant':str(pr.get('variant') or ''),'rarity_signature':list(sig),'calibration_support_groups':int(support),'calibration_support_sets':sorted(cal['sets'][seq]),'calibration_methods':dict(cal['methods'][seq])}
              pairs.append(row); proposal.append(row); cert_counts[code]+=1
            certifiable.append({'set_code':code,'idExpansion':exp,'idMetacard':m,'card_id':cid,'card_name':str(cp[0]['card_name']),'rarity_signature':list(sig),'sequence':list(seq),'support_groups':int(support),'support_sets':sorted(cal['sets'][seq]),'pairs':pairs})
        conn.rollback()
    finally: conn.close()

    if len({x['external_product_id'] for x in proposal})!=len(proposal) or len({x['print_id'] for x in proposal})!=len(proposal): raise RuntimeError('calibrated proposal is not globally one-to-one')
    identity_fields=('set_code','idExpansion','idMetacard','card_id','card_name','external_product_id','idProduct','product_ordinal','calibrated_rarity','print_id','collector_number','canonical_rarity','canonical_variant','rarity_signature')
    stable=hashlib.sha256(json.dumps([{k:x.get(k) for k in identity_fields} for x in proposal],sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()
    cal_summary=[]
    for sig,cal in sorted(calibration.items(),key=lambda kv:(len(kv[0]),kv[0])):
      cal_summary.append({'rarity_signature':list(sig),'sequences':[{'sequence':list(seq),'support_groups':int(n),'support_sets':sorted(cal['sets'][seq]),'methods':dict(cal['methods'][seq]),'examples':cal['examples'][seq]} for seq,n in cal['sequences'].items()]})
    payload={'status':'pass','mode':'read_only','production_writes':0,'cardmarket_capture':str(capture),'ja_baseline':ja,'minimum_group_support':MIN_GROUP_SUPPORT,'ordinal_methods_excluded_from_calibration':True,'independent_calibration_groups':calibration_groups,'target_variant_groups':target_groups,'target_variant_physical':target_physical,'target_physical_by_set':dict(target_counts),'certifiable_groups':len(certifiable),'certifiable_pairs':len(proposal),'certifiable_pairs_by_set':dict(cert_counts),'unresolved_groups':len(unresolved),'stable_identity_sha256':stable,'calibration':cal_summary,'certifiable':certifiable,'unresolved':unresolved,'proposal':proposal}
    out=Path(os.getenv('YGO_OCG_NEAR_EXACT_VARIANT_CALIBRATION_OUTPUT','/tmp/ygo-ocg-near-exact-variant-calibration-v1.json')); out.parent.mkdir(parents=True,exist_ok=True); out.write_text(json.dumps(payload,ensure_ascii=False,indent=2,sort_keys=True,default=str)+'\n'); print(json.dumps(payload,ensure_ascii=False,indent=2,sort_keys=True,default=str)); return 0

if __name__=='__main__': raise SystemExit(main())
