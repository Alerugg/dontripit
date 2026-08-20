from __future__ import annotations

import argparse
import json
from pathlib import Path

from app import db
from app.search_v2.exhaustive_name_query import exhaustive_name_page
from app.search_v2.normalization import normalize_search_text


CASES = (
    {"query": "Pikachu", "game": "pokemon", "expected_cards": 191, "expected_prints": 376},
    {"query": "Luffy", "game": "onepiece", "expected_cards": 87, "expected_prints": 418},
)
PAGE_SIZE = 50


def _audit_case(session, case: dict) -> dict:
    query = case["query"]
    game = case["game"]
    expected_cards = int(case["expected_cards"])
    expected_prints = int(case["expected_prints"])
    query_norm = normalize_search_text(query)

    first = exhaustive_name_page(
        session,
        query=query,
        game=game,
        limit=PAGE_SIZE,
        offset=0,
    )
    assert first["total"] == expected_cards, (case, first["total"])
    assert first["total_prints"] == expected_prints, (case, first["total_prints"])

    items: list[dict] = []
    offsets: list[int] = []
    offset = 0
    while True:
        page = exhaustive_name_page(
            session,
            query=query,
            game=game,
            limit=PAGE_SIZE,
            offset=offset,
        )
        assert page["total"] == expected_cards, (case, offset, page["total"])
        assert page["total_prints"] == expected_prints, (case, offset, page["total_prints"])
        assert page["offset"] == offset
        assert page["limit"] == PAGE_SIZE
        offsets.append(offset)
        items.extend(page["items"])
        if page["next_offset"] is None:
            assert page["has_more"] is False
            break
        assert page["has_more"] is True
        assert int(page["next_offset"]) == offset + len(page["items"])
        assert page["items"], (case, offset)
        offset = int(page["next_offset"])
        assert offset <= expected_cards, (case, offset)

    card_ids = [int(item["card_id"]) for item in items]
    assert len(items) == expected_cards, (case, len(items))
    assert len(set(card_ids)) == expected_cards, (case, "duplicate_card_ids")
    assert all(query_norm in normalize_search_text(item["name"]) for item in items), case
    assert sum(int(item.get("variant_count") or 0) for item in items) == expected_prints, (
        case,
        "variant_count_sum",
        sum(int(item.get("variant_count") or 0) for item in items),
    )

    repeat = exhaustive_name_page(
        session,
        query=query,
        game=game,
        limit=PAGE_SIZE,
        offset=0,
    )
    assert [item["card_id"] for item in repeat["items"]] == [
        item["card_id"] for item in first["items"]
    ], (case, "unstable_page_zero")

    expected_offsets = list(range(0, expected_cards, PAGE_SIZE))
    assert offsets == expected_offsets, (case, offsets, expected_offsets)

    return {
        "query": query,
        "game": game,
        "total_cards": expected_cards,
        "total_prints": expected_prints,
        "page_size": PAGE_SIZE,
        "offsets": offsets,
        "unique_card_ids": len(set(card_ids)),
        "variant_count_sum": sum(int(item.get("variant_count") or 0) for item in items),
        "all_names_contain_query": True,
        "stable_page_zero": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", required=True)
    args = parser.parse_args()

    results: list[dict] = []
    with db.SessionLocal() as session:
        for case in CASES:
            results.append(_audit_case(session, case))

    report = {
        "status": "pass",
        "production_writes": 0,
        "pagination_mode": "canonical_name",
        "cases": results,
    }
    target = Path(args.report)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
