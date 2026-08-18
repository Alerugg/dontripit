from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

import psycopg2
from psycopg2.extras import Json, RealDictCursor

GAME="yugioh"
EXPANSION_ID="6025"
EXPANSION_CODE="ALIN-JP"
SET_CODE="ALIN"
LANGUAGE="ja"
ACCEPTED=("accepted","mapped","exact")
EXPECTED_PAIRS=77
EXPECTED_ACCEPTED_BEFORE=55
EXPECTED_ACCEPTED_AFTER=132
EXPECTED_GROUP_HISTOGRAM={1:7,3:11,4:8,5:1}
CONFIRM="APPLY_YUGIOH_CARDMARKET_ALIN_JP_VERSION_ORDINAL_V1"
METHOD="cardmarket_ocg_certified_version_ordinal_v1"
MANIFEST=Path(__file__).resolve().parents[1]/"data"/"yugioh_alin_jp_version_ordinal_v1.csv"
MANIFEST_SHA256="cefabfdf6fdc0034b1f5a91520f0b96a76192281473e4b1e6bc04485daa18640"
EXPECTED_SEQUENCE={
    3:("super","secret","25thsecret"),
    4:("ultra","secret","25thsecret","ultimate"),
    5:("ultra","secret","25thsecret","ultimate","ghost"),
}
FIVE_VERSION_METACARD="445611"
FIVE_VERSION_COLLECTOR="ALIN-JP051"


def _norm(value:str|None)->str:
    text=unicodedata.normalize("NFKD",str(value or "")).casefold()
    return "".join(ch for ch in text if ch.isalnum())


def _connect(*,readonly:bool):
    url=os.getenv("DATABASE_URL_UNPOOLED") or os.getenv("DATABASE_URL")
    if not url: raise RuntimeError("DATABASE_URL_UNPOOLED or DATABASE_URL is required")
    conn=psycopg2.connect(url,connect_timeout=30,application_name="dontripit_ygo_alin_jp_version_ordinal_v1")
    conn.set_session(readonly=readonly,autocommit=False)
    return conn


def _load_manifest()->list[dict]:
    raw=MANIFEST.read_bytes(); digest=hashlib.sha256(raw).hexdigest()
    if digest!=MANIFEST_SHA256: raise RuntimeError(f"manifest sha256 drifted: expected={MANIFEST_SHA256} actual={digest}")
    rows=[dict(r) for r in csv.DictReader(raw.decode("utf-8").splitlines())]
    if len(rows)!=EXPECTED_PAIRS: raise RuntimeError(f"manifest count expected={EXPECTED_PAIRS} actual={len(rows)}")
    groups=defaultdict(list)
    for r in rows:
        r["group_size"]=int(r["group_size"]); r["ordinal"]=int(r["ordinal"]); r["print_id"]=int(r["print_id"]); r["canonical_rarity"]=str(r["canonical_rarity"]).casefold()
        groups[str(r["idMetacard"])].append(r)
    if len({str(r['idProduct']) for r in rows})!=EXPECTED_PAIRS or len({int(r['print_id']) for r in rows})!=EXPECTED_PAIRS:
        raise RuntimeError("manifest is not one-to-one")
    hist=Counter()
    for meta,group in groups.items():
        group=sorted(group,key=lambda r:int(r['idProduct'])); size=len(group); hist[size]+=1
        if {r['group_size'] for r in group}!={size}: raise RuntimeError(f"group_size drift {meta}")
        if [r['ordinal'] for r in group]!=list(range(1,size+1)): raise RuntimeError(f"ordinal drift {meta}")
        if size>1 and tuple(r['canonical_rarity'] for r in group)!=EXPECTED_SEQUENCE[size]:
            raise RuntimeError(f"rarity sequence drift {meta}: {[r['canonical_rarity'] for r in group]}")
    if dict(hist)!=EXPECTED_GROUP_HISTOGRAM: raise RuntimeError(f"group histogram drift expected={EXPECTED_GROUP_HISTOGRAM} actual={dict(hist)}")
    five=groups.get(FIVE_VERSION_METACARD,[])
    if len(five)!=5 or {r['collector_number'] for r in five}!={FIVE_VERSION_COLLECTOR}: raise RuntimeError("Allied Code Talker five-version surface drifted")
    return rows


