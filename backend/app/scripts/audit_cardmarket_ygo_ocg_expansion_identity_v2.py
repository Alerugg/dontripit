from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.scripts.audit_cardmarket_regional_expansion_identity_v1 import (
    RegionalAnchorSet,
    _download_singles,
    certify_anchor_set,
)


ANCHORS = {
    "alin_jp": RegionalAnchorSet(
        key="yugioh_alin_jp",
        game_slug="yugioh",
        expansion_code="ALIN-JP",
        region="ocg_japan",
        official_expansion_url="https://www.cardmarket.com/en/YuGiOh/Products/Singles/Alliance-Insight-OCG",
        anchors=(
            "Materiactor Meltthrough",
            "World of Spirits",
            "NT8000 - SIRIUS",
        ),
        min_confirmations=2,
    ),
    "pote_jp": RegionalAnchorSet(
        key="yugioh_pote_jp",
        game_slug="yugioh",
        expansion_code="POTE-JP",
        region="ocg_japan",
        official_expansion_url="https://www.cardmarket.com/en/YuGiOh/Products/Singles/Power-of-the-Elements-OCG",
        anchors=(
            "Eka the Flame Buddy",
            "Propa Gandake",
            "Vernusylph of the Flowering Fields",
        ),
        min_confirmations=2,
    ),
}


def main() -> int:
    parser = argparse.ArgumentParser(description="READ ONLY certify additional Yu-Gi-Oh OCG Cardmarket idExpansion identities")
    parser.add_argument("--key", required=True, choices=sorted(ANCHORS))
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--require-certified", action="store_true")
    args = parser.parse_args()

    anchor_set = ANCHORS[args.key]
    rows = _download_singles("yugioh")
    result = certify_anchor_set(anchor_set, rows)
    payload = {
        "source": "cardmarket",
        "mode": "read_only",
        "method": "official_product_catalog_candidate_plus_first_party_cardmarket_image_s3_binary_signature",
        "production_writes": 0,
        "result": result,
    }
    rendered = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(rendered, encoding="utf-8")
    print(rendered, end="")

    if result["status"] == "conflict":
        return 2
    if args.require_certified and result["status"] != "certified":
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
