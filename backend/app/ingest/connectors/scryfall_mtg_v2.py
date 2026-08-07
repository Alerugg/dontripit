from __future__ import annotations

import json
from datetime import date

import requests

from app.ingest.connectors.scryfall_mtg import ScryfallMtgConnector


class ScryfallMtgV2Connector(ScryfallMtgConnector):
    """Scryfall connector with query-independent incremental loading.

    Scryfall search queries that the legacy connector relied on started returning
    HTTP 400 in production. Catalog recovery must not depend on search grammar,
    so this implementation uses Scryfall's official bulk-data metadata and the
    ``default_cards`` dataset, then performs release-date/paper filtering locally.

    The connector name intentionally stays ``scryfall_mtg`` so existing source
    records, sync state and ingest history continue to be reused.
    """

    name = "scryfall_mtg"

    def _download_default_cards(self) -> list[dict]:
        bulk_list = self._request_json(f"{self.base_url}/bulk-data")
        default_bulk = next(
            (item for item in bulk_list.get("data") or [] if item.get("type") == "default_cards"),
            None,
        )
        if default_bulk is None or not default_bulk.get("download_uri"):
            raise RuntimeError("Scryfall default_cards bulk endpoint unavailable")

        with requests.get(default_bulk["download_uri"], stream=True, timeout=180) as response:
            response.raise_for_status()
            response.raw.decode_content = True
            payload = json.load(response.raw)

        if not isinstance(payload, list):
            raise RuntimeError("Scryfall default_cards bulk payload is not a JSON array")
        return [item for item in payload if isinstance(item, dict)]

    @staticmethod
    def _is_paper_card(card: dict) -> bool:
        games = card.get("games")
        if not isinstance(games, list):
            # Older/partial fixture payloads may omit games. Do not discard them
            # solely because the field is absent; live Scryfall bulk data includes it.
            return True
        return "paper" in {str(item).strip().lower() for item in games}

    @staticmethod
    def _released_date(card: dict) -> date | None:
        raw = str(card.get("released_at") or "").strip()
        if not raw:
            return None
        try:
            return date.fromisoformat(raw)
        except ValueError:
            return None

    def _load_remote(self, limit: int | None = None) -> list[dict]:
        cards = self._download_default_cards()
        cards = [card for card in cards if self._is_paper_card(card)]
        cards.sort(
            key=lambda card: (
                self._released_date(card) or date.min,
                str(card.get("id") or ""),
            ),
            reverse=True,
        )

        seen_ids: set[str] = set()
        output: list[dict] = []
        for card in cards:
            card_id = str(card.get("id") or "").strip()
            if card_id and card_id in seen_ids:
                continue
            if card_id:
                seen_ids.add(card_id)
            output.append(card)
            if limit and len(output) >= limit:
                break
        return output

    def _load_incremental(self, limit: int | None = None, last_run_at=None) -> list[dict]:
        cutoff_date = last_run_at.date() if last_run_at is not None else None
        cards = self._download_default_cards()

        candidates: list[dict] = []
        seen_ids: set[str] = set()
        for card in cards:
            if not self._is_paper_card(card):
                continue

            released_date = self._released_date(card)
            if cutoff_date is not None and released_date is not None and released_date < cutoff_date:
                continue

            card_id = str(card.get("id") or "").strip()
            if card_id and card_id in seen_ids:
                continue
            if card_id:
                seen_ids.add(card_id)
            candidates.append(card)

        candidates.sort(
            key=lambda card: (
                self._released_date(card) or date.min,
                str(card.get("id") or ""),
            ),
            reverse=True,
        )

        if limit:
            return candidates[: int(limit)]
        return candidates