def _state(cur,manifest):
    cur.execute("SELECT id FROM games WHERE slug=%s LIMIT 1",(GAME,)); game_id=int(cur.fetchone()['id'])
    cur.execute("SELECT max(last_seen_at) capture FROM external_catalog_products WHERE source='cardmarket'"); capture=cur.fetchone()['capture']
    wanted_products=[str(r['idProduct']) for r in manifest]
    cur.execute("""SELECT e.id external_product_id,e.external_id id_product,e.name,e.metacard_external_id,e.expansion_external_id,e.last_seen_at FROM external_catalog_products e WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single' AND e.external_id=ANY(%s) AND e.last_seen_at=%s ORDER BY e.external_id::bigint""",(game_id,wanted_products,capture))
    products={str(r['id_product']):dict(r) for r in cur.fetchall()}
    wanted_prints=[int(r['print_id']) for r in manifest]
    cur.execute("""SELECT p.id print_id,p.card_id,p.collector_number,p.rarity,p.variant,p.language,c.name card_name,s.code set_code FROM prints p JOIN cards c ON c.id=p.card_id JOIN sets s ON s.id=p.set_id WHERE p.id=ANY(%s) AND c.game_id=%s ORDER BY p.id""",(wanted_prints,game_id))
    prints={int(r['print_id']):dict(r) for r in cur.fetchall()}
    cur.execute("""SELECT l.external_product_id,l.print_id,l.mapping_method,l.confidence,l.reviewed,e.external_id id_product FROM external_catalog_print_links l JOIN external_catalog_products e ON e.id=l.external_product_id WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single' AND l.link_status=ANY(%s)""",(game_id,list(ACCEPTED)))
    accepted=[dict(r) for r in cur.fetchall()]
    return game_id,capture,products,prints,accepted


