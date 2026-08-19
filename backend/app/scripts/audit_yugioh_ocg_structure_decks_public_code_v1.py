from __future__ import annotations

import hashlib
import json
import os
import unicodedata
from collections import Counter,defaultdict
from pathlib import Path

import psycopg2
from psycopg2.extras import RealDictCursor

GAME='yugioh'
ACCEPTED=('accepted','mapped','exact')
EXPECTED_JA=36426
TARGETS={
 'SD41':{'idExpansion':'4535','products':53,'prints':53,'logical':48,'public_title':"Structure Deck: Cyber Style's Successor",'observed_names':['Infinite Impermanence','Lightning Storm','Power Bond'],'evidence_urls':['https://www.cardmarket.com/en/YuGiOh/Products/Singles/Structure-Deck-Cyber-Style-s-Successor/Infinite-Impermanence','https://www.cardmarket.com/en/YuGiOh/Products/Singles/Structure-Deck-Cyber-Style-s-Successor/Power-Bond-V2-Secret-Rare']},
 'SD40':{'idExpansion':'4545','products':54,'prints':54,'logical':46,'public_title':'Structure Deck: Ice Barrier of the Frozen Prison','observed_names':['Strategist of the Ice Barrier','Crossout Designator','Trishula, Zero Dragon of the Ice Barrier'],'evidence_urls':['https://www.cardmarket.com/en/YuGiOh/Products/Singles/Structure-Deck-Ice-Barrier-of-the-Frozen-Prison']},
 'SD38':{'idExpansion':'4557','products':53,'prints':53,'logical':48,'public_title':'Structure Deck: Sacred Beasts of Chaos','observed_names':['Uria, Lord of Searing Flames','Hamon, Lord of Striking Thunder','Dimension Fusion Destruction'],'evidence_urls':['https://www.cardmarket.com/en/YuGiOh/Products/Singles/Structure-Deck-Sacred-Beasts-of-Chaos','https://www.cardmarket.com/en/YuGiOh/Products/Singles/Structure-Deck-Sacred-Beasts-of-Chaos/Hamon-Lord-of-Striking-Thunder-V1-Ultra-Rare']},
 'SD36':{'idExpansion':'4579','products':48,'prints':48,'logical':46,'public_title':'Structure Deck: Revolver','observed_names':['Magic Cylinder','Quick Launch'],'evidence_urls':['https://www.cardmarket.com/en/YuGiOh/Products/Singles/Structure-Deck-Revolver/Magic-Cylinder','https://www.cardmarket.com/en/YuGiOh/Products/Singles/Structure-Deck-Revolver/Quick-Launch']},
}
EVIDENCE={'source':'Cardmarket first-party public Yu-Gi-Oh pages','verified_at_utc':'2026-08-19','scope':'OCG Structure Deck public set codes/titles only; numeric idExpansion candidates remain live-guarded','targets':TARGETS,'observations':{'SD41':"Cardmarket direct pages are printed in Structure Deck: Cyber Style's Successor and are indexed with SD41.",'SD40':'Cardmarket expansion page lists Structure Deck: Ice Barrier of the Frozen Prison products with SD40 prefixes and explicit V.1/V.2 rows.','SD38':'Cardmarket expansion page lists Structure Deck: Sacred Beasts of Chaos products with SD38 prefixes and explicit rarity versions.','SD36':'Cardmarket direct pages are printed in Structure Deck: Revolver and indexed with SD36.'}}

