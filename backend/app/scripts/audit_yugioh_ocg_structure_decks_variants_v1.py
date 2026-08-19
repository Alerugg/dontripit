from __future__ import annotations

import hashlib,json,os,unicodedata
from collections import defaultdict
from pathlib import Path
import psycopg2
from psycopg2.extras import RealDictCursor

GAME='yugioh'; ACCEPTED=('accepted','mapped','exact'); EXPECTED_JA=36426; EXPECTED_TOTAL=40
TARGETS={
 'SD41':{'idExpansion':'4535','physical':53,'singleton':43,'variant_pairs':10,'contracts':{'secret|super':('super','secret'),'secret|ultra':('ultra','secret')}},
 'SD40':{'idExpansion':'4545','physical':54,'singleton':38,'variant_pairs':16,'contracts':{'secretparallel|superparallel':('superparallel','secretparallel')}},
 'SD38':{'idExpansion':'4557','physical':53,'singleton':43,'variant_pairs':10,'contracts':{'secret|super':('super','secret'),'secret|ultra':('ultra','secret')}},
 'SD36':{'idExpansion':'4579','physical':48,'singleton':44,'variant_pairs':4,'contracts':{'secret|ultra':('ultra','secret')}},
}
EVIDENCE={
 'source':'Cardmarket first-party public Yu-Gi-Oh OCG Structure Deck pages','verified_at_utc':'2026-08-19',
 'contracts':{
  'SD41':{'urls':['https://www.cardmarket.com/en/YuGiOh/Products/Singles/Structure-Deck-Cyber-Style-s-Successor/Power-Bond-V2-Secret-Rare','https://www.cardmarket.com/en/YuGiOh/Products/Singles/Structure-Deck-Cyber-Style-s-Successor/Cyberdark-Dragon-V2-Secret-Rare','https://www.cardmarket.com/en/YuGiOh/Products/Singles/Structure-Deck-Cyber-Style-s-Successor/Cyberdark-End-Dragon-V2-Secret-Rare'],'observed':'SD41 public product pages label the second physical version Secret Rare; matching V.1 pages/expansion surface use Super Rare for Cyberdark Dragon/Overload Fusion/Power Bond/Cybernetic Horizon and Ultra Rare for Cyberdark End Dragon.'},
  'SD40':{'urls':['https://www.cardmarket.com/en/YuGiOh/Products/Singles/Structure-Deck-Ice-Barrier-of-the-Frozen-Prison/Trishula-Zero-Dragon-of-the-Ice-Barrier-V1-Super-Rare','https://www.cardmarket.com/en/YuGiOh/Products/Singles/Structure-Deck-Ice-Barrier-of-the-Frozen-Prison'],'observed':'SD40 first-party pages expose V.1 Super Rare and V.2 Secret Rare for the duplicated OCG cards; canonical JA physical rarities are Super Parallel and Secret Parallel respectively.'},
  'SD38':{'urls':['https://www.cardmarket.com/en/YuGiOh/Products/Singles/Structure-Deck-Sacred-Beasts-of-Chaos','https://www.cardmarket.com/en/YuGiOh/Products/Singles/Structure-Deck-Sacred-Beasts-of-Chaos/Raviel-Lord-of-Phantasms-V1-Ultra-Rare','https://www.cardmarket.com/en/YuGiOh/Products/Singles/Structure-Deck-Sacred-Beasts-of-Chaos/Raviel-Lord-of-Phantasms-V2-Secret-Rare'],'observed':'SD38 expansion/product pages explicitly expose V.1 Ultra / V.2 Secret for Uria/Hamon/Raviel/Armityle and V.1 Super / V.2 Secret for Dimension Fusion Destruction.'},
  'SD36':{'urls':['https://www.cardmarket.com/en/YuGiOh/Products/Singles/Structure-Deck-Revolver/Borreload-Furious-Dragon-V1-Ultra-Rare','https://www.cardmarket.com/en/YuGiOh/Products/Singles/Structure-Deck-Revolver'],'observed':'Structure Deck: Revolver first-party pages expose two physical versions for Borreload Furious Dragon and Quadborrel Dragon; V.1 is Ultra Rare and V.2 is Secret Rare.'},
 }
}

