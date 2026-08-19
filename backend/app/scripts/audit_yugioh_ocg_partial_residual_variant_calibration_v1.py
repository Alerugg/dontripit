from __future__ import annotations

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
EXPECTED_CAPTURE='2026-08-18 21:09:20.740891+00:00'
MIN_SUPPORT=2
TARGETS={
 'ROTA':'5840','BLVO':'4546','CHIM':'4577','ETCO':'4563','CIBR':'4640','EXFO':'4634',
 'FLOD':'4627','INOV':'4660','RATE':'4655','TDIL':'4666','CSOC':'4809',
}


def norm(v: object)->str:
    t=unicodedata.normalize('NFKD',str(v or '')).casefold()
    return ''.join(ch for ch in t if ch.isalnum())


def rarity(v: object)->str:
    x=norm(v)
    aliases={
      'commonrare':'common','common':'common','commonparallelrare':'commonparallel','commonparallel':'commonparallel',
      'rarerare':'rare','rare':'rare','rareparallelrare':'rareparallel','rareparallel':'rareparallel',
      'superrare':'super','super':'super','superparallelrare':'superparallel','superparallel':'superparallel',
      'ultrarare':'ultra','ultra':'ultra','ultraparallelrare':'ultraparallel','ultraparallel':'ultraparallel',
      'secretrare':'secret','secret':'secret','secretparallelrare':'secretparallel','secretparallel':'secretparallel',
      'ultimaterare':'ultimate','ultimate':'ultimate','collectorsrare':'collectors','collectors':'collectors',
      'holographicrare':'holographic','holographic':'holographic','ghostrare':'ghost','ghost':'ghost',
    }
    return aliases.get(x,x)


def signature(rows): return tuple(sorted(rarity(r['rarity']) for r in rows))
def sequence(rows): return tuple(rarity(r['rarity']) for r in sorted(rows,key=lambda r:int(r['id_product'])))