def evidence_sha256()->str:
 raw=json.dumps(EVIDENCE,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode('utf-8'); return hashlib.sha256(raw).hexdigest()

def norm(v:object)->str:
 text=unicodedata.normalize('NFKD',str(v or '')).casefold(); return ''.join(ch for ch in text if ch.isalnum())

def main()->int:
 url=os.getenv('DATABASE_URL_UNPOOLED') or os.getenv('DATABASE_URL')
 if not url: raise RuntimeError('DATABASE URL required')
 conn=psycopg2.connect(url,connect_timeout=30,application_name='dontripit_ygo_ocg_structure_decks_public_code_v1'); conn.set_session(readonly=True,autocommit=False)
 try:
  with conn.cursor(cursor_factory=RealDictCursor) as cur:
   cur.execute("SELECT id FROM games WHERE slug=%s LIMIT 1",(GAME,)); gid=int(cur.fetchone()['id'])
   cur.execute("SELECT max(last_seen_at) capture FROM external_catalog_products WHERE source='cardmarket' AND game_id=%s",(gid,)); capture=cur.fetchone()['capture']
   if capture is None: raise RuntimeError('Cardmarket capture missing')
   cur.execute("""SELECT count(*) n FROM prints p JOIN cards c ON c.id=p.card_id WHERE c.game_id=%s AND lower(coalesce(p.language,''))='ja'""",(gid,)); ja=int(cur.fetchone()['n'])
   if ja!=EXPECTED_JA: raise RuntimeError({'ja_baseline_drift':ja})
   cur.execute("""SELECT e.metacard_external_id,p.card_id FROM external_catalog_print_links l JOIN external_catalog_products e ON e.id=l.external_product_id JOIN prints p ON p.id=l.print_id WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single' AND e.metacard_external_id IS NOT NULL AND l.link_status=ANY(%s) GROUP BY e.metacard_external_id,p.card_id""",(gid,list(ACCEPTED)))
   meta_cards=defaultdict(set)
   for r in cur.fetchall(): meta_cards[str(r['metacard_external_id'])].add(int(r['card_id']))
   reports=[]
   for code,cfg in TARGETS.items():
    cur.execute("""SELECT e.id external_product_id,e.external_id id_product,e.name,e.metacard_external_id FROM external_catalog_products e WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single' AND e.expansion_external_id=%s AND e.last_seen_at=%s ORDER BY e.metacard_external_id,e.external_id::bigint""",(gid,cfg['idExpansion'],capture)); products=[dict(r) for r in cur.fetchall()]
    cur.execute("""SELECT p.id print_id,p.card_id,p.collector_number,p.rarity,p.variant,c.name card_name FROM prints p JOIN cards c ON c.id=p.card_id JOIN sets s ON s.id=p.set_id WHERE c.game_id=%s AND upper(coalesce(s.code,''))=%s AND lower(coalesce(p.language,''))='ja' ORDER BY p.card_id,p.collector_number,p.id""",(gid,code)); prints=[dict(r) for r in cur.fetchall()]
    if (len(products),len(prints))!=(cfg['products'],cfg['prints']): raise RuntimeError({'surface_count_drift':code,'products':len(products),'prints':len(prints),'expected':cfg})
    cur.execute("""SELECT count(*) n,count(DISTINCT l.external_product_id) products,count(DISTINCT l.print_id) prints FROM external_catalog_print_links l JOIN external_catalog_products e ON e.id=l.external_product_id JOIN prints p ON p.id=l.print_id JOIN sets s ON s.id=p.set_id WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single' AND e.expansion_external_id=%s AND l.link_status=ANY(%s) AND lower(coalesce(p.language,''))='ja' AND upper(coalesce(s.code,''))=%s""",(gid,cfg['idExpansion'],list(ACCEPTED),code)); x=cur.fetchone(); accepted={'links':int(x['n']),'products':int(x['products']),'prints':int(x['prints'])}
    if accepted!={'links':0,'products':0,'prints':0}: raise RuntimeError({'regional_candidate_already_claimed':code,'accepted':accepted})
    product_names=Counter(norm(r['name']) for r in products); print_names=Counter(norm(r['card_name']) for r in prints)
    metas=[str(r.get('metacard_external_id') or '') for r in products]; unique_metas=set(metas); set_card_ids={int(r['card_id']) for r in prints}
    unique_resolved=into_exact=ambiguous=unresolved=0
    for meta in unique_metas:
     cards=meta_cards.get(meta,set()) if meta else set()
     if len(cards)==1:
      unique_resolved+=1
      if next(iter(cards)) in set_card_ids: into_exact+=1
     elif len(cards)>1: ambiguous+=1
     else: unresolved+=1
    observed={name:any(norm(name)==norm(r['name']) for r in products) for name in cfg['observed_names']}
    report={'set_code':code,'idExpansion':cfg['idExpansion'],'public_title':cfg['public_title'],'products':len(products),'canonical_ja_prints':len(prints),'accepted':accepted,'unique_metacards':len(unique_metas),'blank_metacards':sum(not m for m in metas),'unique_canonical_cards':len(set_card_ids),'name_multiset_equal':product_names==print_names,'product_only_names':sorted((product_names-print_names).elements())[:20],'canonical_only_names':sorted((print_names-product_names).elements())[:20],'global_unique_metacard_resolution':{'unique':unique_resolved,'into_exact_set':into_exact,'ambiguous':ambiguous,'unresolved':unresolved},'first_party_observed_names_present':observed,'all_observed_names_present':all(observed.values()),'candidate_certified':False}
    report['candidate_certified']=(report['name_multiset_equal'] and report['unique_metacards']==cfg['logical'] and report['blank_metacards']==0 and report['unique_canonical_cards']==cfg['logical'] and into_exact==cfg['logical'] and ambiguous==0 and unresolved==0 and report['all_observed_names_present'])
    reports.append(report)
   conn.rollback()
 finally: conn.close()
 payload={'status':'pass' if all(r['candidate_certified'] for r in reports) else 'incomplete','mode':'read_only','production_writes':0,'ja_baseline':ja,'cardmarket_capture':str(capture),'method':'frozen_first_party_public_structure_deck_code_plus_complete_current_product_to_exact_JA_name_multiset_and_metacard_coverage','evidence_sha256':evidence_sha256(),'evidence':EVIDENCE,'certified_count':sum(r['candidate_certified'] for r in reports),'results':reports}
 out=Path(os.getenv('YGO_OCG_STRUCTURE_DECKS_PUBLIC_CODE_OUTPUT','/tmp/yugioh-ocg-structure-decks-public-code-v1.json')); out.parent.mkdir(parents=True,exist_ok=True); out.write_text(json.dumps(payload,ensure_ascii=False,indent=2,sort_keys=True,default=str)+'\n',encoding='utf-8'); print(json.dumps(payload,ensure_ascii=False,indent=2,sort_keys=True,default=str)); return 0
if __name__=='__main__': raise SystemExit(main())
