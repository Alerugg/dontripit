from __future__ import annotations

import hashlib, json, os, unicodedata
from collections import Counter, defaultdict
from pathlib import Path

import psycopg2
from psycopg2.extras import RealDictCursor

GAME='yugioh'
ACCEPTED=('accepted','mapped','exact')
EXPECTED_JA=36426
EXPECTED_CAPTURE='2026-08-18 21:09:20.740891+00:00'
TARGETS=[
 {'code':'DP24','exp':'3334','products':58,'physical':58,'logical':46},
 {'code':'DC01','exp':'4706','products':67,'physical':67,'logical':30},
 {'code':'GS02','exp':'4785','products':40,'physical':40,'logical':20},
 {'code':'GS03','exp':'4766','products':40,'physical':40,'logical':20},
 {'code':'GS04','exp':'4746','products':40,'physical':40,'logical':20},
 {'code':'TBC1','exp':'5543','products':15,'physical':15,'logical':14},
 {'code':'STSP','exp':'6485','products':20,'physical':20,'logical':10},
 {'code':'SD4','exp':'1017','products':32,'physical':32,'logical':32},
 {'code':'YU','exp':'4918','products':48,'physical':47,'logical':47},
 {'code':'KC01','exp':'5680','products':63,'physical':60,'logical':60},
 {'code':'KA','exp':'4911','products':50,'physical':47,'logical':47},
 {'code':'DB12','exp':'4751','products':45,'physical':43,'logical':43},
 {'code':'SD33','exp':'4631','products':42,'physical':41,'logical':41},
 {'code':'SD1','exp':'4872','products':28,'physical':27,'logical':27},
 {'code':'SD46','exp':'5401','products':55,'physical':54,'logical':49},
 {'code':'301','exp':'4908','products':61,'physical':60,'logical':55},
 {'code':'BOSH','exp':'1685','products':105,'physical':103,'logical':80},
 {'code':'CYHO','exp':'4620','products':119,'physical':118,'logical':79},
 {'code':'DUEA','exp':'4707','products':97,'physical':96,'logical':88},
 {'code':'SPFE','exp':'4654','products':75,'physical':74,'logical':44},
 {'code':'23PP','exp':'5222','products':125,'physical':124,'logical':32},
 {'code':'DP18','exp':'4642','products':51,'physical':50,'logical':50},
 {'code':'SD09','exp':'1078','products':37,'physical':36,'logical':36},
 {'code':'SD6','exp':'1061','products':36,'physical':35,'logical':35},
 {'code':'WPP1','exp':'4547','products':97,'physical':94,'logical':77},
 {'code':'LGB1','exp':'4566','products':63,'physical':61,'logical':49},
 {'code':'DP28','exp':'5361','products':60,'physical':58,'logical':46},
 {'code':'SD37','exp':'4567','products':54,'physical':52,'logical':48},
 {'code':'RC02','exp':'4626','products':141,'physical':138,'logical':50},
 {'code':'19PP','exp':'4589','products':60,'physical':57,'logical':19},
 {'code':'GP16','exp':'4668','products':40,'physical':38,'logical':19},
]

def norm(v):
    t=unicodedata.normalize('NFKD',str(v or '')).casefold(); return ''.join(ch for ch in t if ch.isalnum())

