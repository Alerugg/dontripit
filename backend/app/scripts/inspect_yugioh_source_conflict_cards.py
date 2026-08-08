from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from app.ingest.connectors.ygoprodeck_yugioh import YgoProDeckYugiohConnector


CONFLICT_CARD_IDS = {
    "46358784", "72843899", "45236142", "71620241",
    "79086452", "88120966", "300302018", "300302053",
    "94820406", "53982768", "24508238", "28601770",
    "25366484", "78060096",
}


def _clean(value):
    return str(value or "").strip()


def _write(path: Path | None, payload: dict) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def run(*, report_path: Path | None = None) -> dict:
    cards = YgoProDeckYugiohConnector()._load_remote(limit=None, page_size=500)
    rows = []
    found = set()
    for card in cards:
        card_id = _clean(card.get("id"))
        if card_id not in CONFLICT_CARD_IDS:
            continue
        found.add(card_id)
        rows.append({
            "id": card_id,
            "name": card.get("name"),
            "type": card.get("type"),
            "humanReadableCardType": card.get("humanReadableCardType"),
            "frameType": card.get("frameType"),
            "race": card.get("race"),
            "attribute": card.get("attribute"),
            "level": card.get("level"),
            "atk": card.get("atk"),
            "def": card.get("def"),
            "desc": card.get("desc"),
            "typeline": card.get("typeline"),
            "archetype": card.get("archetype"),
            "card_sets": card.get("card_sets") or [],
            "card_images": [
                {
                    "id": image.get("id"),
                    "image_url": image.get("image_url"),
                    "image_url_small": image.get("image_url_small"),
                }
                for image in (card.get("card_images") or [])
            ],
            "misc_info": card.get("misc_info") or [],
        })
    missing = sorted(CONFLICT_CARD_IDS - found)
    if missing:
        raise AssertionError(f"Conflict Card IDs missing from source: {missing}")
    rows.sort(key=lambda row: (row["name"] or "", row["id"]))
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": "read_only_yugioh_source_conflict_card_payloads",
        "status": "pass",
        "count": len(rows),
        "cards": rows,
        "database_writes": 0,
    }
    _write(report_path, report)
    print(json.dumps({
        "status": "pass",
        "count": len(rows),
        "cards": [
            {
                "id": row["id"],
                "name": row["name"],
                "type": row["type"],
                "frameType": row["frameType"],
                "card_sets": len(row["card_sets"]),
                "card_images": len(row["card_images"]),
            }
            for row in rows
        ],
    }, ensure_ascii=False, indent=2))
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report-path", type=Path, default=None)
    args = parser.parse_args()
    run(report_path=args.report_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
