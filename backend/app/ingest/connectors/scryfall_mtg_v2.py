from __future__ import annotations

import gzip
import io
import json
import time
from datetime import date

import requests

from app.ingest.connectors.scryfall_mtg import ScryfallMtgConnector


class ScryfallMtgV2Connector(ScryfallMtgConnector):
    """Scryfall connector using the current JSONL bulk-data contract.

    Current Scryfall behavior requires an identified HTTP client and exposes
    ``jsonl_download_uri`` for compressed JSONL exports. The source name remains
    ``scryfall_mtg`` so all existing sync state and provenance stay continuous.
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
    def _bulk_download_url(item: object) -> str | None:
        if not isinstance(item, dict):
            return None
        for key in ("jsonl_download_uri", "download_uri"):
            value = str(item.get(key) or "").strip()
            if value:
                return value
        return None

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

        if cls._is_default_bulk_item(payload) or cls._bulk_download_url(payload):
            return payload

        data = payload.get("data")
        if isinstance(data, dict):
            if cls._is_default_bulk_item(data) or cls._bulk_download_url(data):
                return data
            return None
        if not isinstance(data, list):
            return None

        for item in data:
            if cls._is_default_bulk_item(item):
                return item
        return None

    def _resolve_bulk_detail(self, candidate: dict) -> dict | None:
        if self._bulk_download_url(candidate):
            return candidate

        detail_uri = str(candidate.get("uri") or "").strip()
        if detail_uri:
            detail = self._request_json(detail_uri)
            if isinstance(detail, dict) and self._bulk_download_url(detail):
                return detail
            nested = self._find_default_bulk(detail)
            if nested and self._bulk_download_url(nested):
                return nested

        bulk_id = str(candidate.get("id") or "").strip()
        if bulk_id:
            detail = self._request_json(f"{self.base_url}/bulk-data/{bulk_id}")
            if isinstance(detail, dict) and self._bulk_download_url(detail):
                return detail
            nested = self._find_default_bulk(detail)
            if nested and self._bulk_download_url(nested):
                return nested
        return None

    def _bulk_metadata(self) -> dict:
        # Try the stable listing first because current responses already include
        # jsonl_download_uri on the default_cards summary record.
        bulk_list = self._request_json(f"{self.base_url}/bulk-data")
        default_bulk = self._find_default_bulk(bulk_list)
        if default_bulk is not None:
            resolved = self._resolve_bulk_detail(default_bulk)
            if resolved is not None:
                return resolved

        raise RuntimeError("Scryfall default_cards JSONL bulk endpoint unavailable")

    @staticmethod
    def _parse_jsonl_lines(lines, *, stop_after: int | None = None) -> list[dict]:
        output: list[dict] = []
        for raw_line in lines:
            if isinstance(raw_line, bytes):
                line = raw_line.decode("utf-8").strip()
            else:
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
        return output

    def _download_default_cards(self, *, stop_after: int | None = None) -> list[dict]:
        default_bulk = self._bulk_metadata()
        download_url = self._bulk_download_url(default_bulk)
        if not download_url:
            raise RuntimeError("Scryfall default_cards metadata has no JSONL download URL")

        download_headers = {
            "User-Agent": self._SCRYFALL_HEADERS["User-Agent"],
            "Accept": "application/gzip,application/jsonl,application/x-ndjson,*/*;q=0.8",
        }

        with requests.get(
            download_url,
            headers=download_headers,
            stream=True,
            timeout=180,
        ) as response:
            response.raise_for_status()
            response.raw.decode_content = False

            if download_url.lower().endswith(".gz"):
                with gzip.GzipFile(fileobj=response.raw, mode="rb") as compressed:
                    with io.TextIOWrapper(compressed, encoding="utf-8") as text_stream:
                        output = self._parse_jsonl_lines(text_stream, stop_after=stop_after)
            else:
                output = self._parse_jsonl_lines(
                    response.iter_lines(decode_unicode=True),
                    stop_after=stop_after,
                )

        if not output:
            raise RuntimeError("Scryfall default_cards bulk payload contained no card rows")
        return output

    def probe_remote(self, *, limit: int = 5) -> list[dict]:
        """Validate metadata + CDN + gzip/JSONL parsing without loading the DB."""
        return self._download_default_cards(stop_after=max(int(limit), 1))

    @staticmethod
    def _is_paper_card(card: dict) -> bool:
        games = card.get("games")
        if not isinstance(games, list):
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
