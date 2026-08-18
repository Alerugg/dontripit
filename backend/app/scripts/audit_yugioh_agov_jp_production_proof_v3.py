from __future__ import annotations

import json
import os
from pathlib import Path

from app.scripts import audit_yugioh_agov_jp_production_proof_v1 as v1


EXPECTED_LINKS = 95
EXPECTED_METHODS = {
    "cardmarket_ocg_certified_unique_physical_v1": 59,
    "cardmarket_ocg_certified_image_bijection_v1": 30,
    "cardmarket_ocg_certified_name_image_bijection_v1": 3,
    "cardmarket_ocg_certified_name_singleton_v1": 3,
}


def main() -> int:
    v1.EXPECTED_LINKS = EXPECTED_LINKS
    report, code = v1.run()
    failures = list(report.get("failures") or [])
    methods = report.get("mapping_methods") or {}
    for method, expected in EXPECTED_METHODS.items():
        actual = int(methods.get(method, 0) or 0)
        if actual != expected:
            failures.append(f"mapping_method_{method}_expected_{expected}_got_{actual}")
    unexpected = {k: int(v or 0) for k, v in methods.items() if k not in EXPECTED_METHODS and int(v or 0) > 0}
    if unexpected:
        failures.append(f"unexpected_mapping_methods_{unexpected}")

    report["proof_version"] = 3
    report["expected_mapping_methods"] = EXPECTED_METHODS
    report["failures"] = failures
    report["status"] = "pass" if not failures else "fail"
    report["production_writes"] = 0

    output = os.getenv("YGO_AGOV_JP_PRODUCTION_PROOF_V3_OUTPUT", "/tmp/yugioh-agov-jp-production-proof-v3.json")
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n"
    Path(output).write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if not failures and code == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
