#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, os
from collections import Counter, defaultdict
from pathlib import Path
import psycopg2
from psycopg2.extras import RealDictCursor


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--report',type=Path,required=True)
    ap.add_argument('--sample-limit',type=int,default=50)
    args=ap.parse_args()
    url=os.getenv('DATABASE_URL_UNPOOLED') or os.getenv('DATABASE_URL')
    if not url: raise SystemExit('database url required')
    conn=psycopg2.connect(url); conn.autocommit=False
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT id FROM games WHERE slug='yugioh'")
            gid=int(cur.fetchone()['id'])
            cur.execute('''
              SELECT ecp.id external_product_id, ecp.external_id, ecp.expansion_external_id, ecp.name,
                     l.print_id, p.collector_number, p.rarity, p.variant, p.language, s.code set_code
              FROM external_catalog_products ecp
              JOIN external_catalog_print_links l ON l.external_product_id=ecp.id
              JOIN prints p ON p.id=l.print_id
              JOIN sets s ON s.id=p.set_id
              WHERE ecp.source='cardmarket' AND ecp.game_id=%s AND ecp.product_group='single'
                AND l.confidence='exact' AND l.link_status IN ('accepted','mapped')
              ORDER BY ecp.id,l.print_id
            ''',(gid,))
            rows=[dict(r) for r in cur.fetchall()]
        by=defaultdict(list)
        for r in rows: by[int(r['external_product_id'])].append(r)
        counts=Counter(); target_hist=Counter(); samples=[]
        for pk,rs in by.items():
            rar={str(r.get('rarity') or '') for r in rs}
            var={str(r.get('variant') or '') for r in rs}
            col={str(r.get('collector_number') or '') for r in rs}
            sets={str(r.get('set_code') or '') for r in rs}
            langs={str(r.get('language') or '') for r in rs}
            target_hist[len(rs)]+=1
            counts['exact_products']+=1
            counts['exact_target_links']+=len(rs)
            if len(rs)>1: counts['multi_target_products']+=1
            if len(rar)==1: counts['single_rarity_products']+=1
            else: counts['multi_rarity_products']+=1
            if len(var)==1: counts['single_variant_products']+=1
            else: counts['multi_variant_products']+=1
            if len(col)==1: counts['single_collector_products']+=1
            else: counts['multi_collector_products']+=1
            if len(sets)==1: counts['single_set_code_products']+=1
            else: counts['multi_set_code_products']+=1
            if len(langs)>1: counts['multi_language_products']+=1
            if len(samples)<args.sample_limit and len(rs)>1:
                samples.append({'external_product_id':pk,'idProduct':str(rs[0]['external_id']),'name':rs[0]['name'],'expansion':str(rs[0].get('expansion_external_id') or ''),'rarities':sorted(rar),'variants':sorted(var),'collectors':sorted(col),'set_codes':sorted(sets),'languages':sorted(langs),'target_count':len(rs),'print_ids':[int(r['print_id']) for r in rs]})
        payload={'mode':'read_only','game':'yugioh','summary':dict(counts),'target_count_histogram':dict(sorted(target_hist.items())),'samples':samples}
        args.report.write_text(json.dumps(payload,ensure_ascii=False,indent=2,sort_keys=True)+'\n',encoding='utf-8')
        print('YGO_EXACT_PRODUCT_DIMENSIONS='+json.dumps({'summary':payload['summary'],'target_count_histogram':payload['target_count_histogram']},separators=(',',':')))
        conn.rollback()
    finally: conn.close()
    return 0
if __name__=='__main__': raise SystemExit(main())
