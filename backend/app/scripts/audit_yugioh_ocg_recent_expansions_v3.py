from __future__ import annotations

import json
import time
from pathlib import Path

from app.scripts.audit_cardmarket_regional_expansion_identity_v1 import (
    _download_singles,
    image_url,
    probe_image,
)

TARGETS = {
    "DABL": ("5107", "5083"),
    "DIFO": ("4519", "4475"),
    "BACH": ("4524", "4397"),
    "BODE": ("4528", "4370"),
}


def _probe_candidate(rows, set_code: str, expansion_id: str) -> dict:
    products = [r for r in rows if str(r.expansion_id or "") == expansion_id]
    distinct = []
    seen_meta = set()
    for row in products:
        meta = str(row.metacard_id or "")
        if meta and meta in seen_meta:
            continue
        seen_meta.add(meta)
        distinct.append(row)
        if len(distinct) >= 2:
            break
    probes = []
    for row in distinct:
        url = image_url(row, f"{set_code}-JP")
        result = probe_image(url, attempts=1)
        probes.append({"idProduct": str(row.product_id), "name": row.name, "idMetacard": str(row.metacard_id or ""), "idExpansion": expansion_id, "image_url": url, **result})
        if result.get("status") == 403:
            break
        time.sleep(0.75)
    positives = sum(bool(p.get("image")) for p in probes)
    any_403 = any(p.get("status") == 403 for p in probes)
    return {"idExpansion": expansion_id, "products": len(products), "probes": probes, "positive_images": positives, "status": "certified" if positives >= 2 else ("inconclusive" if any_403 else "not_certified")}


def main() -> int:
    rows = _download_singles("yugioh")
    reports = []
    for set_code, candidates in TARGETS.items():
        candidate_reports = []
        certified = None
        conflict = False
        for expansion_id in candidates:
            report = _probe_candidate(rows, set_code, expansion_id)
            candidate_reports.append(report)
            if report["status"] == "certified":
                if certified is not None and certified != expansion_id:
                    conflict = True
                certified = expansion_id
                break
            if report["status"] == "inconclusive":
                break
            time.sleep(1.0)
        reports.append({"set_code": set_code, "candidate_expansion_code": f"{set_code}-JP", "candidate_ids": list(candidates), "certified_idExpansion": certified, "status": "conflict" if conflict else ("certified" if certified else "inconclusive"), "candidates": candidate_reports})
        time.sleep(1.5)
    payload = {"source": "cardmarket", "mode": "read_only", "method": "global_surface_candidate_plus_first_party_cardmarket_image_s3_binary_signature", "production_writes": 0, "certified": sum(r["status"] == "certified" for r in reports), "conflicts": sum(r["status"] == "conflict" for r in reports), "inconclusive": sum(r["status"] == "inconclusive" for r in reports), "results": reports}
    output = Path("/tmp/yugioh-ocg-recent-expansions-v3.json")
    rendered = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 2 if payload["conflicts"] else 0


if __name__ == "__main__": raise SystemExit(main())
