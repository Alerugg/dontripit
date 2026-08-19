from __future__ import annotations

import json
import os
from pathlib import Path

import psycopg2
from psycopg2.extras import RealDictCursor

from app.scripts.yugioh_ocg_frozen_version_contract_v1 import contract_payload
from app.scripts.yugioh_ocg_frozen_version_pairs_v1 import EXPECTED_PAIRS, derive


def main() -> int:
    url = os.getenv("DATABASE_URL_UNPOOLED") or os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE URL required")
    conn = psycopg2.connect(
        url,
        connect_timeout=30,
        application_name="dontripit_ygo_ocg_frozen_version_contract_audit_v1",
    )
    conn.set_session(readonly=True, autocommit=False)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            state = derive(cur)
            conn.rollback()
    finally:
        conn.close()

    payload = {
        "status": "pass",
        "mode": "read_only",
        "production_writes": 0,
        "cardmarket_capture": str(state["capture"]),
        "ja_baseline": state["ja_baseline"],
        "contract_sha256": state["contract_sha256"],
        "contract": contract_payload(),
        "candidate_pairs": len(state["pairs"]),
        "candidate_products": len({x["idProduct"] for x in state["pairs"]}),
        "candidate_prints": len({x["print_id"] for x in state["pairs"]}),
        "sets": state["sets"],
        "proposal": state["pairs"],
        "unsupported_groups": state["unsupported_groups"],
    }
    if payload["candidate_pairs"] != EXPECTED_PAIRS:
        raise RuntimeError({"expected_pairs": EXPECTED_PAIRS, "actual": payload["candidate_pairs"]})

    out = Path(
        os.getenv(
            "YGO_OCG_FROZEN_VERSION_CONTRACT_OUTPUT",
            "/tmp/yugioh-ocg-frozen-version-contract-v1.json",
        )
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
