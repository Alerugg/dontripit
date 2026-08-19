from __future__ import annotations

import hashlib,json,os,unicodedata
from collections import Counter,defaultdict
from pathlib import Path
import psycopg2
from psycopg2.extras import RealDictCursor

GAME='yugioh'; ACCEPTED=('accepted','mapped','exact'); EXPECTED_JA=36426
TARGETS={
 'BE2':{
  'idExpansion':'4870','products':250,'prints':250,'public_code':'BE2','public_title':"Beginner's Edition 2",
  'evidence_urls':['https://www.cardmarket.com/en/YuGiOh/Products/Singles/Beginners-Edition-2','https://www.cardmarket.com/en/YuGiOh/Products/Singles/Beginners-Edition-2/Ryu-Senshi','https://www.cardmarket.com/en/YuGiOh/Products/Singles/Beginners-Edition-2/Book-of-Taiyou'],
  'observed_names':['Trap Dustshoot','Ryu Senshi','Book of Taiyou','Time Wizard']},
 'BE02':{
  'idExpansion':'4756','products':210,'prints':210,'public_code':'BE02','public_title':"Beginner's Edition 2 (2011)",
  'evidence_urls':['https://www.cardmarket.com/en/YuGiOh/Products/Singles/Beginners-Edition-2-2011/Ryu-Senshi','https://www.cardmarket.com/en/YuGiOh/Products/Singles/Beginners-Edition-2-2011/Trap-Dustshoot','https://www.cardmarket.com/en/YuGiOh/Products/Singles/Beginners-Edition-2-2011/Toon-Dark-Magician-Girl'],
  'observed_names':['Ryu Senshi','Trap Dustshoot','Toon Dark Magician Girl','Dark Necrofear']},
}
FIRST_PARTY_EVIDENCE={
 'source':'Cardmarket first-party public Yu-Gi-Oh pages','verified_at_utc':'2026-08-20',
 'scope':'OCG Beginner Edition public expansion/product codes only; numeric Cardmarket idExpansion candidates remain live-guarded',
 'targets':TARGETS,
 'observations':{
  'BE2':"Cardmarket expansion/product pages identify Beginner's Edition 2 singles with BE2 code and direct pages say Printed in Beginner's Edition 2.",
  'BE02':"Cardmarket direct product pages identify Beginner's Edition 2 (2011) singles with BE02 code and say Printed in Beginner's Edition 2 (2011).",
 },
}
def evidence_sha256():
 return hashlib.sha256(json.dumps(FIRST_PARTY_EVIDENCE,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()
def norm(v):
 t=unicodedata.normalize('NFKD',str(v or '')).casefold(); return ''.join(ch for ch in t if ch.isalnum())

def main():
 url=os.getenv('DATABASE_URL_UNPOOLED') or os.getenv('DATABASE_URL')
 if not url: raise RuntimeError('DATABASE URL required')
 c=psycopg2.connect(url,connect_timeout=30,application_name='dontripit_ygo_ocg_beginners_editions_public_code_v1'); c.set_session(readonly=True,autocommit=False)
 try:
  with c.cursor(cursor_factory=RealDictCursor) as cur:
   cur.execute("SELECT id FROM games WHERE slug=%s LIMIT 1",(GAME,)); gid=int(cur.fetchone()['id'])
   cur.execute("SELECT max(last_seen_at) capture FROM external_catalog_products WHERE source='cardmarket' AND game_id=%s",(gid,)); capture=cur.fetchone()['capture']
   cur.execute("SELECT count(*) n FROM prints p JOIN cards x ON x.id=p.card_id WHERE x.game_id=%s AND lower(coalesce(p.language,''))='ja'",(gid,)); ja=int(cur.fetchone()['n'])
   if ja!=EXPECTED_JA: raise RuntimeError({'ja_baseline_drift':ja})
   cur.execute("""SELECT e.metacard_external_id,p.card_id FROM external_catalog_print_links l JOIN external_catalog_products e ON e.id=l.external_product_id JOIN prints p ON p.id=l.print_id WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single' AND e.metacard_external_id IS NOT NULL AND l.link_status=ANY(%s) GROUP BY e.metacard_external_id,p.card_id""",(gid,list(ACCEPTED)))
   meta=defaultdict(set)
   for r in cur.fetchall(): meta[str(r['metacard_external_id'])].add(int(r['card_id']))
   reports=[]
   for code,cfg in TARGETS.items():
    cur.execute("""SELECT e.id external_product_id,e.external_id id_product,e.name,e.metacard_external_id FROM external_catalog_products e WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single' AND e.expansion_external_id=%s AND e.last_seen_at=%s ORDER BY e.external_id::bigint""",(gid,cfg['idExpansion'],capture)); products=[dict(r) for r in cur.fetchall()]
    cur.execute("""SELECT p.id print_id,p.card_id,p.collector_number,p.rarity,p.variant,x.name card_name FROM prints p JOIN cards x ON x.id=p.card_id JOIN sets s ON s.id=p.set_id WHERE x.game_id=%s AND upper(coalesce(s.code,''))=%s AND lower(coalesce(p.language,''))='ja' ORDER BY p.card_id,p.collector_number,p.id""",(gid,code)); prints=[dict(r) for r in cur.fetchall()]
    if (len(products),len(prints))!=(cfg['products'],cfg['prints']): raise RuntimeError({'surface_count_drift':code,'products':len(products),'prints':len(prints)})
    cur.execute("""SELECT count(*) n,count(DISTINCT l.external_product_id) products,count(DISTINCT l.print_id) prints FROM external_catalog_print_links l JOIN external_catalog_products e ON e.id=l.external_product_id JOIN prints p ON p.id=l.print_id JOIN sets s ON s.id=p.set_id WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single' AND e.expansion_external_id=%s AND l.link_status=ANY(%s) AND lower(coalesce(p.language,''))='ja' AND upper(coalesce(s.code,''))=%s""",(gid,cfg['idExpansion'],list(ACCEPTED),code)); a=cur.fetchone(); accepted={'links':int(a['n']),'products':int(a['products']),'prints':int(a['prints'])}
    if accepted!={'links':0,'products':0,'prints':0}: raise RuntimeError({'regional_candidate_already_claimed':code,'accepted':accepted})
    pn=Counter(norm(r['name']) for r in products); cn=Counter(norm(r['card_name']) for r in prints); set_cards={int(r['card_id']) for r in prints}
    metas=[str(r.get('metacard_external_id') or '') for r in products]; unique=into=amb=unres=0
    for m in metas:
     cards=meta.get(m,set()) if m else set()
     if len(cards)==1:
      unique+=1; into+=int(next(iter(cards)) in set_cards)
     elif len(cards)>1: amb+=1
     else: unres+=1
    observed={n:any(norm(n)==norm(r['name']) for r in products) for n in cfg['observed_names']}
    rep={'set_code':code,'idExpansion':cfg['idExpansion'],'public_code':cfg['public_code'],'public_title':cfg['public_title'],'products':len(products),'canonical_ja_prints':len(prints),'accepted':accepted,'unique_metacards':len(set(metas)),'blank_metacards':sum(not m for m in metas),'unique_canonical_cards':len(set_cards),'name_multiset_equal':pn==cn,'global_metacard_resolution':{'unique':unique,'into_exact_set':into,'ambiguous':amb,'unresolved':unres},'first_party_observed_names_present':observed}
    rep['candidate_certified']=(pn==cn and len(set(metas))==len(products) and not rep['blank_metacards'] and len(set_cards)==len(prints) and into==len(products) and amb==0 and unres==0 and all(observed.values()))
    reports.append(rep)
   c.rollback()
 finally: c.close()
 payload={'status':'pass' if all(r['candidate_certified'] for r in reports) else 'incomplete','mode':'read_only','production_writes':0,'ja_baseline':ja,'cardmarket_capture':str(capture),'evidence_sha256':evidence_sha256(),'evidence':FIRST_PARTY_EVIDENCE,'certified_count':sum(r['candidate_certified'] for r in reports),'results':reports}
 out=Path(os.getenv('YGO_OCG_BEGINNERS_EDITIONS_PUBLIC_CODE_OUTPUT','/tmp/ygo-ocg-beginners-editions-public-code-v1.json')); out.parent.mkdir(parents=True,exist_ok=True); out.write_text(json.dumps(payload,ensure_ascii=False,indent=2,sort_keys=True,default=str)+'\n'); print(json.dumps(payload,ensure_ascii=False,indent=2,sort_keys=True,default=str)); return 0
if __name__=='__main__': raise SystemExit(main())