def _validate(cur,*,game_id,capture,manifest,products,prints,accepted):
    by_product=defaultdict(list); by_print=defaultdict(list)
    for r in accepted:
        by_product[str(r['id_product'])].append(r); by_print[int(r['print_id'])].append(r)
    groups=defaultdict(list)
    for r in manifest: groups[str(r['idMetacard'])].append(r)
    errors=[]; proposal=[]; existing=[]
    for meta,certs in groups.items():
        certs=sorted(certs,key=lambda r:int(r['idProduct']))
        cur.execute("""SELECT e.external_id id_product,e.name FROM external_catalog_products e WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single' AND e.expansion_external_id=%s AND e.last_seen_at=%s AND e.metacard_external_id=%s ORDER BY e.external_id::bigint""",(game_id,EXPANSION_ID,capture,meta))
        full=[dict(r) for r in cur.fetchall()]
        actual=[str(r['id_product']) for r in full]; expected=[str(r['idProduct']) for r in certs]
        if actual!=expected: errors.append({'idMetacard':meta,'error':'complete_product_surface_drift','expected':expected,'actual':actual}); continue
        card_ids=set(); normalized_product_names=set(); normalized_card_names=set()
        for cert in certs:
            pid=str(cert['idProduct']); print_id=int(cert['print_id']); product=products.get(pid); pr=prints.get(print_id)
            if not product or not pr: errors.append({'idProduct':pid,'print_id':print_id,'error':'identity_missing'}); continue
            card_ids.add(int(pr['card_id'])); normalized_product_names.add(_norm(product.get('name'))); normalized_card_names.add(_norm(pr.get('card_name')))
            failed=[]
            if str(product.get('expansion_external_id') or '')!=EXPANSION_ID: failed.append('expansion')
            if str(product.get('metacard_external_id') or '')!=meta: failed.append('metacard')
            if str(pr.get('collector_number') or '')!=str(cert['collector_number']): failed.append('collector')
            if str(pr.get('rarity') or '').casefold()!=cert['canonical_rarity']: failed.append('rarity')
            if str(pr.get('language') or '').casefold()!=LANGUAGE: failed.append('language')
            if str(pr.get('set_code') or '').upper()!=SET_CODE: failed.append('set')
            if _norm(product.get('name'))!=_norm(pr.get('card_name')): failed.append('normalized_name')
            if failed: errors.append({'idProduct':pid,'print_id':print_id,'error':'identity_guard_failed','failed':failed}); continue
            pclaims=by_product.get(pid,[]); iclaims=by_print.get(print_id,[])
            exact=[r for r in pclaims if int(r['print_id'])==print_id and str(r.get('mapping_method') or '')==METHOD and str(r.get('confidence') or '')=='exact' and bool(r.get('reviewed'))]
            if exact:
                if any(int(r['print_id'])!=print_id for r in pclaims) or any(str(r['id_product'])!=pid for r in iclaims): errors.append({'idProduct':pid,'print_id':print_id,'error':'existing_competing_claim'})
                else: existing.append({**cert,'external_product_id':int(product['external_product_id']),'card_id':int(pr['card_id'])})
                continue
            if pclaims: errors.append({'idProduct':pid,'print_id':print_id,'error':'product_already_claimed'}); continue
            if iclaims: errors.append({'idProduct':pid,'print_id':print_id,'error':'print_already_claimed'}); continue
            proposal.append({**cert,'external_product_id':int(product['external_product_id']),'card_id':int(pr['card_id']),'card_name':pr['card_name']})
        if len(card_ids)!=1: errors.append({'idMetacard':meta,'error':'group_spans_multiple_cards','card_ids':sorted(card_ids)})
        if normalized_product_names!=normalized_card_names or len(normalized_product_names)!=1: errors.append({'idMetacard':meta,'error':'normalized_group_name_disagrees','product_names':sorted(normalized_product_names),'card_names':sorted(normalized_card_names)})
    if errors: raise RuntimeError(json.dumps({'alin_version_ordinal_validation_errors':errors},ensure_ascii=False,default=str))
    if len(proposal)+len(existing)!=EXPECTED_PAIRS: raise RuntimeError(f"manifest coverage proposal={len(proposal)} existing={len(existing)}")
    if len({str(r['idProduct']) for r in proposal+existing})!=EXPECTED_PAIRS or len({int(r['print_id']) for r in proposal+existing})!=EXPECTED_PAIRS: raise RuntimeError("validated pairs not one-to-one")
    return proposal,existing


