from __future__ import annotations

import json
import os
from collections import defaultdict
from pathlib import Path

import psycopg2
from psycopg2.extras import RealDictCursor

ACCEPTED=("accepted","mapped","exact")

def main() -> int:
    url=os.getenv("DATABASE_URL_UNPOOLED") or os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL_UNPOOLED or DATABASE_URL is required")
    conn=psycopg2.connect(url,connect_timeout=30,application_name="dontripit_ygo_alin_unassigned_prints_v1")
    conn.set_session(readonly=True,autocommit=False)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT id FROM games WHERE slug='yugioh' LIMIT 1")
            game_id=int(cur.fetchone()["id"])
            cur.execute("SELECT max(last_seen_at) capture FROM external_catalog_products WHERE source='cardmarket'")
            capture=cur.fetchone()["capture"]
            cur.execute("""SELECT e.id external_product_id,e.external_id id_product,e.name,e.metacard_external_id
                           FROM external_catalog_products e
                           WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single'
                             AND e.expansion_external_id='6025' AND e.last_seen_at=%s
                           ORDER BY e.metacard_external_id,e.external_id::bigint""",(game_id,capture))
            products=[dict(r) for r in cur.fetchall()]
            cur.execute("""SELECT p.id print_id,p.card_id,p.collector_number,p.rarity,p.variant,p.language,c.name card_name
                           FROM prints p JOIN cards c ON c.id=p.card_id JOIN sets s ON s.id=p.set_id
                           WHERE c.game_id=%s AND lower(coalesce(p.language,''))='ja' AND upper(coalesce(s.code,''))='ALIN'
                           ORDER BY p.collector_number,p.id""",(game_id,))
            prints=[dict(r) for r in cur.fetchall()]
            cur.execute("""SELECT l.external_product_id,l.print_id,e.external_id id_product
                           FROM external_catalog_print_links l
                           JOIN external_catalog_products e ON e.id=l.external_product_id
                           WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single'
                             AND l.link_status=ANY(%s)""",(game_id,list(ACCEPTED)))
            links=[dict(r) for r in cur.fetchall()]
            conn.rollback()
    finally:
        conn.close()

    claimed_product_rows={int(r['external_product_id']) for r in links}
    claimed_print_ids={int(r['print_id']) for r in links}
    residual_products=[r for r in products if int(r['external_product_id']) not in claimed_product_rows]
    residual_prints=[r for r in prints if int(r['print_id']) not in claimed_print_ids]
    prints_by_name=defaultdict(list)
    for r in residual_prints:
        prints_by_name[str(r['card_name'])].append(r)
    products_by_meta=defaultdict(list)
    for r in residual_products:
        products_by_meta[str(r.get('metacard_external_id') or '')].append(r)

    consumed=set()
    unbalanced=[]
    for meta,group in sorted(products_by_meta.items(),key=lambda kv:min(int(x['id_product']) for x in kv[1])):
        names=sorted({str(x.get('name') or '') for x in group})
        candidates=prints_by_name.get(names[0],[]) if len(names)==1 else []
        if len(candidates)==len(group) and group:
            consumed.update(int(x['print_id']) for x in candidates)
        else:
            unbalanced.append({
                'idMetacard':meta,
                'product_names':names,
                'idProducts':[str(x['id_product']) for x in group],
                'product_count':len(group),
                'exact_name_candidate_prints':[
                    {'print_id':int(x['print_id']),'card_name':x['card_name'],'collector_number':x['collector_number'],'rarity':x['rarity'],'variant':x['variant']}
                    for x in candidates
                ],
            })

    unassigned=[r for r in residual_prints if int(r['print_id']) not in consumed]
    report={
        'status':'pass' if len(residual_products)==77 and len(residual_prints)==77 and len(unassigned)==7 and len(unbalanced)==3 else 'fail',
        'production_writes':0,
        'cardmarket_capture':str(capture),
        'residual_products':len(residual_products),
        'residual_prints':len(residual_prints),
        'exact_name_consumed_prints':len(consumed),
        'unbalanced_product_groups':unbalanced,
        'unassigned_residual_prints':[
            {'print_id':int(r['print_id']),'card_id':int(r['card_id']),'card_name':r['card_name'],'collector_number':r['collector_number'],'rarity':r['rarity'],'variant':r['variant']}
            for r in unassigned
        ],
    }
    if report['status']!='pass':
        report['failure']=f"expected 77/77 residual, 70 exact-name consumed, 3 groups, 7 unassigned; got products={len(residual_products)} prints={len(residual_prints)} consumed={len(consumed)} groups={len(unbalanced)} unassigned={len(unassigned)}"
    out=Path(os.getenv('YGO_ALIN_JP_UNASSIGNED_OUTPUT','/tmp/yugioh-alin-jp-unassigned-prints-v1.json'))
    text=json.dumps(report,ensure_ascii=False,indent=2,sort_keys=True,default=str)+'\n'
    out.write_text(text,encoding='utf-8')
    print(text,end='')
    return 0 if report['status']=='pass' else 2

if __name__=='__main__':
    raise SystemExit(main())
