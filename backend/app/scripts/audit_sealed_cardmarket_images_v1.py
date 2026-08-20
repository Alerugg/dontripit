from __future__ import annotations
import argparse, hashlib, html, io, json, re, urllib.error, urllib.parse, urllib.request
from collections import Counter, defaultdict
from pathlib import Path
from PIL import Image
from sqlalchemy import text
from app import db

ORIGIN='https://www.cardmarket.com'
HOST='product-images.s3.cardmarket.com'
UA='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36'
CONTROL='https://product-images.s3.cardmarket.com/5/DUAD-JP/823714/823714.jpg'
URL_RE=re.compile(r'https?://product-images\.s3\.cardmarket\.com[^\s\"\'<>]+',re.I)

def get(url,accept,timeout=30):
    req=urllib.request.Request(url,headers={'User-Agent':UA,'Accept':accept,'Accept-Language':'en-US,en;q=0.9','Referer':'https://www.cardmarket.com/','Cache-Control':'no-cache'})
    try:
        with urllib.request.urlopen(req,timeout=timeout) as r:
            body=r.read(); return body,{'status':int(getattr(r,'status',200) or 200),'content_type':r.headers.get('Content-Type'),'bytes':len(body),'final_url':r.geturl(),'sha256':hashlib.sha256(body).hexdigest()}
    except urllib.error.HTTPError as e:
        return None,{'status':int(e.code),'error':f'HTTPError: {e.code}','final_url':e.geturl()}
    except Exception as e:
        return None,{'status':None,'error':f'{type(e).__name__}: {e}'}

def image_meta(body,meta):
    out=dict(meta)
    if not body: return False,out
    try:
        with Image.open(io.BytesIO(body)) as im: im.verify()
        with Image.open(io.BytesIO(body)) as im: w,h=im.size; fmt=im.format
        out.update(width=w,height=h,format=fmt)
        ct=str(out.get('content_type') or '').lower()
        return bool(w>=80 and h>=80 and (not ct or 'image' in ct)),out
    except Exception as e:
        out['decode_error']=f'{type(e).__name__}: {e}'; return False,out

def candidates(page,pid):
    txt=html.unescape(page.decode('utf-8','replace')).replace('\\/','/')
    found=[]
    for raw in URL_RE.findall(txt):
        u=raw.rstrip('),;]'); p=urllib.parse.urlparse(u)
        if p.hostname!=HOST: continue
        parts=[x for x in p.path.split('/') if x]
        if pid not in parts or not parts: continue
        if not re.fullmatch(rf'{re.escape(pid)}\.(?:jpe?g|png|webp|avif)',parts[-1],re.I): continue
        found.append(urllib.parse.urlunparse(('https',p.netloc,p.path,'',p.query,'')))
    return sorted(set(found))

