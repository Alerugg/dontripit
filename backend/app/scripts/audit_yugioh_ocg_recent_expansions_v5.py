from __future__ import annotations

import json
import time
from pathlib import Path

from app.scripts.audit_cardmarket_regional_expansion_identity_v1 import _download_singles, image_url, probe_image

TARGETS = {
    "ETCO": ("4563", "3009"),
    "IGAS": ("4570", "2854"),
    "CHIM": ("4577", "2659"),
    "RIRA": ("4583", "2466"),
}


def probe_candidate(rows, set_code: str, expansion_id: str) -> dict:
    products=[r for r in rows if str(r.expansion_id or "")==expansion_id]
    distinct=[]; seen=set()
    for row in products:
        meta=str(row.metacard_id or "")
        if meta and meta in seen: continue
        seen.add(meta); distinct.append(row)
        if len(distinct)>=2: break
    probes=[]
    for row in distinct:
        url=image_url(row,f"{set_code}-JP"); result=probe_image(url,attempts=1)
        probes.append({"idProduct":str(row.product_id),"name":row.name,"idMetacard":str(row.metacard_id or ""),"idExpansion":expansion_id,"image_url":url,**result})
        if result.get("status")==403: break
        time.sleep(.75)
    positives=sum(bool(p.get("image")) for p in probes); any403=any(p.get("status")==403 for p in probes)
    return {"idExpansion":expansion_id,"products":len(products),"positive_images":positives,"status":"certified" if positives>=2 else ("inconclusive" if any403 else "not_certified"),"probes":probes}


def main()->int:
    rows=_download_singles("yugioh"); reports=[]
    for set_code,candidates in TARGETS.items():
        tried=[]; certified=None
        for expansion_id in candidates:
            r=probe_candidate(rows,set_code,expansion_id); tried.append(r)
            if r["status"]=="certified": certified=expansion_id; break
            if r["status"]=="inconclusive": break
            time.sleep(1)
        reports.append({"set_code":set_code,"candidate_expansion_code":f"{set_code}-JP","candidate_ids":list(candidates),"certified_idExpansion":certified,"status":"certified" if certified else "inconclusive","candidates":tried})
        time.sleep(1.5)
    payload={"source":"cardmarket","mode":"read_only","method":"global_surface_candidate_plus_first_party_cardmarket_image_s3_binary_signature","production_writes":0,"certified":sum(r["status"]=="certified" for r in reports),"inconclusive":sum(r["status"]=="inconclusive" for r in reports),"results":reports}
    Path('/tmp/yugioh-ocg-recent-expansions-v5.json').write_text(json.dumps(payload,indent=2,sort_keys=True)+'\n',encoding='utf-8'); print(json.dumps(payload,indent=2,sort_keys=True)); return 0

if __name__=='__main__': raise SystemExit(main())
