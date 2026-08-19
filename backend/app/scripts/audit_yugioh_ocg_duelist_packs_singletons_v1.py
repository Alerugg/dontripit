from __future__ import annotations

import json
import os
from collections import Counter, defaultdict
from pathlib import Path

import psycopg2
from psycopg2.extras import RealDictCursor

from app.scripts.audit_yugioh_ocg_duelist_packs_public_code_v1 import TARGETS,evidence_sha256,norm

GAME='yugioh'
ACCEPTED=('accepted','mapped','exact')
EXPECTED_JA=36426
EXPECTED_TOTAL=219
EXPECTED={'DP23':56,'DP22':56,'DP21':56,'DP19':51}


def main()->int:
    url=os.getenv('DATABASE_URL_UNPOOLED') or os.getenv('DATABASE_URL')
    if not url: raise RuntimeError('DATABASE URL required')
    conn=psycopg2.connect(url,connect_timeout=30,application_name='dontripit_ygo_ocg_duelist_pack_singletons_v1')
    conn.set_session(readonly=True,autocommit=False)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT id FROM games WHERE slug=%s LIMIT 1",(GAME,)); gid=int(cur.fetchone()['id'])
            cur.execute("SELECT max(last_seen_at) capture FROM external_catalog_products WHERE source='cardmarket' AND game_id=%s",(gid,)); capture=cur.fetchone()['capture']
            if capture is None: raise RuntimeError('Cardmarket capture missing')
            cur.execute("""SELECT count(*) n FROM prints p JOIN cards c ON c.id=p.card_id WHERE c.game_id=%s AND lower(coalesce(p.language,''))='ja'""",(gid,)); ja=int(cur.fetchone()['n'])
            if ja!=EXPECTED_JA: raise RuntimeError({'ja_baseline_drift':ja})

            cur.execute("""SELECT e.metacard_external_id,p.card_id,count(*) evidence_links
                FROM external_catalog_print_links l JOIN external_catalog_products e ON e.id=l.external_product_id JOIN prints p ON p.id=l.print_id
                WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single' AND e.metacard_external_id IS NOT NULL AND l.link_status=ANY(%s)
                GROUP BY e.metacard_external_id,p.card_id""",(gid,list(ACCEPTED)))
            meta_cards=defaultdict(set); evidence=Counter()
            for r in cur.fetchall():
                meta=str(r['metacard_external_id']); cid=int(r['card_id']); meta_cards[meta].add(cid); evidence[(meta,cid)]+=int(r['evidence_links'] or 0)

            cur.execute("""SELECT e.external_id id_product,l.external_product_id,l.print_id,l.mapping_method,l.confidence,l.reviewed
                FROM external_catalog_print_links l JOIN external_catalog_products e ON e.id=l.external_product_id
                WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single' AND l.link_status=ANY(%s)""",(gid,list(ACCEPTED)))
            accepted=[dict(r) for r in cur.fetchall()]; by_product=defaultdict(list); by_print=defaultdict(list)
            for r in accepted:
                by_product[int(r['external_product_id'])].append(r); by_print[int(r['print_id'])].append(r)

            proposal=[]; reports=[]
            for code,cfg in TARGETS.items():
                expected=EXPECTED[code]
                cur.execute("""SELECT e.id external_product_id,e.external_id id_product,e.name,e.metacard_external_id,e.expansion_external_id,e.last_seen_at
                    FROM external_catalog_products e WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single' AND e.expansion_external_id=%s AND e.last_seen_at=%s
                    ORDER BY e.metacard_external_id,e.external_id::bigint""",(gid,cfg['idExpansion'],capture)); products=[dict(r) for r in cur.fetchall()]
                cur.execute("""SELECT p.id print_id,p.card_id,p.collector_number,p.rarity,p.variant,c.name card_name
                    FROM prints p JOIN cards c ON c.id=p.card_id JOIN sets s ON s.id=p.set_id
                    WHERE c.game_id=%s AND upper(coalesce(s.code,''))=%s AND lower(coalesce(p.language,''))='ja'
                    ORDER BY p.card_id,p.collector_number,p.id""",(gid,code)); prints=[dict(r) for r in cur.fetchall()]
                if (len(products),len(prints))!=(expected,expected): raise RuntimeError({'surface_drift':code,'products':len(products),'prints':len(prints)})
                pc=defaultdict(list)
                for pr in prints: pc[int(pr['card_id'])].append(pr)
                pairs=[]
                for prod in products:
                    meta=str(prod.get('metacard_external_id') or '')
                    cards=meta_cards.get(meta,set())
                    if not meta or len(cards)!=1: raise RuntimeError({'metacard_resolution_drift':code,'idProduct':str(prod['id_product']),'idMetacard':meta,'cards':sorted(cards)})
                    cid=next(iter(cards)); cprints=pc.get(cid,[])
                    if len(cprints)!=1: raise RuntimeError({'canonical_print_not_singleton':code,'idProduct':str(prod['id_product']),'card_id':cid,'print_count':len(cprints)})
                    pr=cprints[0]
                    if norm(prod['name'])!=norm(pr['card_name']): raise RuntimeError({'name_drift':code,'idProduct':str(prod['id_product']),'product':str(prod['name']),'card':str(pr['card_name'])})
                    eid=int(prod['external_product_id']); pid=int(pr['print_id'])
                    if by_product.get(eid) or by_print.get(pid): raise RuntimeError({'accepted_identity_claim_exists':code,'idProduct':str(prod['id_product']),'print_id':pid})
                    pairs.append({'set_code':code,'idExpansion':str(cfg['idExpansion']),'external_product_id':eid,'idProduct':str(prod['id_product']),'idMetacard':meta,'print_id':pid,'card_id':cid,'card_name':str(pr['card_name']),'collector_number':str(pr['collector_number']),'canonical_rarity':pr.get('rarity'),'canonical_variant':pr.get('variant'),'metacard_evidence_links':int(evidence.get((meta,cid),0))})
                if len(pairs)!=expected or len({x['external_product_id'] for x in pairs})!=expected or len({x['print_id'] for x in pairs})!=expected: raise RuntimeError({'not_one_to_one':code})
                proposal.extend(pairs); reports.append({'set_code':code,'idExpansion':str(cfg['idExpansion']),'products':len(products),'canonical_ja_prints':len(prints),'pairs':len(pairs),'existing_same':0,'new_ready':len(pairs)})
            conn.rollback()
    finally: conn.close()

    if len(proposal)!=EXPECTED_TOTAL or len({x['external_product_id'] for x in proposal})!=EXPECTED_TOTAL or len({x['print_id'] for x in proposal})!=EXPECTED_TOTAL: raise RuntimeError('global proposal not 219 one-to-one')
    payload={'status':'pass','mode':'read_only','production_writes':0,'cardmarket_capture':str(capture),'ja_baseline':ja,'identity_evidence_sha256':evidence_sha256(),'method':'first_party_public_code_certified_unique_metacard_to_single_JA_print','certified_pairs':len(proposal),'sets':reports,'proposal':proposal}
    out=Path(os.getenv('YGO_OCG_DUELIST_PACK_SINGLETONS_OUTPUT','/tmp/yugioh-ocg-duelist-packs-singletons-v1.json')); out.parent.mkdir(parents=True,exist_ok=True); out.write_text(json.dumps(payload,ensure_ascii=False,indent=2,sort_keys=True,default=str)+'\n',encoding='utf-8'); print(json.dumps(payload,ensure_ascii=False,indent=2,sort_keys=True,default=str)); return 0

if __name__=='__main__': raise SystemExit(main())
