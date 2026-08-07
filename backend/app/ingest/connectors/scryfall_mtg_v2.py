from __future__ import annotations

import json
import time
from datetime import date

import requests

from app.ingest.connectors.scryfall_mtg import ScryfallMtgConnector


class ScryfallMtgV2Connector(ScryfallMtgConnector):
    """Scryfall connector using current bulk-data conventions.

    Scryfall rejects poorly identified HTTP clients with 400/403 responses and,
    as of July 20 2026, bulk exports are JSONL-only. This connector therefore
    sends explicit application headers and parses JSON Lines rather than assuming
    the legacy top-level JSON array format.

    The source name intentionally stays ``scryfall_mtg`` so existing sync state,
    source records and ingest history remain continuous.
    """

    name = "scryfall_mtg"
    _SCRYFALL_HEADERS = {
        "User-Agent": "TCGCatalogV2/1.0 (+https://github.com/Alerugg/dontripit)",
        "Accept": "application/json;q=0.9,*/*;q=0.8",
    }

    def _request_json(self, url: str, params: dict | None = None) -> dict:
        wait_seconds = 0.3
        for _ in range(6):
            response = requests.get(
                url,
                params=params,
                headers=self._SCRYFALL_HEADERS,
                timeout=30,
            )
            if response.status_code in (429, 500, 502, 503, 504):
                time.sleep(wait_seconds)
                wait_seconds *= 2
                continue
            response.raise_for_status()
            time.sleep(0.12)
            return response.json()
        raise RuntimeError(f"Scryfall request failed after retries: {url}")

    @staticmethod
    def _is_default_bulk_item(item: object) -> bool:
        if not isinstance(item, dict):
            return False
        raw_type = str(item.get("type") or "").strip().lower().replace("-", "_")
        raw_name = str(item.get("name") or "").strip().lower().replace("-", " ").replace("_", " ")
        return raw_type == "default_cards" or raw_type.startswith("default_cards_") or raw_name == "default cards"

    @classmethod
    def _find_default_bulk(cls, payload: object) -> dict | None:
        if not isinstance(payload, dict):
            return None

        # A detailed bulk object may be returned directly.
        if cls._is_default_bulk_item(payload) or payload.get("download_uri"):
            return payload

        data = payload.get("data")
        if isinstance(data, dict):
            if cls._is_default_bulk_item(data) or data.get("download_uri"):
                return data
            return None
        if not isinstance(data, list):
            return None

        for item in data:
            if cls._is_default_bulk_item(item):
                return item
        return None

    def _resolve_bulk_detail(self, candidate: dict) -> dict | None:
        if candidate.get("download_uri"):
            return candidate

        detail_uri = str(candidate.get("uri") or "").strip()
        if detail_uri:
            detail = self._request_json(detail_uri)
            if isinstance(detail, dict) and detail.get("download_uri"):
                return detail
            nested = self._find_default_bulk(detail)
            if nested and nested.get("download_uri"):
                return nested

        bulk_id = str(candidate.get("id") or "").strip()
        if bulk_id:
            detail = self._request_json(f"{self.base_url}/bulk-data/{bulk_id}")
            if isinstance(detail, dict) and detail.get("download_uri"):
                return detail
            nested = self._find_default_bulk(detail)
            if nested and nested.get("download_uri"):
                return nested
        return None

    def _bulk_metadata(self) -> dict:
        # Prefer the typed endpoint. Current Scryfall deployments can return a
        # summary object/list here, so follow uri/id when download_uri is omitted.
        try:
            direct = self._request_json(f"{self.base_url}/bulk-data/default_cards")
            direct_match = self._find_default_bulk(direct)
            if direct_match is not None:
                resolved = self._resolve_bulk_detail(direct_match)
                if resolved is not None:
                    return resolved
        except requests.HTTPError as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status not in {404, 405}:
                raise

        bulk_list = self._request_json(f"{self.base_url}/bulk-data")
        default_bulk = self._find_default_bulk(bulk_list)
        if default_bulk is not None:
            resolved = self._resolve_bulk_detail(default_bulk)
            if resolved is not None:
                return resolved

        candidates = bulk_list.get("data") if isinstance(bulk_list, dict) else None
        sample = []
        if isinstance(candidates, list):
            sample = [
                {
                    "id": item.get("id"),
                    "type": item.get("type"),
                    "name": item.get("name"),
                    "uri": item.get("uri"),
                    "content_type": item.get("content_type"),
                    "has_download_uri": bool(item.get("download_uri")),
                }
                for item in candidates[:10]
                if isinstance(item, dict)
            ]
        raise RuntimeError(
            "Scryfall default_cards bulk endpoint unavailable "
            f"(top_level_keys={sorted(bulk_list.keys()) if isinstance(bulk_list, dict) else []}, sample={sample})"
        )

    def _download_default_cards(self, *, stop_after: int | None = None) -> list[dict]:
        default_bulk = self._bulk_metadata()
        download_headers = {
            "User-Agent": self._SCRYFALL_HEADERS["User-Agent"],
            "Accept": "application/jsonl,application/x-ndjson,application/json,*/*;q=0.8",
        }

        output: list[dict] = []
        with requests.get(
            default_bulk["download_uri"],
            headers=download_headers,
            stream=True,
            timeout=180,
        ) as response:
            response.raise_for_status()
            response.encoding = "utf-8"

            # Current Scryfall bulk exports are JSONL: one complete card object per
            # line. Parsing line-by-line avoids holding the raw compressed file plus
            # a second decoded JSON representation in memory.
            for raw_line in response.iter_lines(decode_unicode=True):
                line = str(raw_line or "").strip()
                if not line:
                    continue
                try:
                    item = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise RuntimeError("Scryfall default_cards bulk payload is not valid JSONL") from exc
                if isinstance(item, dict):
                    output.append(item)
                    if stop_after and len(output) >= int(stop_after):
                        break

        if not output:
            raise RuntimeError("Scryfall default_cards bulk payload contained no card rows")
        return output

    def probe_remote(self, *, limit: int = 5) -> list[dict]:
        """Read only a few JSONL rows to validate metadata + CDN access."""
        return self._download_default_cards(stop_after=max(int(limit), 1))

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
