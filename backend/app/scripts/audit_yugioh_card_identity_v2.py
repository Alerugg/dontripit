from __future__ import annotations

import argparse
import json
import re
import unicodedata
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from app.ingest.connectors.ygoprodeck_yugioh import YgoProDeckYugiohConnector


def _clean(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())


def _fold(value: object) -> str:
    text = unicodedata.normalize("NFKD", _clean(value))
    text = "".join(ch for ch in text if not unicodedata.combining(ch)).casefold()
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def _skill_alias_base(name: object) -> str:
    text = _clean(name)
    return re.sub(r"\s*\(skill card\)\s*$", "", text, flags=re.IGNORECASE).strip()


def _fingerprint(card: dict) -> dict:
    images = card.get("card_images") or []
    return {
        "id": str(card.get("id") or "").strip(),
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
        "image_ids": [str(row.get("id")) for row in images if row.get("id") is not None],
        "card_set_count": len(card.get("card_sets") or []),
        "card_sets": card.get("card_sets") or [],
        "misc_info": card.get("misc_info") or [],
    }


def _write(path: Path | None, payload: dict) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def run(*, report_path: Path | None = None) -> dict:
    cards = YgoProDeckYugiohConnector()._load_remote(limit=None, page_size=500)
    exact_groups: dict[str, list[dict]] = defaultdict(list)
    skill_alias_groups: dict[str, list[dict]] = defaultdict(list)

    for card in cards:
        fp = _fingerprint(card)
        name = _clean(card.get("name"))
        exact_groups[_fold(name)].append(fp)
        skill_alias_groups[_fold(_skill_alias_base(name))].append(fp)

    exact_duplicates = []
    for key, rows in exact_groups.items():
        distinct_names = {_clean(row["name"]) for row in rows}
        if len(rows) > 1:
            exact_duplicates.append({"normalized_name": key, "distinct_names": sorted(distinct_names), "cards": rows})
    exact_duplicates.sort(key=lambda row: (-len(row["cards"]), row["normalized_name"]))

    skill_alias_candidates = []
    for key, rows in skill_alias_groups.items():
        if len(rows) <= 1:
            continue
        names = {_clean(row["name"]) for row in rows}
        if not any(re.search(r"\(skill card\)\s*$", name, re.IGNORECASE) for name in names):
            continue
        skill_alias_candidates.append({"alias_base": key, "distinct_names": sorted(names), "cards": rows})
    skill_alias_candidates.sort(key=lambda row: (-len(row["cards"]), row["alias_base"]))

    # Exact semantic fingerprints are a high-confidence duplicate signal even if names differ.
    semantic_groups: dict[tuple, list[dict]] = defaultdict(list)
    for card in cards:
        fp = _fingerprint(card)
        semantic_key = (
            _fold(fp["desc"]),
            _fold(fp["type"]),
            _fold(fp["race"]),
            str(fp["attribute"] or ""),
            str(fp["level"] if fp["level"] is not None else ""),
            str(fp["atk"] if fp["atk"] is not None else ""),
            str(fp["def"] if fp["def"] is not None else ""),
        )
        if semantic_key[0]:
            semantic_groups[semantic_key].append(fp)
    semantic_duplicate_candidates = []
    for key, rows in semantic_groups.items():
        ids = {row["id"] for row in rows}
        names = {_clean(row["name"]) for row in rows}
        if len(ids) > 1 and len(names) > 1:
            # Keep only likely aliases: same text/type/stats but different source names.
            semantic_duplicate_candidates.append({
                "semantic_key": list(key),
                "distinct_names": sorted(names),
                "cards": rows,
            })
    semantic_duplicate_candidates.sort(key=lambda row: (-len(row["cards"]), str(row["distinct_names"])))

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": "read_only_yugioh_logical_card_identity_audit",
        "status": "review_required" if (exact_duplicates or skill_alias_candidates) else "pass",
        "source_card_ids": len(cards),
        "exact_normalized_name_duplicate_groups": len(exact_duplicates),
        "skill_card_alias_candidate_groups": len(skill_alias_candidates),
        "semantic_alias_candidate_groups": len(semantic_duplicate_candidates),
        "exact_normalized_name_duplicates": exact_duplicates,
        "skill_card_alias_candidates": skill_alias_candidates,
        "semantic_alias_candidates": semantic_duplicate_candidates[:250],
        "database_writes": 0,
    }
    _write(report_path, report)
    print(json.dumps({
        "status": report["status"],
        "source_card_ids": len(cards),
        "exact_normalized_name_duplicate_groups": len(exact_duplicates),
        "skill_card_alias_candidate_groups": len(skill_alias_candidates),
        "semantic_alias_candidate_groups": len(semantic_duplicate_candidates),
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
