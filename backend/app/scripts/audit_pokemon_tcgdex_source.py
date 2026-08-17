from __future__ import annotations

import json
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

import requests


BASE_URL = "https://api.tcgdex.net/v2/en"
TIMEOUT = 25
MAX_WORKERS = 8


def _get_json(session: requests.Session, path: str, *, attempts: int = 4):
    url = f"{BASE_URL}/{path.lstrip('/')}"
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            response = session.get(url, timeout=TIMEOUT)
            if response.status_code == 429:
                time.sleep(1.0 + attempt * 1.5)
                continue
            response.raise_for_status()
            return response.json()
        except Exception as exc:  # network evidence is reported, never hidden
            last_error = exc
            if attempt + 1 < attempts:
                time.sleep(0.5 * (2**attempt))
    raise RuntimeError(f"TCGdex request failed after {attempts} attempts: {url}: {last_error}")


def _audit_set(set_summary: dict) -> dict:
    set_id = str(set_summary.get("id") or "").strip()
    if not set_id:
        return {"set_id": None, "error": "missing_set_id", "cards": []}

    session = requests.Session()
    session.headers.update({"User-Agent": "dontripit-catalog-audit/2.0", "Accept": "application/json"})
    try:
        detail = _get_json(session, f"sets/{set_id}")
    finally:
        session.close()

    cards = detail.get("cards") if isinstance(detail, dict) else None
    cards = cards if isinstance(cards, list) else []
    declared = detail.get("cardCount") if isinstance(detail, dict) else None
    if not isinstance(declared, dict):
        declared = {}

    return {
        "set_id": set_id,
        "set_name": detail.get("name") or set_summary.get("name"),
        "series": (detail.get("serie") or {}).get("name") if isinstance(detail.get("serie"), dict) else None,
        "declared_total": declared.get("total"),
        "declared_official": declared.get("official"),
        "cards_returned": len(cards),
        "cards": cards,
    }


def run() -> dict:
    session = requests.Session()
    session.headers.update({"User-Agent": "dontripit-catalog-audit/2.0", "Accept": "application/json"})
    try:
        sets_payload = _get_json(session, "sets")
    finally:
        session.close()

    if not isinstance(sets_payload, list) or not sets_payload:
        raise AssertionError(f"TCGdex /sets returned an unexpected payload: {type(sets_payload)!r}")

    set_ids = [str(item.get("id") or "").strip() for item in sets_payload if isinstance(item, dict)]
    duplicate_set_ids = sorted([value for value, count in Counter(set_ids).items() if value and count > 1])
    missing_set_ids = sum(1 for value in set_ids if not value)

    audited_sets: list[dict] = []
    errors: list[dict] = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(_audit_set, item): item for item in sets_payload if isinstance(item, dict)}
        for future in as_completed(futures):
            source = futures[future]
            try:
                audited_sets.append(future.result())
            except Exception as exc:
                errors.append({
                    "set_id": source.get("id"),
                    "set_name": source.get("name"),
                    "error": str(exc),
                })

    audited_sets.sort(key=lambda row: str(row.get("set_id") or ""))

    cards: list[dict] = []
    for set_row in audited_sets:
        set_id = set_row.get("set_id")
        set_name = set_row.get("set_name")
        for card in set_row.get("cards") or []:
            if not isinstance(card, dict):
                continue
            cards.append({
                "set_id": set_id,
                "set_name": set_name,
                "id": str(card.get("id") or "").strip(),
                "local_id": str(card.get("localId") or "").strip(),
                "name": str(card.get("name") or "").strip(),
                "image": card.get("image"),
            })

    card_ids = [row["id"] for row in cards]
    duplicate_card_ids = sorted([value for value, count in Counter(card_ids).items() if value and count > 1])
    missing_card_ids = [row for row in cards if not row["id"]]
    missing_local_ids = [row for row in cards if not row["local_id"]]
    missing_names = [row for row in cards if not row["name"]]
    missing_images = [row for row in cards if not row["image"]]

    count_mismatches = []
    for row in audited_sets:
        declared = row.get("declared_total")
        returned = int(row.get("cards_returned") or 0)
        if isinstance(declared, int) and declared != returned:
            count_mismatches.append({
                "set_id": row.get("set_id"),
                "set_name": row.get("set_name"),
                "declared_total": declared,
                "cards_returned": returned,
            })

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": "TCGdex REST v2 / en",
        "base_url": BASE_URL,
        "status": "pass" if not errors and not duplicate_set_ids and not duplicate_card_ids else "fail",
        "summary": {
            "sets_listed": len(sets_payload),
            "sets_audited": len(audited_sets),
            "cards_returned": len(cards),
            "unique_card_ids": len(set(card_ids) - {""}),
            "missing_set_ids": missing_set_ids,
            "duplicate_set_ids": len(duplicate_set_ids),
            "duplicate_card_ids": len(duplicate_card_ids),
            "missing_card_ids": len(missing_card_ids),
            "missing_local_ids": len(missing_local_ids),
            "missing_names": len(missing_names),
            "missing_images": len(missing_images),
            "set_count_mismatches": len(count_mismatches),
            "request_errors": len(errors),
        },
        "duplicate_set_ids": duplicate_set_ids[:100],
        "duplicate_card_ids": duplicate_card_ids[:100],
        "count_mismatches": count_mismatches[:100],
        "request_errors": errors,
        "missing_image_samples": missing_images[:50],
        "missing_local_id_samples": missing_local_ids[:50],
        "sets": [
            {
                "set_id": row.get("set_id"),
                "set_name": row.get("set_name"),
                "series": row.get("series"),
                "declared_total": row.get("declared_total"),
                "declared_official": row.get("declared_official"),
                "cards_returned": row.get("cards_returned"),
            }
            for row in audited_sets
        ],
    }

    print(json.dumps(report, ensure_ascii=False, indent=2))

    if errors:
        raise AssertionError(f"TCGdex source audit had {len(errors)} set request errors")
    if duplicate_set_ids:
        raise AssertionError(f"TCGdex source audit found duplicate set IDs: {duplicate_set_ids[:10]}")
    if duplicate_card_ids:
        raise AssertionError(f"TCGdex source audit found duplicate card IDs: {duplicate_card_ids[:10]}")

    return report


def main() -> int:
    run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