def main()->int:
    url=os.getenv('DATABASE_URL_UNPOOLED') or os.getenv('DATABASE_URL')
    if not url: raise RuntimeError('DATABASE URL required')
    conn=psycopg2.connect(url,connect_timeout=30,application_name='dontripit_ygo_ocg_partial_residual_variant_calibration_v1')
    conn.set_session(readonly=True,autocommit=False)
    try:
      with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT id FROM games WHERE slug=%s LIMIT 1",(GAME,)); gid=int(cur.fetchone()['id'])
        cur.execute("SELECT max(last_seen_at) capture FROM external_catalog_products WHERE source='cardmarket' AND game_id=%s",(gid,)); capture=cur.fetchone()['capture']
        if str(capture)!=EXPECTED_CAPTURE: raise RuntimeError({'capture_drift':str(capture)})
        cur.execute("SELECT count(*) n FROM prints p JOIN cards c ON c.id=p.card_id WHERE c.game_id=%s AND lower(coalesce(p.language,''))='ja'",(gid,)); ja=int(cur.fetchone()['n'])
        if ja!=EXPECTED_JA: raise RuntimeError({'ja_baseline_drift':ja})

        # Current product groups.
        cur.execute("""SELECT e.id external_product_id,e.external_id id_product,e.expansion_external_id,e.metacard_external_id,e.name
          FROM external_catalog_products e WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single'
          AND e.last_seen_at=%s AND e.expansion_external_id IS NOT NULL AND e.metacard_external_id IS NOT NULL
          ORDER BY e.expansion_external_id,e.metacard_external_id,e.external_id::bigint""",(gid,capture))
        product_groups=defaultdict(list)
        for r in cur.fetchall():
            row=dict(r); product_groups[(str(row['expansion_external_id']),str(row['metacard_external_id']))].append(row)

        # Complete accepted metacard identity bridge and all claims.
        cur.execute("""SELECT e.id external_product_id,e.external_id id_product,e.expansion_external_id,e.metacard_external_id,e.name,
          l.mapping_method,l.confidence,l.reviewed,p.id print_id,p.card_id,p.rarity,p.variant,p.collector_number,c.name card_name,s.code set_code,p.language
          FROM external_catalog_print_links l JOIN external_catalog_products e ON e.id=l.external_product_id
          JOIN prints p ON p.id=l.print_id JOIN cards c ON c.id=p.card_id JOIN sets s ON s.id=p.set_id
          WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single' AND l.link_status=ANY(%s)""",(gid,list(ACCEPTED)))
        accepted=[dict(r) for r in cur.fetchall()]
        meta=defaultdict(set); by_product=defaultdict(list); by_print=defaultdict(list)
        for r in accepted:
            if r.get('metacard_external_id') is not None: meta[str(r['metacard_external_id'])].add(int(r['card_id']))
            by_product[int(r['external_product_id'])].append(r); by_print[int(r['print_id'])].append(r)

        # Non-circular calibration uses current exact/reviewed JA groups only, excluding all ordinal-derived methods.
        calibration=defaultdict(Counter); cal_sets=defaultdict(lambda:defaultdict(set)); cal_methods=defaultdict(lambda:defaultdict(Counter)); cal_examples=defaultdict(lambda:defaultdict(list))
        accepted_current=defaultdict(list)
        for r in accepted:
            if str(r['language']).lower()!='ja' or str(r['confidence'])!='exact' or not bool(r['reviewed']): continue
            if str(r.get('mapping_method') or '').find('ordinal')>=0: continue
            if not r.get('metacard_external_id'): continue
            # We only calibrate from products that are still in the current capture.
            key=(str(r['expansion_external_id']),str(r['metacard_external_id']))
            if key not in product_groups: continue
            accepted_current[key].append(r)
        calibration_groups=0
        for key,gp in product_groups.items():
            if len(gp)<=1: continue
            rows=accepted_current.get(key,[])
            if len(rows)!=len(gp) or len({int(r['external_product_id']) for r in rows})!=len(gp) or len({int(r['print_id']) for r in rows})!=len(gp): continue
            if len({int(r['card_id']) for r in rows})!=1 or len({str(r['set_code']).upper() for r in rows})!=1: continue
            if any(norm(r['name'])!=norm(r['card_name']) for r in rows): continue
            sig=signature(rows); seq=sequence(rows)
            if len(sig)!=len(set(sig)): continue
            calibration[sig][seq]+=1; cal_sets[sig][seq].add(str(rows[0]['set_code']).upper()); cal_methods[sig][seq].update(str(r['mapping_method']) for r in rows)
            if len(cal_examples[sig][seq])<5:
                cal_examples[sig][seq].append({'set_code':str(rows[0]['set_code']).upper(),'idExpansion':key[0],'idMetacard':key[1],'idProducts':[str(x['id_product']) for x in sorted(rows,key=lambda x:int(x['id_product']))]})
            calibration_groups+=1

        certifiable=[]; unresolved=[]; proposal=[]; per_set=[]; target_signature_counts=Counter()
        for code,exp in TARGETS.items():
            cur.execute("""SELECT p.id print_id,p.card_id,p.collector_number,p.rarity,p.variant,c.name card_name
              FROM prints p JOIN cards c ON c.id=p.card_id JOIN sets s ON s.id=p.set_id
              WHERE c.game_id=%s AND upper(coalesce(s.code,''))=%s AND lower(coalesce(p.language,''))='ja'
              ORDER BY p.card_id,p.id""",(gid,code))
            by_card=defaultdict(list)
            for r in cur.fetchall(): row=dict(r); by_card[int(row['card_id'])].append(row)
            groups=cert_groups=cert_pairs=unres_groups=unres_pairs=0
            for (gexp,m),gp in product_groups.items():
                if gexp!=exp or len(gp)<=1: continue
                cards=meta.get(m,set())
                if len(cards)!=1: raise RuntimeError({'target_metacard_resolution_drift':code,'idMetacard':m,'cards':sorted(cards)})
                cid=next(iter(cards)); cp=by_card.get(cid,[])
                if not cp: raise RuntimeError({'target_card_missing':code,'idMetacard':m,'card_id':cid})
                # Residual group must be entirely unclaimed or entirely claimed. Partial state is unsafe.
                claimed_p=sum(bool(by_product.get(int(p['external_product_id']))) for p in gp)
                claimed_pr=sum(bool(by_print.get(int(pr['print_id']))) for pr in cp)
                if claimed_p==len(gp) and claimed_pr==len(cp): continue
                if claimed_p or claimed_pr: raise RuntimeError({'partial_residual_claim_state':code,'idMetacard':m,'claimed_products':claimed_p,'products':len(gp),'claimed_prints':claimed_pr,'prints':len(cp)})
                if len(gp)!=len(cp): raise RuntimeError({'residual_cardinality_drift':code,'idMetacard':m,'products':len(gp),'prints':len(cp)})
                if any(norm(p['name'])!=norm(cp[0]['card_name']) for p in gp): raise RuntimeError({'target_name_drift':code,'idMetacard':m})
                groups+=1; sig=signature(cp); target_signature_counts[sig]+=1
                seqs=calibration.get(sig,Counter())
                supported=[(seq,n) for seq,n in seqs.items() if n>=MIN_SUPPORT]
                safe=(len(supported)==1 and len(seqs)==1 and len(sig)==len(set(sig)))
                if safe:
                    seq,support=supported[0]; byrar=defaultdict(list)
                    for pr in cp: byrar[rarity(pr['rarity'])].append(pr)
                    if any(len(byrar[r])!=1 for r in seq): safe=False
                if not safe:
                    unresolved.append({'set_code':code,'idExpansion':exp,'idMetacard':m,'card_id':cid,'card_name':str(cp[0]['card_name']),'physical':len(gp),'rarity_signature':list(sig),'observed_sequences':[{'sequence':list(seq),'support_groups':int(n),'support_sets':sorted(cal_sets[sig][seq]),'methods':dict(cal_methods[sig][seq])} for seq,n in seqs.items()]})
                    unres_groups+=1; unres_pairs+=len(gp); continue
                ordered=sorted(gp,key=lambda x:int(x['id_product'])); pairs=[]
                for ordinal,(prod,rar) in enumerate(zip(ordered,seq),1):
                    pr=byrar[rar][0]
                    row={'set_code':code,'idExpansion':exp,'idMetacard':m,'card_id':cid,'card_name':str(pr['card_name']),'external_product_id':int(prod['external_product_id']),'idProduct':str(prod['id_product']),'product_ordinal':ordinal,'calibrated_rarity':rar,'print_id':int(pr['print_id']),'collector_number':str(pr['collector_number']),'canonical_rarity':str(pr['rarity']),'canonical_variant':str(pr.get('variant') or ''),'rarity_signature':list(sig),'calibration_support_groups':int(support),'calibration_support_sets':sorted(cal_sets[sig][seq]),'calibration_methods':dict(cal_methods[sig][seq])}
                    pairs.append(row); proposal.append(row)
                certifiable.append({'set_code':code,'idExpansion':exp,'idMetacard':m,'card_id':cid,'card_name':str(cp[0]['card_name']),'physical':len(gp),'rarity_signature':list(sig),'sequence':list(seq),'support_groups':int(support),'support_sets':sorted(cal_sets[sig][seq]),'pairs':pairs})
                cert_groups+=1; cert_pairs+=len(gp)
            per_set.append({'set_code':code,'idExpansion':exp,'residual_variant_groups':groups,'certifiable_groups':cert_groups,'certifiable_pairs':cert_pairs,'unresolved_groups':unres_groups,'unresolved_pairs':unres_pairs})
        conn.rollback()
    finally:
      conn.close()

    if len({x['external_product_id'] for x in proposal})!=len(proposal) or len({x['print_id'] for x in proposal})!=len(proposal): raise RuntimeError('proposal_not_globally_one_to_one')
    cal=[]
    for sig,seqs in sorted(calibration.items(),key=lambda kv:(len(kv[0]),kv[0])):
        cal.append({'rarity_signature':list(sig),'sequences':[{'sequence':list(seq),'support_groups':int(n),'support_sets':sorted(cal_sets[sig][seq]),'methods':dict(cal_methods[sig][seq]),'examples':cal_examples[sig][seq]} for seq,n in seqs.items()]})
    payload={'status':'pass','mode':'read_only','production_writes':0,'cardmarket_capture':str(capture),'ja_baseline':ja,'minimum_support':MIN_SUPPORT,
      'independent_calibration_groups':calibration_groups,'target_residual_variant_groups':sum(x['residual_variant_groups'] for x in per_set),
      'certifiable_groups':len(certifiable),'certifiable_pairs':len(proposal),'unresolved_groups':len(unresolved),
      'target_signature_counts':[{'rarity_signature':list(sig),'groups':int(n)} for sig,n in sorted(target_signature_counts.items(),key=lambda kv:(-kv[1],kv[0]))],
      'sets':per_set,'calibration':cal,'certifiable':certifiable,'unresolved':unresolved,'proposal':proposal}
    out=Path(os.getenv('YGO_OCG_PARTIAL_RESIDUAL_VARIANT_CALIBRATION_OUTPUT','/tmp/ygo-ocg-partial-residual-variant-calibration-v1.json'))
    out.parent.mkdir(parents=True,exist_ok=True); out.write_text(json.dumps(payload,ensure_ascii=False,indent=2,sort_keys=True,default=str)+'\n',encoding='utf-8'); print(json.dumps(payload,ensure_ascii=False,indent=2,sort_keys=True,default=str)); return 0

if __name__=='__main__': raise SystemExit(main())
