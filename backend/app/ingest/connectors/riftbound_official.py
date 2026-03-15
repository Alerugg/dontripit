from __future__ import annotations

import os
import time
from typing import Any

import requests

from app.ingest.connectors.riftbound_types import RiftboundBackend, RiftboundBatch, RiftboundLogicalRecord


class RiftboundOfficialBackend(RiftboundBackend):
    source_name = "official"
    _CONTENT_PATH = "/riftbound/content/v1/contents"
    _RETRY_STATUSES = {429, 500, 502, 503, 504}

    def __init__(self, logger):
        self.logger = logger
        self.base_url = (os.getenv("RIFTBOUND_API_BASE_URL") or "").strip().rstrip("/")
        self.api_key = (os.getenv("RIFTBOUND_API_KEY") or "").strip()
        self.timeout_seconds = max(float(os.getenv("RIFTBOUND_TIMEOUT_SECONDS") or 30), 1)
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Accept": "application/json",
                "User-Agent": "API-PROJECT/1.0",
            }
        )
        if self.api_key:
            # Riot Developer APIs accept keys via X-Riot-Token header.
            self.session.headers["X-Riot-Token"] = self.api_key

    def is_configured(self) -> bool:
        return bool(self.base_url and self.api_key)

    def _build_url(self, path: str) -> str:
        if not self.base_url:
            raise RuntimeError("RIFTBOUND_API_BASE_URL is required for official Riftbound source")
        return f"{self.base_url}/{path.lstrip('/')}"

    def _request_json(self, path: str, *, params: dict[str, Any] | None = None) -> dict[str, Any]:
        if not self.api_key:
            raise RuntimeError("RIFTBOUND_API_KEY is required for official Riftbound source")

        url = self._build_url(path)
        backoff_seconds = 0.5
        last_error: Exception | None = None

        for attempt in range(1, 5):
            try:
                response = self.session.get(url, timeout=self.timeout_seconds, params=params)
                status = response.status_code
                if status in self._RETRY_STATUSES and attempt < 4:
                    self.logger.warning(
                        "ingest riftbound official_retry url=%s attempt=%s status=%s wait_seconds=%s",
                        url,
                        attempt,
                        status,
                        backoff_seconds,
                    )
                    time.sleep(backoff_seconds)
                    backoff_seconds *= 2
                    continue

                if status >= 400:
                    body_preview = response.text[:400].replace("\n", " ")
                    msg = (
                        "Riftbound official request failed "
                        f"url={url} status={status} body={body_preview}"
                    )
                    if status == 401:
                        msg += (
                            " hint=riot authentication rejected the key. "
                            "Confirm RIFTBOUND_API_KEY is current (not expired/revoked), "
                            "is sent as X-Riot-Token, and your Riot app is enabled for Riftbound content access."
                        )
                    elif status == 403:
                        msg += (
                            " hint=riot accepted the request but forbids access. "
                            "Your key exists but does not have valid authorization for Riftbound content; "
                            "verify key lifecycle (expired/revoked), product/API entitlement for riftbound-content-v1, "
                            "and that the Riot app/environment is enabled for this product."
                        )
                        msg += " hint=verify RIFTBOUND_API_KEY and send it as X-Riot-Token"
                    elif status == 403:
                        msg += " hint=developer key may be expired or not authorized for this API"
                    elif status == 404:
                        msg += " hint=verify API base URL and path (/riftbound/content/v1/contents)"
                    raise RuntimeError(msg)

                payload = response.json()
                if not isinstance(payload, dict):
                    raise RuntimeError(f"Riftbound official response is not a JSON object url={url}")
                return payload
            except requests.RequestException as exc:
                last_error = exc
                if attempt >= 4:
                    break
                self.logger.warning(
                    "ingest riftbound official_retry url=%s attempt=%s error=%s wait_seconds=%s",
                    url,
                    attempt,
                    exc,
                    backoff_seconds,
                )
                time.sleep(backoff_seconds)
                backoff_seconds *= 2

        raise RuntimeError(f"Riftbound official request failed url={url} last_error={last_error}")

    @staticmethod
    def _build_set_code(set_payload: dict[str, Any]) -> str:
        raw_set_id = str(set_payload.get("id") or "").strip()
        if "-" in raw_set_id:
            return raw_set_id.split("-")[-1].strip().lower()
        return raw_set_id.strip().lower()

    @staticmethod
    def _normalize_collector_number(value: object) -> str:
        collector = str(value or "").strip()
        if collector.isdigit() and len(collector) < 3:
            return collector.zfill(3)
        return collector

    @staticmethod
    def _extract_images(card_payload: dict[str, Any]) -> tuple[str | None, str | None]:
        art = card_payload.get("art") if isinstance(card_payload.get("art"), dict) else {}
        full_url = str(art.get("fullURL") or "").strip() or None
        thumb_url = str(art.get("thumbnailURL") or "").strip() or None
        return full_url, thumb_url

    @staticmethod
    def _extract_variant(card_payload: dict[str, Any]) -> str:
        tags = card_payload.get("tags") if isinstance(card_payload.get("tags"), list) else []
        for tag in tags:
            normalized = str(tag or "").strip().lower()
            if normalized in {"default", "foil", "showcase", "extended-art", "alternate-art", "borderless"}:
                return normalized
        return "default"

    @staticmethod
    def _extract_locale(content_payload: dict[str, Any]) -> str:
        locale = str(content_payload.get("locale") or "").strip().lower()
        return locale or "en"

    def fetch_all_from_content(self, content: dict[str, Any], **kwargs) -> RiftboundBatch:
        sets_payload = content.get("sets") if isinstance(content.get("sets"), list) else []
        logical_sets: list[dict[str, Any]] = []
        logical_cards: list[dict[str, Any]] = []
        logical_prints: list[dict[str, Any]] = []

        for set_payload in sets_payload:
            if not isinstance(set_payload, dict):
                continue
            set_id = str(set_payload.get("id") or "").strip()
            set_name = str(set_payload.get("name") or "").strip()
            set_code = self._build_set_code(set_payload)
            logical_sets.append({"id": set_id, "code": set_code, "name": set_name})

            set_cards = set_payload.get("cards") if isinstance(set_payload.get("cards"), list) else []
            for card_payload in set_cards:
                if not isinstance(card_payload, dict):
                    continue
                card_id = str(card_payload.get("id") or "").strip()
                collector_number = self._normalize_collector_number(card_payload.get("collectorNumber"))
                rarity = str(card_payload.get("rarity") or "").strip().lower()
                card_name = str(card_payload.get("name") or "").strip()
                locale_value = self._extract_locale(content)
                variant = self._extract_variant(card_payload)
                image_url, thumbnail_url = self._extract_images(card_payload)

                logical_cards.append({"id": card_id, "name": card_name})
                logical_prints.append(
                    {
                        "id": f"{set_id}:{card_id}:{collector_number or 'unknown'}:{locale_value}:{variant}",
                        "set_id": set_id,
                        "card_id": card_id,
                        "collector_number": collector_number,
                        "rarity": rarity,
                        "language": locale_value,
                        "variant": variant,
                        "image_url": image_url,
                        "thumbnail_url": thumbnail_url,
                        "raw_card": card_payload,
                    }
                )

        limit = kwargs.get("limit")
        if limit:
            logical_prints = logical_prints[: int(limit)]

        return RiftboundBatch(sets=logical_sets, cards=logical_cards, prints=logical_prints)

    def fetch_all(self, **kwargs) -> RiftboundBatch:
        params: dict[str, Any] = {}
        locale = kwargs.get("locale")
        if locale:
            params["locale"] = locale

        content = self._request_json(self._CONTENT_PATH, params=params or None)
        return self.fetch_all_from_content(content, **kwargs)

    def fetch_sets(self, **kwargs) -> list[dict[str, Any]]:
        return self.fetch_all(**kwargs).sets

    def fetch_cards(self, **kwargs) -> list[dict[str, Any]]:
        return self.fetch_all(**kwargs).cards

    def fetch_prints(self, **kwargs) -> list[dict[str, Any]]:
        return self.fetch_all(**kwargs).prints

    def to_logical_records(self, batch: RiftboundBatch, **kwargs) -> list[RiftboundLogicalRecord]:
        set_map: dict[str, dict[str, Any]] = {
            str(set_payload.get("id") or "").strip(): set_payload for set_payload in batch.sets if isinstance(set_payload, dict)
        }
        card_map: dict[str, dict[str, Any]] = {
            str(card_payload.get("id") or "").strip(): card_payload
            for card_payload in batch.cards
            if isinstance(card_payload, dict)
        }

        logical: list[RiftboundLogicalRecord] = []
        seen_print_ids: set[str] = set()
        for print_payload in batch.prints:
            if not isinstance(print_payload, dict):
                continue
            print_id = str(print_payload.get("id") or "").strip()
            if print_id and print_id in seen_print_ids:
                continue
            if print_id:
                seen_print_ids.add(print_id)

            set_payload = set_map.get(str(print_payload.get("set_id") or "").strip(), {})
            card_payload = card_map.get(str(print_payload.get("card_id") or "").strip(), {})
            image_url = str(print_payload.get("image_url") or "").strip() or None
            thumbnail_url = str(print_payload.get("thumbnail_url") or "").strip() or None

            logical.append(
                RiftboundLogicalRecord(
                    game_slug="riftbound",
                    set_name=str(set_payload.get("name") or "").strip(),
                    set_code=str(set_payload.get("code") or "").strip(),
                    card_name=str(card_payload.get("name") or "").strip(),
                    card_external_id=str(card_payload.get("id") or "").strip() or None,
                    print_external_id=print_id or None,
                    collector_number=str(print_payload.get("collector_number") or "").strip(),
                    rarity=str(print_payload.get("rarity") or "").strip(),
                    variant=str(print_payload.get("variant") or "").strip(),
                    locale=str(print_payload.get("language") or "en").strip(),
                    image_url=image_url,
                    thumbnail_url=thumbnail_url,
                    source_system="riot_riftbound_content_v1",
                    metadata={
                        "set_external_id": set_payload.get("id"),
                        "raw_print": {
                            "id": print_payload.get("id"),
                            "set_id": print_payload.get("set_id"),
                            "card_id": print_payload.get("card_id"),
                        },
                        "riot_card": print_payload.get("raw_card") if isinstance(print_payload.get("raw_card"), dict) else None,
                    },
                )
            )
        return logical
