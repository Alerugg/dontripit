from __future__ import annotations

import json,time
from pathlib import Path

from app.scripts.audit_cardmarket_regional_expansion_identity_v1 import _download_singles,image_url,probe_image

TARGETS={
    'AC02':'5081',
    'AC01':'4532',
    'DBSS':'4558',
    'DBMF':'4575',
}


def probe_candidate(rows,set_code,expansion_id):
    products=[r for r in rows if str(r.expansion_id or '')==expansion_id]
    distinct=[]; seen=set()
    for row in products:
        meta=str(row.metacard_id or '')
        if meta and meta in seen:
            continue
        seen.add(meta); distinct.append(row)
        if len(distinct)>=2:
            break
    probes=[]
    for row in distinct:
        url=image_url(row,f'{set_code}-JP')
        result=probe_image(url,attempts=2)
        probes.append({'idProduct':str(row.product_id),'name':row.name,'idMetacard':str(row.metacard_id or ''),'idExpansion':expansion_id,'image_url':url,**result})
        if result.get('status')==403:
            break
        time.sleep(.75)
    positives=sum(bool(p.get('image')) for p in probes)
    any403=any(p.get('status')==403 for p in probes)
    return {
        'idExpansion':expansion_id,
        'products':len(products),
        'positive_images':positives,
        'status':'certified' if positives>=2 else ('inconclusive' if any403 else 'not_certified'),
        'probes':probes,
    }


def main():
    rows=_download_singles('yugioh')
    reports=[]
    for code,exp in TARGETS.items():
        r=probe_candidate(rows,code,exp)
        reports.append({'set_code':code,'candidate_expansion_code':f'{code}-JP','candidate_idExpansion':exp,'certified_idExpansion':exp if r['status']=='certified' else None,'status':r['status'],'candidate':r})
        time.sleep(1.25)
    payload={
        'source':'cardmarket',
        'mode':'read_only',
        'method':'global_surface_perfect_candidate_plus_first_party_cardmarket_image_s3_binary_signature',
        'production_writes':0,
        'certified':sum(r['status']=='certified' for r in reports),
        'inconclusive':sum(r['status']=='inconclusive' for r in reports),
        'not_certified':sum(r['status']=='not_certified' for r in reports),
        'results':reports,
    }
    Path('/tmp/yugioh-ocg-modern-specials-v11.json').write_text(json.dumps(payload,indent=2,sort_keys=True)+'\n',encoding='utf-8')
    print(json.dumps(payload,indent=2,sort_keys=True))
    return 0


if __name__=='__main__':
    raise SystemExit(main())