def evidence_sha256(): return hashlib.sha256(json.dumps(EVIDENCE,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()
def norm(v):
 t=unicodedata.normalize('NFKD',str(v or '')).casefold(); return ''.join(ch for ch in t if ch.isalnum())
def rarity(v):
 x=norm(v); aliases={'superrare':'super','super':'super','secretrare':'secret','secret':'secret','ultrarare':'ultra','ultra':'ultra','superparallelrare':'superparallel','superparallel':'superparallel','secretparallelrare':'secretparallel','secretparallel':'secretparallel'}; return aliases.get(x,x)
def rkey(rows): return '|'.join(sorted(rarity(r['rarity']) for r in rows))

def main():
 url=os.getenv('DATABASE_URL_UNPOOLED') or os.getenv('DATABASE_URL')
 if not url: raise RuntimeError('DATABASE URL required')
 c=psycopg2.connect(url,connect_timeout=30,application_name='dontripit_ygo_ocg_structure_deck_variants_v1'); c.set_session(readonly=True,autocommit=False)
 try:
  with c.cursor(cursor_factory=RealDictCursor) as cur:
   cur.execute("SELECT id FROM games WHERE slug=%s LIMIT 1",(GAME,)); gid=int(cur.fetchone()['id'])
   cur.execute("SELECT max(last_seen_at) capture FROM external_catalog_products WHERE source='cardmarket' AND game_id=%s",(gid,)); capture=cur.fetchone()['capture']
   cur.execute("""SELECT count(*) n FROM prints p JOIN cards c ON c.id=p.card_id WHERE c.game_id=%s AND lower(coalesce(p.language,''))='ja'""",(gid,)); ja=int(cur.fetchone()['n'])
   if ja!=EXPECTED_JA: raise RuntimeError({'ja_baseline_drift':ja})
   cur.execute("""SELECT e.metacard_external_id,p.card_id FROM external_catalog_print_links l JOIN external_catalog_products e ON e.id=l.external_product_id JOIN prints p ON p.id=l.print_id WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single' AND e.metacard_external_id IS NOT NULL AND l.link_status=ANY(%s) GROUP BY e.metacard_external_id,p.card_id""",(gid,list(ACCEPTED)))
   meta=defaultdict(set)
   for r in cur.fetchall(): meta[str(r['metacard_external_id'])].add(int(r['card_id']))
   cur.execute("""SELECT l.external_product_id,l.print_id FROM external_catalog_print_links l JOIN external_catalog_products e ON e.id=l.external_product_id WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single' AND l.link_status=ANY(%s)""",(gid,list(ACCEPTED)))
   bp=defaultdict(list); br=defaultdict(list)
   for r in cur.fetchall(): bp[int(r['external_product_id'])].append(dict(r)); br[int(r['print_id'])].append(dict(r))
   proposal=[]; reports=[]
   for code,cfg in TARGETS.items():
    cur.execute("""SELECT e.id external_product_id,e.external_id id_product,e.name,e.metacard_external_id FROM external_catalog_products e WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single' AND e.expansion_external_id=%s AND e.last_seen_at=%s ORDER BY e.metacard_external_id,e.external_id::bigint""",(gid,cfg['idExpansion'],capture)); products=[dict(r) for r in cur.fetchall()]
    cur.execute("""SELECT p.id print_id,p.card_id,p.collector_number,p.rarity,p.variant,c.name card_name FROM prints p JOIN cards c ON c.id=p.card_id JOIN sets s ON s.id=p.set_id WHERE c.game_id=%s AND upper(coalesce(s.code,''))=%s AND lower(coalesce(p.language,''))='ja' ORDER BY p.card_id,p.id""",(gid,code)); prints=[dict(r) for r in cur.fetchall()]
    if (len(products),len(prints))!=(cfg['physical'],cfg['physical']): raise RuntimeError({'surface_drift':code})
    pg=defaultdict(list); pc=defaultdict(list)
    for x in products: pg[str(x.get('metacard_external_id') or '')].append(x)
    for x in prints: pc[int(x['card_id'])].append(x)
    pairs=[]; groups=[]
    for m,g in pg.items():
     cards=meta.get(m,set()) if m else set()
     if not m or len(cards)!=1: raise RuntimeError({'metacard_resolution':code,'meta':m,'cards':sorted(cards)})
     cid=next(iter(cards)); cps=pc.get(cid,[])
     if len(g)!=len(cps): raise RuntimeError({'group_cardinality':code,'meta':m})
     if len(g)==1: continue
     if any(norm(x['name'])!=norm(cps[0]['card_name']) for x in g): raise RuntimeError({'name_drift':code,'meta':m})
     residual_products=[x for x in g if not bp.get(int(x['external_product_id']))]; residual_prints=[x for x in cps if not br.get(int(x['print_id']))]
     if not residual_products and not residual_prints: continue
     if len(residual_products)!=len(g) or len(residual_prints)!=len(cps): raise RuntimeError({'partial_variant_group_claim':code,'meta':m})
     key=rkey(cps); seq=cfg['contracts'].get(key)
     if not seq or len(seq)!=len(g): raise RuntimeError({'unsupported_variant_geometry':code,'meta':m,'rarity_key':key})
     byrar=defaultdict(list)
     for x in cps: byrar[rarity(x['rarity'])].append(x)
     if any(len(byrar[r])!=1 for r in seq): raise RuntimeError({'rarity_not_bijective':code,'meta':m})
     ordered=sorted(g,key=lambda x:int(x['id_product'])); gp=[]
     for ordinal,(prod,rar) in enumerate(zip(ordered,seq),1):
      pr=byrar[rar][0]; eid=int(prod['external_product_id']); pid=int(pr['print_id'])
      if bp.get(eid) or br.get(pid): raise RuntimeError({'claim_race':code,'idProduct':str(prod['id_product']),'print_id':pid})
      row={'set_code':code,'idExpansion':str(cfg['idExpansion']),'idMetacard':m,'external_product_id':eid,'idProduct':str(prod['id_product']),'product_name':str(prod['name']),'product_ordinal':ordinal,'contract_rarity':rar,'print_id':pid,'card_id':cid,'card_name':str(pr['card_name']),'collector_number':str(pr['collector_number']),'canonical_rarity':str(pr['rarity']),'canonical_variant':str(pr['variant'] or ''),'contract_key':key}
      gp.append(row); pairs.append(row); proposal.append(row)
     groups.append({'idMetacard':m,'card_id':cid,'card_name':str(cps[0]['card_name']),'rarity_key':key,'sequence':list(seq),'pairs':gp})
    if len(pairs)!=cfg['variant_pairs']: raise RuntimeError({'variant_pair_count_drift':code,'actual':len(pairs),'expected':cfg['variant_pairs']})
    reports.append({'set_code':code,'idExpansion':str(cfg['idExpansion']),'physical':cfg['physical'],'singleton_links_expected':cfg['singleton'],'variant_groups':len(groups),'variant_pairs':len(pairs),'groups':groups})
   c.rollback()
 finally: c.close()
 if len(proposal)!=EXPECTED_TOTAL or len({x['external_product_id'] for x in proposal})!=EXPECTED_TOTAL or len({x['print_id'] for x in proposal})!=EXPECTED_TOTAL: raise RuntimeError('global variant proposal not 40 one-to-one')
 payload={'status':'pass','mode':'read_only','production_writes':0,'cardmarket_capture':str(capture),'ja_baseline':ja,'evidence_sha256':evidence_sha256(),'evidence':EVIDENCE,'candidate_pairs':len(proposal),'candidate_products':len({x['external_product_id'] for x in proposal}),'candidate_prints':len({x['print_id'] for x in proposal}),'sets':reports,'proposal':proposal}
 out=Path(os.getenv('YGO_OCG_STRUCTURE_DECK_VARIANTS_OUTPUT','/tmp/yugioh-ocg-structure-deck-variants-v1.json')); out.parent.mkdir(parents=True,exist_ok=True); out.write_text(json.dumps(payload,ensure_ascii=False,indent=2,sort_keys=True,default=str)+'\n',encoding='utf-8'); print(json.dumps(payload,ensure_ascii=False,indent=2,sort_keys=True,default=str)); return 0
if __name__=='__main__': raise SystemExit(main())
