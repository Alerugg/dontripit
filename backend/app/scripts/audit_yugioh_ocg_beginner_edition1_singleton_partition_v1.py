from __future__ import annotations

import hashlib, json, os
from collections import Counter, defaultdict
from pathlib import Path

import psycopg2
from psycopg2.extras import RealDictCursor

from app.scripts.audit_yugioh_ocg_full_bijection_cohort_v1 import norm

GAME='yugioh'
ACCEPTED=('accepted','mapped','exact')
EXPECTED_JA=36426
EXPECTED_CAPTURE='2026-08-18 21:09:20.740891+00:00'
TARGETS=[
    {'label':'BE01','set_code':'BE01','idExpansion':'4760','products':210,'canonical':205},
    {'label':'BE1-1014','set_code':'BE1','idExpansion':'1014','products':250,'canonical':246},
    {'label':'BE1-4879','set_code':'BE1','idExpansion':'4879','products':250,'canonical':246},
]

def main()->int:
    url=os.getenv('DATABASE_URL_UNPOOLED') or os.getenv('DATABASE_URL')
    if not url: raise RuntimeError('DATABASE URL required')
    conn=psycopg2.connect(url,connect_timeout=30,application_name='dontripit_ygo_ocg_beginner_edition1_partition_v1')
    conn.set_session(readonly=True,autocommit=False)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT id FROM games WHERE slug=%s LIMIT 1",(GAME,)); gid=int(cur.fetchone()['id'])
            cur.execute("SELECT max(last_seen_at) capture FROM external_catalog_products WHERE source='cardmarket' AND game_id=%s",(gid,)); capture=cur.fetchone()['capture']
            if str(capture)!=EXPECTED_CAPTURE: raise RuntimeError({'capture_drift':str(capture)})
            cur.execute("SELECT count(*) n FROM prints p JOIN cards c ON c.id=p.card_id WHERE c.game_id=%s AND lower(coalesce(p.language,''))='ja'",(gid,)); ja=int(cur.fetchone()['n'])
            if ja!=EXPECTED_JA: raise RuntimeError({'ja_baseline_drift':ja})

            cur.execute("""SELECT e.metacard_external_id,p.card_id,count(*) evidence_links
                FROM external_catalog_print_links l
                JOIN external_catalog_products e ON e.id=l.external_product_id
                JOIN prints p ON p.id=l.print_id
                WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single'
                  AND e.metacard_external_id IS NOT NULL AND l.link_status=ANY(%s)
                GROUP BY e.metacard_external_id,p.card_id""",(gid,list(ACCEPTED)))
            meta_cards=defaultdict(set); evidence=Counter()
            for r in cur.fetchall():
                m=str(r['metacard_external_id']); cid=int(r['card_id'])
                meta_cards[m].add(cid); evidence[(m,cid)]+=int(r['evidence_links'] or 0)

            cur.execute("""SELECT l.external_product_id,l.print_id,e.external_id id_product
                FROM external_catalog_print_links l
                JOIN external_catalog_products e ON e.id=l.external_product_id
                WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single'
                  AND l.link_status=ANY(%s)""",(gid,list(ACCEPTED)))
            product_claims=defaultdict(list); print_claims=defaultdict(list)
            for r in cur.fetchall():
                row=dict(r); product_claims[int(r['external_product_id'])].append(row); print_claims[int(r['print_id'])].append(row)

            cur.execute("""SELECT e.id external_product_id,e.external_id id_product,e.expansion_external_id,e.name,e.metacard_external_id
                FROM external_catalog_products e
                WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single'
                  AND e.last_seen_at=%s AND e.expansion_external_id IS NOT NULL
                ORDER BY e.expansion_external_id,e.external_id::bigint""",(gid,capture))
            all_products=defaultdict(list)
            for r in cur.fetchall(): all_products[str(r['expansion_external_id'])].append(dict(r))

            surfaces=[]; global_proposal=[]
            for t in TARGETS:
                code=t['set_code']; exp=t['idExpansion']
                products=all_products.get(exp,[])
                if len(products)!=t['products']: raise RuntimeError({'product_count_drift':t['label'],'got':len(products),'expected':t['products']})
                cur.execute("""SELECT p.id print_id,p.card_id,p.collector_number,p.rarity,p.variant,c.name card_name
                    FROM prints p JOIN cards c ON c.id=p.card_id JOIN sets s ON s.id=p.set_id
                    WHERE c.game_id=%s AND upper(coalesce(s.code,''))=%s AND lower(coalesce(p.language,''))='ja'
                    ORDER BY p.card_id,p.id""",(gid,code))
                prints=[dict(r) for r in cur.fetchall()]
                by_card=defaultdict(list)
                for r in prints: by_card[int(r['card_id'])].append(r)
                if len(prints)!=t['canonical'] or len(by_card)!=t['canonical'] or any(len(v)!=1 for v in by_card.values()):
                    raise RuntimeError({'canonical_surface_drift':t['label'],'physical':len(prints),'logical':len(by_card),'expected':t['canonical']})
                canonical_ids=set(by_card)

                pg=defaultdict(list)
                for p in products:
                    m=str(p.get('metacard_external_id') or '')
                    cards=meta_cards.get(m,set()) if m else set()
                    if not m or len(cards)!=1: raise RuntimeError({'metacard_resolution':t['label'],'idProduct':str(p['id_product']),'cards':sorted(cards)})
                    cid=next(iter(cards))
                    if cid not in canonical_ids: raise RuntimeError({'resolved_outside_set':t['label'],'idProduct':str(p['id_product']),'card_id':cid})
                    if norm(p['name'])!=norm(by_card[cid][0]['card_name']): raise RuntimeError({'name_drift':t['label'],'idProduct':str(p['id_product'])})
                    pg[cid].append({**p,'idMetacard':m})
                if set(pg)!=canonical_ids or len(pg)!=t['canonical']:
                    raise RuntimeError({'logical_coverage_failed':t['label'],'covered':len(pg),'canonical':t['canonical']})

                competitors=[]
                for oexp,other in all_products.items():
                    if oexp==exp or len(other)<t['canonical']: continue
                    oc=Counter(); ok=True
                    for p in other:
                        m=str(p.get('metacard_external_id') or ''); cards=meta_cards.get(m,set()) if m else set()
                        if not m or len(cards)!=1: ok=False; break
                        cid=next(iter(cards))
                        if cid not in canonical_ids: ok=False; break
                        oc[cid]+=1
                    if ok and set(oc)==canonical_ids and len(oc)==t['canonical']:
                        competitors.append({'idExpansion':oexp,'products':len(other),'extra_products':len(other)-t['canonical']})

                proposal=[]; residual=[]
                for cid in sorted(canonical_ids):
                    group=pg[cid]; pr=by_card[cid][0]
                    blockers=[]
                    if len(group)!=1: blockers.append(f'product_multiplicity_{len(group)}')
                    if print_claims.get(int(pr['print_id'])): blockers.append(f'print_claims_{len(print_claims[int(pr["print_id"])])}')
                    for p in group:
                        if product_claims.get(int(p['external_product_id'])): blockers.append(f'product_{p["id_product"]}_claims_{len(product_claims[int(p["external_product_id"])])}')
                    if len(group)==1 and not blockers and not competitors:
                        p=group[0]
                        proposal.append({'label':t['label'],'set_code':code,'idExpansion':exp,'external_product_id':int(p['external_product_id']),'idProduct':str(p['id_product']),'idMetacard':str(p['idMetacard']),'print_id':int(pr['print_id']),'card_id':cid,'card_name':str(pr['card_name']),'collector_number':str(pr['collector_number']),'canonical_rarity':pr.get('rarity'),'canonical_variant':pr.get('variant'),'metacard_evidence_links':int(evidence.get((str(p['idMetacard']),cid),0))})
                    else:
                        residual.append({'card_id':cid,'card_name':str(pr['card_name']),'collector_number':str(pr['collector_number']),'print_id':int(pr['print_id']),'idProducts':[str(p['id_product']) for p in group],'product_count':len(group),'blockers':sorted(set(blockers))})

                surface={'label':t['label'],'set_code':code,'idExpansion':exp,'products':len(products),'canonical_prints':len(prints),'canonical_cards':len(canonical_ids),'extra_products':len(products)-len(canonical_ids),'competitors':competitors,'singleton_pairs_ready':len(proposal),'residual_groups':len(residual),'residual_physical_products':sum(x['product_count'] for x in residual),'proposal':proposal,'residual':residual}
                surfaces.append(surface); global_proposal.extend(proposal)
            conn.rollback()
    finally:
        conn.close()

    if len({x['external_product_id'] for x in global_proposal})!=len(global_proposal): raise RuntimeError('proposal product collision')
    if len({x['print_id'] for x in global_proposal})!=len(global_proposal): raise RuntimeError('proposal print collision')
    if not all(x['metacard_evidence_links']>0 for x in global_proposal): raise RuntimeError('proposal without accepted metacard evidence')
    identity=[{k:v for k,v in x.items() if k!='metacard_evidence_links'} for x in global_proposal]
    fp=hashlib.sha256(json.dumps(identity,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()
    report={'status':'pass','mode':'read_only','production_writes':0,'cardmarket_capture':str(capture),'ja_baseline':ja,'screened_surfaces':len(TARGETS),'certified_singleton_pairs':len(global_proposal),'stable_singleton_identity_sha256':fp,'surfaces':surfaces}
    out=Path(os.getenv('YGO_OCG_BE1_PARTITION_OUTPUT','/tmp/ygo-ocg-be1-partition-v1.json'))
    out.parent.mkdir(parents=True,exist_ok=True); out.write_text(json.dumps(report,ensure_ascii=False,indent=2,sort_keys=True,default=str)+'\n',encoding='utf-8'); print(json.dumps(report,ensure_ascii=False,indent=2,sort_keys=True,default=str)); return 0

if __name__=='__main__': raise SystemExit(main())
