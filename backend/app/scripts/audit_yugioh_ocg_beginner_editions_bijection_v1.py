from __future__ import annotations

import hashlib
import json
import os
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

import psycopg2
from psycopg2.extras import RealDictCursor

GAME='yugioh'
ACCEPTED=('accepted','mapped','exact')
EXPECTED_JA=36426
TARGETS={
    'BE2': {'idExpansion':'4870','physical':250,'logical':250,'set_name':'ＢＥＧＩＮＮＥＲ＇Ｓ ＥＤＩＴＩＯＮ ２'},
    'BE02': {'idExpansion':'4756','physical':210,'logical':210,'set_name':'ＢＥＧＩＮＮＥＲ＇Ｓ ＥＤＩＴＩＯＮ ２ ［２０１１］'},
}
EXPECTED_TOTAL=sum(x['physical'] for x in TARGETS.values())


def norm(value: object)->str:
    text=unicodedata.normalize('NFKD',str(value or '')).casefold()
    return ''.join(ch for ch in text if ch.isalnum())


def contract_sha256()->str:
    raw=json.dumps(TARGETS,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode('utf-8')
    return hashlib.sha256(raw).hexdigest()


def main()->int:
    url=os.getenv('DATABASE_URL_UNPOOLED') or os.getenv('DATABASE_URL')
    if not url: raise RuntimeError('DATABASE URL required')
    conn=psycopg2.connect(url,connect_timeout=30,application_name='dontripit_ygo_ocg_beginner_editions_bijection_v1')
    conn.set_session(readonly=True,autocommit=False)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT id FROM games WHERE slug=%s LIMIT 1",(GAME,)); row=cur.fetchone()
            if not row: raise RuntimeError('Yu-Gi-Oh game missing')
            gid=int(row['id'])
            cur.execute("SELECT max(last_seen_at) capture FROM external_catalog_products WHERE source='cardmarket' AND game_id=%s",(gid,)); capture=cur.fetchone()['capture']
            if capture is None: raise RuntimeError('Cardmarket capture missing')
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

            cur.execute("""SELECT l.external_product_id,l.print_id,e.external_id id_product
                FROM external_catalog_print_links l JOIN external_catalog_products e ON e.id=l.external_product_id
                WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single' AND l.link_status=ANY(%s)""",(gid,list(ACCEPTED)))
            by_product=defaultdict(list); by_print=defaultdict(list)
            for r in cur.fetchall():
                by_product[int(r['external_product_id'])].append(dict(r)); by_print[int(r['print_id'])].append(dict(r))

            proposal=[]; reports=[]
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
                    raise RuntimeError({'canonical_not_logical_physical_bijection':code,'logical':len(card_ids),'physical':len(prints)})
                if {str(x['set_name']) for x in prints}!={cfg['set_name']}:
                    raise RuntimeError({'canonical_set_name_drift':code})
                prints_by_card=defaultdict(list)
                for x in prints: prints_by_card[int(x['card_id'])].append(x)
                if any(len(v)!=1 for v in prints_by_card.values()):
                    raise RuntimeError({'canonical_duplicate_card_prints':code})

                product_cards=[]; pairs=[]
                for prod in products:
                    meta=str(prod.get('metacard_external_id') or '')
                    cards=meta_cards.get(meta,set())
                    if not meta or len(cards)!=1:
                        raise RuntimeError({'metacard_resolution_drift':code,'idProduct':str(prod['id_product']),'idMetacard':meta,'cards':sorted(cards)})
                    cid=next(iter(cards)); product_cards.append(cid)
                    if cid not in prints_by_card:
                        raise RuntimeError({'resolved_card_outside_target_set':code,'idProduct':str(prod['id_product']),'card_id':cid})
                    pr=prints_by_card[cid][0]
                    if norm(prod['name'])!=norm(pr['card_name']):
                        raise RuntimeError({'name_drift':code,'idProduct':str(prod['id_product']),'product':str(prod['name']),'card':str(pr['card_name'])})
                    eid=int(prod['external_product_id']); pid=int(pr['print_id'])
                    if by_product.get(eid) or by_print.get(pid):
                        raise RuntimeError({'accepted_identity_claim_exists':code,'idProduct':str(prod['id_product']),'print_id':pid})
                    pairs.append({'set_code':code,'idExpansion':str(cfg['idExpansion']),'external_product_id':eid,'idProduct':str(prod['id_product']),'idMetacard':meta,'print_id':pid,'card_id':cid,'card_name':str(pr['card_name']),'collector_number':str(pr['collector_number']),'canonical_rarity':pr.get('rarity'),'canonical_variant':pr.get('variant'),'metacard_evidence_links':int(evidence.get((meta,cid),0))})

                if len(product_cards)!=cfg['logical'] or len(set(product_cards))!=cfg['logical'] or set(product_cards)!=card_ids:
                    raise RuntimeError({'logical_card_bijection_failed':code,'resolved':len(product_cards),'unique':len(set(product_cards)),'canonical':len(card_ids)})
                if Counter(norm(x['name']) for x in products)!=Counter(norm(x['card_name']) for x in prints):
                    raise RuntimeError({'normalized_name_multiset_drift':code})
                if len({x['external_product_id'] for x in pairs})!=cfg['physical'] or len({x['print_id'] for x in pairs})!=cfg['physical']:
                    raise RuntimeError({'physical_bijection_failed':code})
                proposal.extend(pairs)
                reports.append({'set_code':code,'idExpansion':str(cfg['idExpansion']),'products':len(products),'canonical_ja_prints':len(prints),'canonical_logical_cards':len(card_ids),'resolved_unique_logical_cards':len(set(product_cards)),'pairs':len(pairs),'existing_same':0,'new_ready':len(pairs)})
            conn.rollback()
    finally:
        conn.close()

    if len(proposal)!=EXPECTED_TOTAL or len({x['external_product_id'] for x in proposal})!=EXPECTED_TOTAL or len({x['print_id'] for x in proposal})!=EXPECTED_TOTAL:
        raise RuntimeError({'global_bijection_failed':len(proposal)})
    if not all(x['metacard_evidence_links']>0 for x in proposal):
        raise RuntimeError('proposal contains pair without accepted metacard evidence')
    payload={
        'status':'pass','mode':'read_only','production_writes':0,'cardmarket_capture':str(capture),'ja_baseline':ja,
        'contract_sha256':contract_sha256(),'method':'complete_current_expansion_to_exact_JA_set_logical_and_physical_bijection',
        'certified_pairs':len(proposal),'sets':reports,'proposal':proposal,
    }
    out=Path(os.getenv('YGO_OCG_BEGINNER_EDITIONS_BIJECTION_OUTPUT','/tmp/yugioh-ocg-beginner-editions-bijection-v1.json'))
    out.parent.mkdir(parents=True,exist_ok=True); out.write_text(json.dumps(payload,ensure_ascii=False,indent=2,sort_keys=True,default=str)+'\n',encoding='utf-8')
    print(json.dumps(payload,ensure_ascii=False,indent=2,sort_keys=True,default=str)); return 0

if __name__=='__main__': raise SystemExit(main())