def run(*,apply:bool,confirm:str=""):
    if apply and confirm!=CONFIRM: raise RuntimeError(f"--apply requires --confirm {CONFIRM}")
    manifest=_load_manifest(); conn=_connect(readonly=not apply)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            game_id,capture,products,prints,accepted=_state(cur,manifest)
            cur.execute("""SELECT count(*) n FROM external_catalog_print_links l JOIN external_catalog_products e ON e.id=l.external_product_id JOIN prints p ON p.id=l.print_id JOIN sets s ON s.id=p.set_id WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single' AND e.expansion_external_id=%s AND l.link_status=ANY(%s) AND lower(coalesce(p.language,''))=%s AND upper(coalesce(s.code,''))=%s""",(game_id,EXPANSION_ID,list(ACCEPTED),LANGUAGE,SET_CODE))
            before=int(cur.fetchone()['n'])
            if before not in (EXPECTED_ACCEPTED_BEFORE,EXPECTED_ACCEPTED_AFTER): raise RuntimeError(f"unexpected ALIN accepted surface {before}")
            proposal,existing=_validate(cur,game_id=game_id,capture=capture,manifest=manifest,products=products,prints=prints,accepted=accepted)
            if before==EXPECTED_ACCEPTED_BEFORE and (len(proposal),len(existing))!=(EXPECTED_PAIRS,0): raise RuntimeError(f"preapply idempotency failed proposal={len(proposal)} existing={len(existing)}")
            if before==EXPECTED_ACCEPTED_AFTER and (len(proposal),len(existing))!=(0,EXPECTED_PAIRS): raise RuntimeError(f"postapply idempotency failed proposal={len(proposal)} existing={len(existing)}")
            report={'mode':'apply' if apply else 'dry_run','production_writes':0,'cardmarket_capture':str(capture),'manifest_sha256':MANIFEST_SHA256,'manifest_pairs':EXPECTED_PAIRS,'accepted_alin_before':before,'proposed_exact_links':len(proposal),'already_exact_idempotent_links':len(existing),'mapping_method':METHOD,'ordinal_contract':{'3':['super','secret','25thsecret'],'4':['ultra','secret','25thsecret','ultimate'],'5':['ultra','secret','25thsecret','ultimate','ghost']},'name_normalization':'unicode NFKD + alphanumeric casefold; reconciles Cardmarket display-symbol omissions without changing identity','external_five_version_evidence':{'card':'Allied Code Talker @Ignister','collector_number':FIVE_VERSION_COLLECTOR,'cardmarket_version':'V.5 - Holographic Rare','canonical_equivalence':'ghost'},'proposal':proposal}
            if not apply: conn.rollback(); return report
            writes=0
            for row in proposal:
                evidence={'source':'cardmarket_official_version_ordinal+yugioh_canonical_physical_prints','identity_basis':['certified_ALIN-JP_expansion','complete_metacard_product_surface','exact_collector_number','exact_JA_language','normalized_card_name','official_ALIN_OCG_version_ordinal','global_one_to_one'],'idExpansion':EXPANSION_ID,'expansion_code':EXPANSION_CODE,'idProduct':str(row['idProduct']),'idMetacard':str(row['idMetacard']),'collector_number':str(row['collector_number']),'product_ordinal':int(row['ordinal']),'group_size':int(row['group_size']),'canonical_rarity':str(row['canonical_rarity']),'manifest_sha256':MANIFEST_SHA256}
                if str(row['idMetacard'])==FIVE_VERSION_METACARD: evidence['five_version_external_proof']='Cardmarket ALIN-JP lists Allied Code Talker @Ignister V.5 as Holographic Rare; canonical fifth rarity is ghost'
                cur.execute("""INSERT INTO external_catalog_print_links(external_product_id,print_id,mapping_method,confidence,link_status,reviewed,evidence) VALUES(%s,%s,%s,'exact','accepted',true,%s) ON CONFLICT(external_product_id,print_id) DO NOTHING""",(int(row['external_product_id']),int(row['print_id']),METHOD,Json(evidence)))
                if cur.rowcount!=1: raise RuntimeError(f"atomic insert lost race idProduct={row['idProduct']} print={row['print_id']}")
                writes+=1
            cur.execute("""SELECT count(*) n FROM external_catalog_print_links l JOIN external_catalog_products e ON e.id=l.external_product_id JOIN prints p ON p.id=l.print_id JOIN sets s ON s.id=p.set_id WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single' AND e.expansion_external_id=%s AND l.link_status=ANY(%s) AND lower(coalesce(p.language,''))=%s AND upper(coalesce(s.code,''))=%s""",(game_id,EXPANSION_ID,list(ACCEPTED),LANGUAGE,SET_CODE))
            after=int(cur.fetchone()['n'])
            if after!=EXPECTED_ACCEPTED_AFTER: raise RuntimeError(f"postapply ALIN expected={EXPECTED_ACCEPTED_AFTER} actual={after}")
            report['accepted_alin_after']=after; report['production_writes']=writes; conn.commit(); return report
    except Exception:
        conn.rollback(); raise
    finally: conn.close()


def main()->int:
    p=argparse.ArgumentParser(); p.add_argument('--apply',action='store_true'); p.add_argument('--confirm',default=''); p.add_argument('--report',type=Path,default=Path('/tmp/yugioh-cardmarket-alin-jp-version-ordinal-v1.json')); a=p.parse_args()
    payload=run(apply=a.apply,confirm=a.confirm); a.report.parent.mkdir(parents=True,exist_ok=True); text=json.dumps(payload,ensure_ascii=False,indent=2,sort_keys=True,default=str)+'\n'; a.report.write_text(text,encoding='utf-8'); print(text,end=''); return 0

if __name__=='__main__': raise SystemExit(main())