def main()->int:
    url=os.getenv('DATABASE_URL_UNPOOLED') or os.getenv('DATABASE_URL')
    if not url: raise RuntimeError('DATABASE URL required')
    c=psycopg2.connect(url,connect_timeout=30,application_name='dontripit_ygo_ocg_untouched_near_exact_v1'); c.set_session(readonly=True,autocommit=False)
    try:
      with c.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT id FROM games WHERE slug=%s LIMIT 1",(GAME,)); gid=int(cur.fetchone()['id'])
        cur.execute("SELECT max(last_seen_at) capture FROM external_catalog_products WHERE source='cardmarket' AND game_id=%s",(gid,)); capture=cur.fetchone()['capture']
        if str(capture)!=EXPECTED_CAPTURE: raise RuntimeError({'capture_drift':str(capture)})
        cur.execute("SELECT count(*) n FROM prints p JOIN cards c ON c.id=p.card_id WHERE c.game_id=%s AND lower(coalesce(p.language,''))='ja'",(gid,)); ja=int(cur.fetchone()['n'])
        if ja!=EXPECTED_JA: raise RuntimeError({'ja_baseline_drift':ja})

        cur.execute("""SELECT e.metacard_external_id,p.card_id,count(*) evidence_links FROM external_catalog_print_links l
          JOIN external_catalog_products e ON e.id=l.external_product_id JOIN prints p ON p.id=l.print_id
          WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single' AND e.metacard_external_id IS NOT NULL
            AND l.link_status=ANY(%s) GROUP BY e.metacard_external_id,p.card_id""",(gid,list(ACCEPTED)))
        meta=defaultdict(set); ev=Counter()
        for r in cur.fetchall(): m=str(r['metacard_external_id']); cid=int(r['card_id']); meta[m].add(cid); ev[(m,cid)]+=int(r['evidence_links'] or 0)

        cur.execute("""SELECT e.id external_product_id,e.external_id id_product,e.expansion_external_id,e.name,e.metacard_external_id
          FROM external_catalog_products e WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single'
            AND e.last_seen_at=%s AND e.expansion_external_id IS NOT NULL ORDER BY e.expansion_external_id,e.external_id::bigint""",(gid,capture))
        allp=defaultdict(list)
        for r in cur.fetchall(): allp[str(r['expansion_external_id'])].append(dict(r))

        cur.execute("""SELECT l.external_product_id,l.print_id,e.external_id id_product,e.expansion_external_id,l.mapping_method,l.confidence,l.reviewed
          FROM external_catalog_print_links l JOIN external_catalog_products e ON e.id=l.external_product_id
          WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single' AND l.link_status=ANY(%s)""",(gid,list(ACCEPTED)))
        pclaims=defaultdict(list); rclaims=defaultdict(list)
        for r in cur.fetchall(): row=dict(r); pclaims[int(r['external_product_id'])].append(row); rclaims[int(r['print_id'])].append(row)

        certified=[]; rejected=[]; proposal=[]; residual=[]
        for t in TARGETS:
          code=t['code']; exp=t['exp']; products=allp.get(exp,[])
          cur.execute("""SELECT p.id print_id,p.card_id,p.collector_number,p.rarity,p.variant,c.name card_name
            FROM prints p JOIN cards c ON c.id=p.card_id JOIN sets s ON s.id=p.set_id
            WHERE c.game_id=%s AND upper(coalesce(s.code,''))=%s AND lower(coalesce(p.language,''))='ja' ORDER BY p.card_id,p.id""",(gid,code))
          prints=[dict(r) for r in cur.fetchall()]; bycard=defaultdict(list)
          for r in prints: bycard[int(r['card_id'])].append(r)
          reasons=[]
          if len(products)!=t['products']: reasons.append(f'product_count_{len(products)}_expected_{t["products"]}')
          if len(prints)!=t['physical']: reasons.append(f'physical_{len(prints)}_expected_{t["physical"]}')
          if len(bycard)!=t['logical']: reasons.append(f'logical_{len(bycard)}_expected_{t["logical"]}')
          canonical=Counter(int(r['card_id']) for r in prints); canonical_ids=set(canonical)
          canonical_names=Counter(norm(r['card_name']) for r in prints)

          groups=defaultdict(list); outside=[]; unresolved=[]; in_names=Counter()
          for p in products:
            m=str(p.get('metacard_external_id') or ''); cards=meta.get(m,set()) if m else set()
            if not m or len(cards)!=1:
              unresolved.append({'idProduct':str(p['id_product']),'idMetacard':m,'cards':sorted(cards)}); continue
            cid=next(iter(cards))
            if cid not in canonical_ids:
              outside.append({'idProduct':str(p['id_product']),'idMetacard':m,'card_id':cid,'name':str(p['name'])}); continue
            if norm(p['name'])!=norm(bycard[cid][0]['card_name']): reasons.append(f'name_mismatch_{p["id_product"]}')
            groups[cid].append({**p,'idMetacard':m}); in_names[norm(p['name'])]+=1
          in_counter=Counter({cid:len(g) for cid,g in groups.items()})
          if unresolved: reasons.append(f'unresolved_products_{len(unresolved)}')
          if in_counter!=canonical: reasons.append('in_set_physical_multiset_mismatch')
          if in_names!=canonical_names: reasons.append('in_set_name_multiset_mismatch')

          competitors=[]
          for oexp,other in allp.items():
            if oexp==exp or len(other)<t['physical']: continue
            oc=Counter(); onames=Counter(); ounresolved=0
            for p in other:
              m=str(p.get('metacard_external_id') or ''); cards=meta.get(m,set()) if m else set()
              if not m or len(cards)!=1: ounresolved+=1; continue
              cid=next(iter(cards))
              if cid in canonical_ids: oc[cid]+=1; onames[norm(p['name'])]+=1
            if oc==canonical and onames==canonical_names:
              competitors.append({'idExpansion':oexp,'products':len(other),'unresolved_products':ounresolved,'outside_or_extra_products':len(other)-sum(oc.values())})
          if competitors: reasons.append('competing_complete_in_set_surface')

          claim_products=sum(bool(pclaims.get(int(p['external_product_id']))) for g in groups.values() for p in g)
          claim_prints=sum(bool(rclaims.get(int(pr['print_id']))) for pr in prints)
          if claim_products or claim_prints: reasons.append(f'existing_claims_products_{claim_products}_prints_{claim_prints}')

          singles=[]; variants=[]
          if not reasons:
            for cid in sorted(canonical_ids):
              gp=groups[cid]; cp=bycard[cid]
              if len(gp)==1 and len(cp)==1:
                p=gp[0]; pr=cp[0]
                singles.append({'set_code':code,'idExpansion':exp,'external_product_id':int(p['external_product_id']),'idProduct':str(p['id_product']),'idMetacard':str(p['idMetacard']),'print_id':int(pr['print_id']),'card_id':cid,'card_name':str(pr['card_name']),'collector_number':str(pr['collector_number']),'canonical_rarity':pr.get('rarity'),'canonical_variant':pr.get('variant'),'metacard_evidence_links':int(ev.get((str(p['idMetacard']),cid),0))})
              else:
                variants.append({'set_code':code,'idExpansion':exp,'card_id':cid,'card_name':str(cp[0]['card_name']),'product_count':len(gp),'print_count':len(cp),'idProducts':[str(x['id_product']) for x in sorted(gp,key=lambda x:int(x['id_product']))],'prints':[{'print_id':int(x['print_id']),'collector_number':str(x['collector_number']),'rarity':x.get('rarity'),'variant':x.get('variant')} for x in cp]})
            certified.append({'set_code':code,'idExpansion':exp,'products':len(products),'canonical_physical':len(prints),'logical':len(bycard),'outside_products':outside,'singleton_pairs':len(singles),'variant_groups':len(variants),'variant_physical':sum(x['product_count'] for x in variants)})
            proposal.extend(singles); residual.extend(variants)
          else:
            rejected.append({'set_code':code,'idExpansion':exp,'products':len(products),'canonical_physical':len(prints),'logical':len(bycard),'outside_products':outside,'unresolved_products':unresolved[:20],'competitors':competitors,'reasons':sorted(set(reasons))})
        c.rollback()
    finally: c.close()

    if len({x['external_product_id'] for x in proposal})!=len(proposal) or len({x['print_id'] for x in proposal})!=len(proposal): raise RuntimeError('global singleton proposal collision')
    if not all(x['metacard_evidence_links']>0 for x in proposal): raise RuntimeError('singleton without accepted metacard evidence')
    identity=[{k:v for k,v in x.items() if k!='metacard_evidence_links'} for x in proposal]
    fp=hashlib.sha256(json.dumps(identity,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()
    report={'status':'pass','mode':'read_only','production_writes':0,'cardmarket_capture':str(capture),'ja_baseline':ja,'screened_targets':len(TARGETS),'certified_targets':len(certified),'rejected_targets':len(rejected),'certified_singleton_pairs':len(proposal),'stable_singleton_identity_sha256':fp,'certified_variant_groups':len(residual),'certified_variant_physical':sum(x['product_count'] for x in residual),'certified':certified,'rejected':rejected,'proposal':proposal,'variant_residual_groups':residual}
    out=Path(os.getenv('YGO_OCG_UNTOUCHED_NEAR_EXACT_OUTPUT','/tmp/ygo-ocg-untouched-near-exact-v1.json')); out.parent.mkdir(parents=True,exist_ok=True); out.write_text(json.dumps(report,ensure_ascii=False,indent=2,sort_keys=True,default=str)+'\n'); print(json.dumps(report,ensure_ascii=False,indent=2,sort_keys=True,default=str)); return 0

if __name__=='__main__': raise SystemExit(main())