def probe(row):
    pid=str(row.get('external_id') or '').strip(); path=str(row.get('website_path') or '').strip()
    keep=('variant_id','game','product_name','product_type','set_code','language','region','packaging','external_id','external_name','category_id','category','website_path','mapping_method')
    out={k:row.get(k) for k in keep}; out['id_product']=pid
    if not pid or not path: out.update(probe='missing_exact_page_path',valid_image=False); return out
    page_url=f'{ORIGIN}/en{path}' if path.startswith('/') else f'{ORIGIN}/en/{path}'
    out['page_url']=page_url
    page,meta=get(page_url,'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8'); out['page']=meta
    if not page:
        out.update(probe='page_blocked' if meta.get('status') in {401,403,429} else 'page_error',valid_image=False); return out
    q=urllib.parse.parse_qs(urllib.parse.urlparse(str(meta.get('final_url') or '')).query)
    final_pid=(q.get('idProduct') or [None])[0]
    if final_pid is not None and str(final_pid)!=pid:
        out.update(probe='redirect_mismatch',valid_image=False,redirect_id_product=final_pid); return out
    urls=candidates(page,pid); out['exact_candidate_urls']=urls; out['exact_candidate_count']=len(urls)
    if not urls: out.update(probe='no_exact_image_candidate',valid_image=False); return out
    checks=[]
    for u in urls[:4]:
        body,m=get(u,'image/avif,image/webp,image/png,image/jpeg,image/*;q=0.8,*/*;q=0.1'); ok,m=image_meta(body,m); checks.append({'url':u,'valid':ok,**m})
    out['candidate_verification']=checks; good=[x for x in checks if x['valid']]
    if not good:
        statuses={x.get('status') for x in checks}; out.update(probe='image_blocked' if statuses & {401,403,429} else 'image_invalid',valid_image=False); return out
    hashes={str(x.get('sha256') or '') for x in good}
    if len(hashes)!=1: out.update(probe='multiple_distinct_exact_images',valid_image=False); return out
    win=good[0]; out.update(probe='resolved',valid_image=True,image_url=win['url'],image_sha256=win.get('sha256'),image_width=win.get('width'),image_height=win.get('height'),image_format=win.get('format')); return out

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--report',type=Path,required=True); ap.add_argument('--sample-per-type',type=int,default=2); a=ap.parse_args()
    if not 1<=a.sample_per_type<=5: raise SystemExit('--sample-per-type must be 1..5')
    b,m=get(CONTROL,'image/avif,image/webp,image/png,image/jpeg,image/*;q=0.8,*/*;q=0.1'); ok,m=image_meta(b,m); control={'url':CONTROL,'valid':ok,**m}
    sql=text('''
    WITH latest AS (
      SELECT game_id,MAX(last_seen_at) latest_seen FROM external_catalog_products
      WHERE source='cardmarket' AND product_group='non_single' GROUP BY game_id
    ), strict AS (
      SELECT l.product_variant_id,l.external_product_id,l.mapping_method,e.external_id,e.name external_name,e.category_id,e.category,e.website_path,
             COUNT(*) OVER(PARTITION BY l.external_product_id) variants_per_external,
             COUNT(*) OVER(PARTITION BY l.product_variant_id) externals_per_variant
      FROM external_catalog_product_variant_links l JOIN external_catalog_products e ON e.id=l.external_product_id
      JOIN latest x ON x.game_id=e.game_id AND x.latest_seen=e.last_seen_at
      WHERE e.source='cardmarket' AND e.product_group='non_single' AND l.link_status IN ('accepted','mapped','exact') AND l.confidence='exact' AND l.reviewed=TRUE
    ), eligible AS (
      SELECT s.product_variant_id variant_id,g.slug game,p.name product_name,p.product_type,st.code set_code,pv.language,pv.region,pv.packaging,
             s.external_id,s.external_name,s.category_id,s.category,s.website_path,s.mapping_method,
             ROW_NUMBER() OVER(PARTITION BY g.slug,p.product_type ORDER BY CASE WHEN COALESCE(s.website_path,'')='' THEN 1 ELSE 0 END,p.name,pv.id) rn
      FROM strict s JOIN product_variants pv ON pv.id=s.product_variant_id JOIN products p ON p.id=pv.product_id JOIN games g ON g.id=p.game_id LEFT JOIN sets st ON st.id=p.set_id
      WHERE s.variants_per_external=1 AND s.externals_per_variant=1 AND NOT EXISTS(SELECT 1 FROM product_images pi WHERE pi.product_variant_id=pv.id)
    ) SELECT * FROM eligible WHERE rn<=:n ORDER BY game,product_type,rn,variant_id
    ''')
    with db.SessionLocal() as s: rows=[dict(r) for r in s.execute(sql,{'n':a.sample_per_type}).mappings().all()]
    results=[probe(r) for r in rows]; counts=Counter(str(r['probe']) for r in results); by_game=defaultdict(Counter); by_type=defaultdict(Counter)
    for r in results: by_game[str(r['game'])][str(r['probe'])]+=1; by_type[f"{r['game']}|{r['product_type']}"][str(r['probe'])]+=1
    resolved=[r for r in results if r.get('valid_image')]; hashes=defaultdict(list)
    for r in resolved: hashes[str(r.get('image_sha256') or '')].append({k:r.get(k) for k in ('game','product_type','variant_id','id_product','product_name','image_url')})
    dup={h:v for h,v in hashes.items() if h and len({str(x['id_product']) for x in v})>1}
    report={'status':'pass','production_writes':0,'control_cardmarket_s3':control,'sample_per_game_product_type':a.sample_per_type,'sample_rows':len(rows),'probe_counts':dict(sorted(counts.items())),'resolved_exact_product_images':len(resolved),'resolved_unique_urls':len({str(r.get('image_url')) for r in resolved}),'resolved_unique_hashes':len({str(r.get('image_sha256')) for r in resolved}),'by_game':{g:dict(sorted(c.items())) for g,c in sorted(by_game.items())},'by_game_product_type':{k:dict(sorted(c.items())) for k,c in sorted(by_type.items())},'duplicate_hash_groups_across_distinct_products':dup,'results':results}
    a.report.parent.mkdir(parents=True,exist_ok=True); a.report.write_text(json.dumps(report,indent=2,sort_keys=True,default=str)+'\n',encoding='utf-8'); print(json.dumps(report,indent=2,sort_keys=True,default=str)); return 0
if __name__=='__main__': raise SystemExit(main())
