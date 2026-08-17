from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass

import requests


TCGDEX_BASE = "https://api.tcgdex.net/v2/en"
POCKET_SERIES_NAME = "Pokémon TCG Pocket"
TIMEOUT = 30
MAX_WORKERS = 8


@dataclass(frozen=True)
class PokemonSourceInventory:
    sets: list[dict]
    cards: dict[str, dict]
    pocket_set_ids: set[str]
    unassigned_cards: list[dict]

    @property
    def physical_sets(self) -> list[dict]:
        return [row for row in self.sets if row.get("series") != POCKET_SERIES_NAME]

    @property
    def pocket_sets(self) -> list[dict]:
        return [row for row in self.sets if row.get("series") == POCKET_SERIES_NAME]

    @property
    def physical_cards(self) -> dict[str, dict]:
        pocket = self.pocket_set_ids
        return {key: value for key, value in self.cards.items() if value.get("set_id") not in pocket}

    @property
    def pocket_cards(self) -> dict[str, dict]:
        pocket = self.pocket_set_ids
        return {key: value for key, value in self.cards.items() if value.get("set_id") in pocket}


def request_json(session: requests.Session, path: str, *, attempts: int = 5):
    url = f"{TCGDEX_BASE}/{path.lstrip('/')}"
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            response = session.get(url, timeout=TIMEOUT)
            if response.status_code in {429, 500, 502, 503, 504}:
                time.sleep(0.75 * (attempt + 1))
                continue
            response.raise_for_status()
            return response.json()
        except Exception as exc:
            last_error = exc
            if attempt + 1 < attempts:
                time.sleep(0.5 * (2**attempt))
    raise RuntimeError(f"TCGdex request failed after {attempts} attempts: {url}: {last_error}")


def _normalized_abbreviation(value: object) -> str | None:
    if isinstance(value, str):
        clean = value.strip()
        return clean or None
    if isinstance(value, dict):
        # TCGdex uses objects such as {"official": "MCD11"} for many sets.
        # Prefer the official code, then other stable string values.
        for key in ("official", "tcgOnline", "ptcgo", "code"):
            clean = str(value.get(key) or "").strip()
            if clean:
                return clean
        for raw in value.values():
            if isinstance(raw, str) and raw.strip():
                return raw.strip()
    return None


def _set_detail(summary: dict) -> dict:
    set_id = str(summary.get("id") or "").strip()
    if not set_id:
        raise ValueError("TCGdex set without id")
    session = requests.Session()
    session.headers.update({"User-Agent": "dontripit-pokemon-inventory/2.0", "Accept": "application/json"})
    try:
        detail = request_json(session, f"sets/{set_id}")
    finally:
        session.close()

    serie = detail.get("serie") if isinstance(detail, dict) else None
    card_count = detail.get("cardCount") if isinstance(detail, dict) else None
    raw_abbreviation = (detail or {}).get("abbreviation")
    return {
        "set_id": set_id,
        "set_name": str((detail or {}).get("name") or summary.get("name") or "").strip(),
        "series": (serie or {}).get("name") if isinstance(serie, dict) else None,
        "series_id": (serie or {}).get("id") if isinstance(serie, dict) else None,
        "release_date": (detail or {}).get("releaseDate"),
        "declared_total": (card_count or {}).get("total") if isinstance(card_count, dict) else None,
        "declared_official": (card_count or {}).get("official") if isinstance(card_count, dict) else None,
        "set_endpoint_cards": len((detail or {}).get("cards") or []),
        "abbreviation": _normalized_abbreviation(raw_abbreviation),
        "abbreviation_raw": raw_abbreviation,
    }


def _assign_set_id(card_id: str, set_ids_longest_first: list[str]) -> str | None:
    for set_id in set_ids_longest_first:
        if card_id.startswith(f"{set_id}-"):
            return set_id
    return None


def load_inventory() -> PokemonSourceInventory:
    session = requests.Session()
    session.headers.update({"User-Agent": "dontripit-pokemon-inventory/2.0", "Accept": "application/json"})
    try:
        set_summaries = request_json(session, "sets")
        card_summaries = request_json(session, "cards")
    finally:
        session.close()

    if not isinstance(set_summaries, list) or not set_summaries:
        raise AssertionError("TCGdex /sets returned no sets")
    if not isinstance(card_summaries, list) or not card_summaries:
        raise AssertionError("TCGdex /cards returned no cards")

    sets: list[dict] = []
    errors: list[str] = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(_set_detail, row): row for row in set_summaries if isinstance(row, dict)}
        for future in as_completed(futures):
            try:
                sets.append(future.result())
            except Exception as exc:
                errors.append(f"{futures[future].get('id')}: {exc}")
    if errors:
        raise AssertionError(f"Could not resolve {len(errors)} TCGdex sets: {errors[:10]}")

    sets.sort(key=lambda row: row["set_id"])
    set_ids = sorted((row["set_id"] for row in sets), key=len, reverse=True)
    set_by_id = {row["set_id"]: row for row in sets}
    pocket_set_ids = {row["set_id"] for row in sets if row.get("series") == POCKET_SERIES_NAME}

    cards: dict[str, dict] = {}
    unassigned: list[dict] = []
    for raw in card_summaries:
        if not isinstance(raw, dict):
            continue
        card_id = str(raw.get("id") or "").strip()
        if not card_id:
            continue
        if card_id in cards:
            raise AssertionError(f"Duplicate TCGdex card id from /cards: {card_id}")
        set_id = _assign_set_id(card_id, set_ids)
        row = {
            "id": card_id,
            "local_id": str(raw.get("localId") or "").strip(),
            "name": str(raw.get("name") or "").strip(),
            "image": raw.get("image"),
            "set_id": set_id,
            "set_name": (set_by_id.get(set_id) or {}).get("set_name") if set_id else None,
            "series": (set_by_id.get(set_id) or {}).get("series") if set_id else None,
        }
        cards[card_id] = row
        if set_id is None:
            unassigned.append(row)

    return PokemonSourceInventory(
        sets=sets,
        cards=cards,
        pocket_set_ids=pocket_set_ids,
        unassigned_cards=unassigned,
    )
