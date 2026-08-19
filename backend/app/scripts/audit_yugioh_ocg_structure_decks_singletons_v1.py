from __future__ import annotations

import json,os
from collections import Counter,defaultdict
from pathlib import Path
import psycopg2
from psycopg2.extras import RealDictCursor
from app.scripts.audit_yugioh_ocg_structure_decks_public_code_v1 import TARGETS,evidence_sha256,norm

GAME='yugioh'; ACCEPTED=('accepted','mapped','exact'); EXPECTED_JA=36426; EXPECTED_TOTAL=208

def main()->int:
 url=os.getenv('DATABASE_URL_UNPOOLED') or os.getenv('DATABASE_URL')
 if not url: raise RuntimeError('DATABASE URL required')
 conn=psycopg2.connect(url,connect_timeout=30,application_name='dontripit_ygo_ocg_structure_decks_singletons_v1'); conn.set_session(readonly=True,autocommit=False)
 try:
  with conn.cursor(cursor_factory=RealDictCursor) as cur:
   cur.execute("SELECT id FROM games WHERE slug=%s LIMIT 1",(GAME,)); gid=int(cur.fetchone()['id'])
   cur.execute("SELECT max(last_seen_at) capture FROM external_catalog_products WHERE source='cardmarket' AND game_id=%s",(gid,)); capture=cur.fetchone()['capture']
   if capture is None: raise RuntimeError('Cardmarket capture missing')
   cur.execute("""SELECT count(*) n FROM prints p JOIN cards c ON c.id=p.card_id WHERE c.game_id=%s AND lower(coalesce(p.language,''))='ja'""",(gid,)); ja=int(cur.fetchone()['n'])
   if ja!=EXPECTED_JA: raise RuntimeError({'ja_baseline_drift':ja})
   cur.execute("""SELECT e.metacard_external_id,p.card_id,count(*) evidence_links FROM external_catalog_print_links l JOIN external_catalog_products e ON e.id=l.external_product_id JOIN prints p ON p.id=l.print_id WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single' AND e.metacard_external_id IS NOT NULL AND l.link_status=ANY(%s) GROUP BY e.metacard_external_id,p.card_id""",(gid,list(ACCEPTED)))
   meta_cards=defaultdict(set); evidence=Counter()
   for r in cur.fetchall(): meta=str(r['metacard_external_id']); cid=int(r['card_id']); meta_cards[meta].add(cid); evidence[(meta,cid)]+=int(r['evidence_links'] or 0)
   cur.execute("""SELECT e.external_id id_product,l.external_product_id,l.print_id FROM external_catalog_print_links l JOIN external_catalog_products e ON e.id=l.external_product_id WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single' AND l.link_status=ANY(%s)""",(gid,list(ACCEPTED)))
   accepted=[dict(r) for r in cur.fetchall()]; by_product=defaultdict(list); by_print=defaultdict(list)
   for r in accepted: by_product[int(r['external_product_id'])].append(r); by_print[int(r['print_id'])].append(r)
   proposal=[]; residual=[]; reports=[]
   for code,cfg in TARGETS.items():
    cur.execute("""SELECT e.id external_product_id,e.external_id id_product,e.name,e.metacard_external_id FROM external_catalog_products e WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single' AND e.expansion_external_id=%s AND e.last_seen_at=%s ORDER BY e.metacard_external_id,e.external_id::bigint""",(gid,cfg['idExpansion'],capture)); products=[dict(r) for r in cur.fetchall()]
    cur.execute("""SELECT p.id print_id,p.card_id,p.collector_number,p.rarity,p.variant,c.name card_name FROM prints p JOIN cards c ON c.id=p.card_id JOIN sets s ON s.id=p.set_id WHERE c.game_id=%s AND upper(coalesce(s.code,''))=%s AND lower(coalesce(p.language,''))='ja' ORDER BY p.card_id,p.collector_number,p.id""",(gid,code)); prints=[dict(r) for r in cur.fetchall()]
    if (len(products),len(prints))!=(cfg['products'],cfg['prints']): raise RuntimeError({'surface_drift':code})
    pg=defaultdict(list); pc=defaultdict(list)
    for x in products: pg[str(x.get('metacard_external_id') or '')].append(x)
    for x in prints: pc[int(x['card_id'])].append(x)
    set_pairs=[]; set_residual=[]
    for meta,group in pg.items():
     cards=meta_cards.get(meta,set()) if meta else set()
     if not meta or len(cards)!=1: raise RuntimeError({'metacard_resolution_drift':code,'idMetacard':meta,'cards':sorted(cards)})
     cid=next(iter(cards)); cprints=pc.get(cid,[])
     if len(group)!=len(cprints): raise RuntimeError({'group_cardinality_drift':code,'idMetacard':meta,'products':len(group),'prints':len(cprints),'card_id':cid})
     if any(norm(x['name'])!=norm(cprints[0]['card_name']) for x in group): raise RuntimeError({'name_drift':code,'idMetacard':meta})
     claimed_products=sum(bool(by_product.get(int(x['external_product_id']))) for x in group); claimed_prints=sum(bool(by_print.get(int(x['print_id']))) for x in cprints)
     if claimed_products or claimed_prints: raise RuntimeError({'unexpected_claim_in_new_regional_surface':code,'idMetacard':meta,'claimed_products':claimed_products,'claimed_prints':claimed_prints})
     if len(group)==1:
      prod=group[0]; pr=cprints[0]
      pair={'set_code':code,'idExpansion':str(cfg['idExpansion']),'external_product_id':int(prod['external_product_id']),'idProduct':str(prod['id_product']),'idMetacard':meta,'print_id':int(pr['print_id']),'card_id':cid,'card_name':str(pr['card_name']),'collector_number':str(pr['collector_number']),'canonical_rarity':pr.get('rarity'),'canonical_variant':pr.get('variant'),'metacard_evidence_links':int(evidence.get((meta,cid),0))}
      set_pairs.append(pair); proposal.append(pair)
     else:
      row={'set_code':code,'idExpansion':str(cfg['idExpansion']),'idMetacard':meta,'card_id':cid,'card_name':str(cprints[0]['card_name']),'product_count':len(group),'print_count':len(cprints),'products':[{'external_product_id':int(x['external_product_id']),'idProduct':str(x['id_product']),'name':str(x['name'])} for x in sorted(group,key=lambda z:int(z['id_product']))],'prints':[{'print_id':int(x['print_id']),'collector_number':str(x['collector_number']),'rarity':x.get('rarity'),'variant':x.get('variant')} for x in cprints]}
      set_residual.append(row); residual.append(row)
    if len(set_pairs)+sum(r['product_count'] for r in set_residual)!=cfg['products']: raise RuntimeError({'set_partition_drift':code})
    reports.append({'set_code':code,'idExpansion':str(cfg['idExpansion']),'physical':cfg['products'],'logical':cfg['logical'],'singleton_pairs':len(set_pairs),'variant_groups':len(set_residual),'variant_products':sum(r['product_count'] for r in set_residual),'variant_extra_physical':sum(r['product_count']-1 for r in set_residual)})
   conn.rollback()
 finally: conn.close()
 if len(proposal)+sum(r['product_count'] for r in residual)!=EXPECTED_TOTAL: raise RuntimeError('global partition drift')
 if len({x['external_product_id'] for x in proposal})!=len(proposal) or len({x['print_id'] for x in proposal})!=len(proposal): raise RuntimeError('singleton proposal not one-to-one')
 payload={'status':'pass','mode':'read_only','production_writes':0,'cardmarket_capture':str(capture),'ja_baseline':ja,'identity_evidence_sha256':evidence_sha256(),'singleton_pairs':len(proposal),'singleton_products':len({x['external_product_id'] for x in proposal}),'singleton_prints':len({x['print_id'] for x in proposal}),'variant_groups':len(residual),'variant_products':sum(r['product_count'] for r in residual),'sets':reports,'proposal':proposal,'variant_residuals':residual}
 out=Path(os.getenv('YGO_OCG_STRUCTURE_DECKS_SINGLETONS_OUTPUT','/tmp/yugioh-ocg-structure-decks-singletons-v1.json')); out.parent.mkdir(parents=True,exist_ok=True); out.write_text(json.dumps(payload,ensure_ascii=False,indent=2,sort_keys=True,default=str)+'\n',encoding='utf-8'); print(json.dumps(payload,ensure_ascii=False,indent=2,sort_keys=True,default=str)); return 0
if __name__=='__main__': raise SystemExit(main())
