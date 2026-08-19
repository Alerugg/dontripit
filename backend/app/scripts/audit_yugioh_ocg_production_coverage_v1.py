from __future__ import annotations

import json
import os
from pathlib import Path

import psycopg2
from psycopg2.extras import RealDictCursor

GAME='yugioh'
ACCEPTED=('accepted','mapped','exact')
CERTIFIED_PREFIX='cardmarket_ocg_certified_unique_physical_'
EXPECTED_JA=36426
EXPECTED_JA_SETS=797


def main()->int:
    url=os.getenv('DATABASE_URL_UNPOOLED') or os.getenv('DATABASE_URL')
    if not url:
        raise RuntimeError('DATABASE URL required')
    conn=psycopg2.connect(url,connect_timeout=30,application_name='dontripit_ygo_ocg_production_coverage_v1')
    conn.set_session(readonly=True,autocommit=False)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT id FROM games WHERE slug=%s LIMIT 1",(GAME,))
            gid=int(cur.fetchone()['id'])
            cur.execute("""SELECT count(*) prints,count(DISTINCT s.id) sets
                FROM prints p JOIN cards c ON c.id=p.card_id JOIN sets s ON s.id=p.set_id
                WHERE c.game_id=%s AND lower(coalesce(p.language,''))='ja'""",(gid,))
            base=cur.fetchone(); ja=int(base['prints']); ja_sets=int(base['sets'])
            if (ja,ja_sets)!=(EXPECTED_JA,EXPECTED_JA_SETS):
                raise RuntimeError({'ja_baseline_drift':{'prints':ja,'sets':ja_sets}})

            cur.execute("""SELECT count(*) links,count(DISTINCT l.external_product_id) products,count(DISTINCT l.print_id) prints
                FROM external_catalog_print_links l
                JOIN external_catalog_products e ON e.id=l.external_product_id
                JOIN prints p ON p.id=l.print_id JOIN cards c ON c.id=p.card_id
                WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single'
                  AND l.link_status=ANY(%s) AND c.game_id=%s AND lower(coalesce(p.language,''))='ja'""",(gid,list(ACCEPTED),gid))
            x=cur.fetchone(); accepted={'links':int(x['links']),'products':int(x['products']),'prints':int(x['prints'])}

            cur.execute("""SELECT l.mapping_method,count(*) links,count(DISTINCT l.external_product_id) products,count(DISTINCT l.print_id) prints
                FROM external_catalog_print_links l
                JOIN external_catalog_products e ON e.id=l.external_product_id
                JOIN prints p ON p.id=l.print_id JOIN cards c ON c.id=p.card_id
                WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single'
                  AND l.link_status=ANY(%s) AND c.game_id=%s AND lower(coalesce(p.language,''))='ja'
                GROUP BY l.mapping_method ORDER BY count(*) DESC,l.mapping_method""",(gid,list(ACCEPTED),gid))
            methods=[{'mapping_method':str(r['mapping_method'] or ''),'links':int(r['links']),'products':int(r['products']),'prints':int(r['prints'])} for r in cur.fetchall()]
            certified=[r for r in methods if r['mapping_method'].startswith(CERTIFIED_PREFIX)]
            certified_totals={'links':sum(r['links'] for r in certified),'products':sum(r['products'] for r in certified),'prints':sum(r['prints'] for r in certified)}

            cur.execute("""SELECT count(*) n FROM (
                SELECT l.print_id FROM external_catalog_print_links l
                JOIN external_catalog_products e ON e.id=l.external_product_id
                JOIN prints p ON p.id=l.print_id JOIN cards c ON c.id=p.card_id
                WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single' AND l.link_status=ANY(%s)
                  AND c.game_id=%s AND lower(coalesce(p.language,''))='ja'
                GROUP BY l.print_id HAVING count(DISTINCT l.external_product_id)>1
            ) q""",(gid,list(ACCEPTED),gid)); print_conflicts=int(cur.fetchone()['n'])
            cur.execute("""SELECT count(*) n FROM (
                SELECT l.external_product_id FROM external_catalog_print_links l
                JOIN external_catalog_products e ON e.id=l.external_product_id
                JOIN prints p ON p.id=l.print_id JOIN cards c ON c.id=p.card_id
                WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single' AND l.link_status=ANY(%s)
                  AND c.game_id=%s AND lower(coalesce(p.language,''))='ja'
                GROUP BY l.external_product_id HAVING count(DISTINCT l.print_id)>1
            ) q""",(gid,list(ACCEPTED),gid)); product_conflicts=int(cur.fetchone()['n'])

            cur.execute("""WITH ja AS (
                SELECT s.id set_id,s.code,count(*) ja_prints
                FROM prints p JOIN cards c ON c.id=p.card_id JOIN sets s ON s.id=p.set_id
                WHERE c.game_id=%s AND lower(coalesce(p.language,''))='ja'
                GROUP BY s.id,s.code
            ), mapped AS (
                SELECT p.set_id,count(DISTINCT p.id) mapped_prints,
                       count(DISTINCT p.id) FILTER (WHERE l.mapping_method LIKE %s) certified_prints
                FROM external_catalog_print_links l
                JOIN external_catalog_products e ON e.id=l.external_product_id
                JOIN prints p ON p.id=l.print_id JOIN cards c ON c.id=p.card_id
                WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single' AND l.link_status=ANY(%s)
                  AND c.game_id=%s AND lower(coalesce(p.language,''))='ja'
                GROUP BY p.set_id
            )
            SELECT ja.set_id,ja.code,ja.ja_prints,coalesce(mapped.mapped_prints,0) mapped_prints,
                   coalesce(mapped.certified_prints,0) certified_prints
            FROM ja LEFT JOIN mapped ON mapped.set_id=ja.set_id
            ORDER BY ja.ja_prints DESC,ja.code""",(gid,CERTIFIED_PREFIX+'%',gid,list(ACCEPTED),gid))
            per=[]
            for r in cur.fetchall():
                total=int(r['ja_prints']); mapped=int(r['mapped_prints']); cert=int(r['certified_prints'])
                per.append({'set_id':int(r['set_id']),'set_code':str(r['code'] or ''),'ja_prints':total,'mapped_prints':mapped,'certified_ocg_prints':cert,'unmapped_prints':total-mapped,'coverage':round(mapped/total,6) if total else 0.0})
            sets_with_any=sum(r['mapped_prints']>0 for r in per)
            fully_mapped=sum(r['mapped_prints']==r['ja_prints'] for r in per)
            zero_mapped=sum(r['mapped_prints']==0 for r in per)
            top_unmapped=sorted(per,key=lambda r:(r['unmapped_prints'],r['ja_prints']),reverse=True)[:40]
            top_partial=sorted((r for r in per if r['mapped_prints']>0 and r['unmapped_prints']>0),key=lambda r:r['unmapped_prints'],reverse=True)[:40]
            conn.rollback()
    finally:
        conn.close()

    if certified_totals['links']!=certified_totals['products'] or certified_totals['links']!=certified_totals['prints']:
        raise RuntimeError({'certified_not_one_to_one':certified_totals})
    report={
        'status':'pass',
        'production_writes':0,
        'ja_physical_prints':ja,
        'ja_sets':ja_sets,
        'accepted_cardmarket_ja':accepted,
        'certified_ocg':certified_totals,
        'certified_methods':certified,
        'all_ja_mapping_methods':methods,
        'identity_conflicts':{'prints_with_multiple_products':print_conflicts,'products_with_multiple_prints':product_conflicts},
        'coverage':{
            'mapped_ja_prints':accepted['prints'],
            'unmapped_ja_prints':ja-accepted['prints'],
            'mapped_fraction':round(accepted['prints']/ja,6),
            'sets_with_any_mapping':sets_with_any,
            'fully_mapped_sets':fully_mapped,
            'zero_mapped_sets':zero_mapped,
        },
        'top_unmapped_sets':top_unmapped,
        'top_partial_sets':top_partial,
    }
    out=Path(os.getenv('YGO_OCG_PRODUCTION_COVERAGE_OUTPUT','/tmp/yugioh-ocg-production-coverage-v1.json'))
    out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(json.dumps(report,ensure_ascii=False,indent=2,sort_keys=True)+'\n',encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2,sort_keys=True))
    return 0


if __name__=='__main__':
    raise SystemExit(main())
