from __future__ import annotations

import json
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from pathlib import Path
from typing import Iterable

import requests
from sqlalchemy import select

from app.ingest.base import IngestStats, SourceConnector
from app.ingest.normalization import normalize_collector_number, normalize_variant
from app.models import Card, Game, Print, PrintIdentifier, PrintImage, Set


class OnePieceConnector(SourceConnector):
    name = "onepiece"
    _DEFAULT_TIMEOUT_SECONDS = 30
    _DEFAULT_PUNKRECORDS_ROOT_URL = "https://raw.githubusercontent.com/DevTheFrog/punk-records/main"
    _DEFAULT_PUNKRECORDS_LANGUAGE = "english"
    _DEFAULT_IMAGE_FALLBACK_URL = "https://placehold.co/367x512?text=ONE+PIECE"
    _DEFAULT_REMOTE_MAX_WORKERS = 12
    _COMMERCIAL_CODE_RE = re.compile(r"\b(op|st|eb)[\s\-_]?(\d{1,2})\b", re.IGNORECASE)
    _PACK_PREFIX_BY_FAMILY = {"0": "st", "1": "op", "2": "eb"}

    def __init__(self) -> None:
        self._github_http_session: requests.Session | None = None
        self._remote_json_cache: dict[str, object | None] = {}

    @staticmethod
    def _env(name: str, default: str) -> str:
        value = str(os.getenv(name) or "").strip()
        return value or default

    def _source_mode(self, *, fixture: bool = False) -> str:
        if fixture:
            return "fixture"
        mode = self._env("ONEPIECE_SOURCE", "fixture").lower()
        if mode not in {"fixture", "remote"}:
            self.logger.warning("ingest onepiece invalid_source_mode=%s using=fixture", mode)
            return "fixture"
        return mode

    def _http_timeout(self) -> int:
        raw = self._env("ONEPIECE_TIMEOUT_SECONDS", str(self._DEFAULT_TIMEOUT_SECONDS))
        try:
            parsed = int(raw)
        except ValueError:
            return self._DEFAULT_TIMEOUT_SECONDS
        return parsed if parsed > 0 else self._DEFAULT_TIMEOUT_SECONDS

    @staticmethod
    def _build_url(base_url: str, path: str) -> str:
        if path.startswith(("https://", "http://")):
            return path
        return f"{base_url.rstrip('/')}/{path.lstrip('/')}"

    def _punkrecords_root_url(self) -> str:
        root = self._env("ONEPIECE_PUNKRECORDS_ROOT_URL", "")
        if root:
            return root
        legacy_base = self._env("ONEPIECE_PUNKRECORDS_BASE_URL", "")
        if legacy_base:
            return legacy_base
        return self._DEFAULT_PUNKRECORDS_ROOT_URL

    def _punkrecords_language(self) -> str:
        return self._env("ONEPIECE_PUNKRECORDS_LANGUAGE", self._DEFAULT_PUNKRECORDS_LANGUAGE).lower()

    @staticmethod
    def _record_get(record: dict, *keys: str) -> object:
        for key in keys:
            value = record.get(key)
            if value not in (None, ""):
                return value
        return None

    def _resolve_remote_image_url(self, record: dict) -> str:
        candidate = str(
            self._record_get(
                record,
                "img_full_url",
                "image_url",
                "img_url",
                "img_thumb_url",
                "image",
            )
            or ""
        ).strip()
        if candidate and "example.cdn.onepiece" not in candidate.lower():
            return candidate
        return self._env("ONEPIECE_IMAGE_FALLBACK_URL", self._DEFAULT_IMAGE_FALLBACK_URL)

    def _iter_pack_records(self, packs_payload: object) -> Iterable[dict]:
        if isinstance(packs_payload, list):
            for raw_pack in packs_payload:
                if isinstance(raw_pack, dict):
                    yield raw_pack
            return

        if isinstance(packs_payload, dict):
            for pack_key, raw_pack in packs_payload.items():
                if isinstance(raw_pack, dict):
                    pack_copy = dict(raw_pack)
                    if not pack_copy.get("id"):
                        pack_copy["id"] = str(pack_key)
                    yield pack_copy

    def _iter_card_records(self, cards_payload: object) -> Iterable[dict]:
        if isinstance(cards_payload, list):
            for raw_card in cards_payload:
                if isinstance(raw_card, dict):
                    yield raw_card
            return

        if isinstance(cards_payload, dict):
            list_keys = ("cards", "data", "results", "items")
            for key in list_keys:
                nested = cards_payload.get(key)
                if isinstance(nested, list):
                    for raw_card in nested:
                        if isinstance(raw_card, dict):
                            yield raw_card
                    return

            for card_key, raw_card in cards_payload.items():
                if isinstance(raw_card, dict):
                    card_copy = dict(raw_card)
                    if not card_copy.get("id"):
                        card_copy["id"] = str(card_key)
                    yield card_copy

    def _normalize_remote_payload(self, *, packs_payload: object, cards_payload_by_pack: dict[str, object], language: str) -> dict:
        normalized_sets: dict[str, dict] = {}
        commercial_code_by_pack_key: dict[str, str] = {}
        for raw_pack in self._iter_pack_records(packs_payload):
            title_parts = raw_pack.get("title_parts") if isinstance(raw_pack.get("title_parts"), dict) else {}
            set_code = self._derive_commercial_set_code(raw_pack)
            if not set_code:
                continue
            for key in self._pack_lookup_keys(raw_pack):
                commercial_code_by_pack_key[key] = set_code
            normalized_sets[set_code] = {
                "id": str(self._record_get(raw_pack, "id", "code", "pack_id", "set_code") or set_code).strip(),
                "code": set_code,
                "name": str(
                    self._record_get(raw_pack, "name", "display_name", "set_name")
                    or title_parts.get("label")
                    or raw_pack.get("raw_title")
                    or raw_pack.get("code")
                    or set_code
                ).strip(),
                "type": str(self._record_get(raw_pack, "type", "category") or "").strip() or None,
                "release_date": self._record_get(raw_pack, "release_date", "date_release", "released_at", "date"),
            }

        cards_by_key: dict[str, dict] = {}
        for pack_code, payload in cards_payload_by_pack.items():
            for raw_card in self._iter_card_records(payload):
                card_name = str(self._record_get(raw_card, "name", "card_name") or "").strip()
                set_code = self._derive_card_set_code(raw_card, fallback_pack_code=pack_code, known_set_codes=commercial_code_by_pack_key)
                collector = str(self._record_get(raw_card, "id", "code", "collector_number", "number") or "").strip()
                if not card_name or not set_code or not collector:
                    continue
                if set_code not in normalized_sets:
                    normalized_sets[set_code] = {
                        "id": set_code.upper(),
                        "code": set_code,
                        "name": set_code.upper(),
                        "type": None,
                        "release_date": None,
                    }

                logical_number = collector.split("_", 1)[0]
                card_id = str(self._record_get(raw_card, "card_id", "uuid") or f"{set_code}:{logical_number}").strip().lower().replace(" ", "-")
                if card_id not in cards_by_key:
                    cards_by_key[card_id] = {"id": card_id, "name": card_name, "prints": []}

                variant = str(self._record_get(raw_card, "variant", "finish", "category") or "").strip()
                if not variant and "_" in collector:
                    variant = collector.split("_", 1)[1]
                variant = self._normalize_onepiece_variant(variant or "default")

                cards_by_key[card_id]["prints"].append(
                    {
                        "id": str(self._record_get(raw_card, "id", "code", "external_id") or "").strip() or None,
                        "set_code": set_code,
                        "collector_number": logical_number,
                        "rarity": str(self._record_get(raw_card, "rarity", "rarity_code") or "").strip() or None,
                        "variant": variant,
                        "image_url": self._resolve_remote_image_url(raw_card),
                    }
                )

        return {
            "source": "punk_records",
            "language": language,
            "sets": sorted(normalized_sets.values(), key=lambda row: row["code"]),
            "cards": list(cards_by_key.values()),
        }

    @staticmethod
    def _normalize_onepiece_variant(value: object) -> str:
        variant = normalize_variant(value)
        if re.fullmatch(r"p\d+", variant):
            return "parallel"
        return variant

    def _derive_commercial_set_code(self, raw_pack: dict) -> str:
        candidates: list[str] = []
        title_parts = raw_pack.get("title_parts") if isinstance(raw_pack.get("title_parts"), dict) else {}
        for field in ("code", "set_code", "pack_id", "id", "name", "display_name", "set_name", "raw_title"):
            value = str(raw_pack.get(field) or "").strip()
            if value:
                candidates.append(value)
        for key in ("label", "code", "name"):
            value = str(title_parts.get(key) or "").strip()
            if value:
                candidates.append(value)

        for candidate in candidates:
            parsed = self._extract_commercial_code(candidate)
            if parsed:
                return parsed

        remote_pack_id = str(self._record_get(raw_pack, "id", "pack_id", "set_id") or "").strip().lower()
        return self._commercial_code_from_remote_pack_id(remote_pack_id)

    def _derive_card_set_code(self, raw_card: dict, *, fallback_pack_code: str, known_set_codes: dict[str, str]) -> str:
        for field in ("set_code", "pack_id", "set", "set_id"):
            candidate = str(raw_card.get(field) or "").strip().lower()
            if not candidate:
                continue
            if candidate in known_set_codes:
                return known_set_codes[candidate]
            parsed = self._extract_commercial_code(candidate)
            if parsed:
                return parsed
            parsed = self._commercial_code_from_remote_pack_id(candidate)
            if parsed:
                return parsed

        fallback = str(fallback_pack_code or "").strip().lower()
        if fallback in known_set_codes:
            return known_set_codes[fallback]
        parsed = self._extract_commercial_code(fallback)
        if parsed:
            return parsed
        parsed = self._commercial_code_from_remote_pack_id(fallback)
        if parsed:
            return parsed
        return fallback

    def _extract_commercial_code(self, candidate: str) -> str:
        normalized = str(candidate or "").strip().lower()
        if not normalized:
            return ""
        match = self._COMMERCIAL_CODE_RE.search(normalized)
        if not match:
            return ""
        prefix, number = match.groups()
        return f"{prefix.lower()}-{int(number):02d}"

    def _commercial_code_from_remote_pack_id(self, remote_pack_id: str) -> str:
        raw = str(remote_pack_id or "").strip().lower()
        if not raw.isdigit() or len(raw) < 6:
            return ""
        if not raw.startswith("569"):
            return ""
        family = raw[3]
        prefix = self._PACK_PREFIX_BY_FAMILY.get(family)
        if not prefix:
            return ""
        return f"{prefix}-{int(raw[-2:]):02d}"

    def _merge_set_into_canonical(self, *, session, stats: IngestStats, source_set: Set, canonical_set: Set, language: str) -> None:
        source_prints = session.execute(select(Print).where(Print.set_id == source_set.id)).scalars().all()
        for source_print in source_prints:
            same_identity = session.execute(
                select(Print).where(
                    Print.set_id == canonical_set.id,
                    Print.collector_number == source_print.collector_number,
                    Print.language == source_print.language,
                    Print.is_foil == source_print.is_foil,
                    Print.variant == source_print.variant,
                )
            ).scalar_one_or_none()
            if same_identity is None:
                source_print.set_id = canonical_set.id
                normalized_language = self._normalize_language(source_print.language or language)
                source_print.language = normalized_language
                source_print.print_key = f"onepiece:{canonical_set.code}:{normalize_collector_number(source_print.collector_number)}:{normalized_language}:{source_print.variant}"
                stats.records_updated += 1
                continue

            source_images = session.execute(select(PrintImage).where(PrintImage.print_id == source_print.id)).scalars().all()
            existing_image_urls = {
                str(url).strip()
                for url in session.execute(select(PrintImage.url).where(PrintImage.print_id == same_identity.id)).scalars().all()
                if str(url).strip()
            }
            for image in source_images:
                if image.url not in existing_image_urls:
                    image.print_id = same_identity.id
                    stats.records_updated += 1
                else:
                    session.delete(image)
                    stats.records_updated += 1

            source_identifiers = session.execute(select(PrintIdentifier).where(PrintIdentifier.print_id == source_print.id)).scalars().all()
            for identifier in source_identifiers:
                existing_identifier = session.execute(
                    select(PrintIdentifier).where(
                        PrintIdentifier.print_id == same_identity.id,
                        PrintIdentifier.source == identifier.source,
                    )
                ).scalar_one_or_none()
                if existing_identifier is None:
                    identifier.print_id = same_identity.id
                    stats.records_updated += 1
                else:
                    session.delete(identifier)
                    stats.records_updated += 1

            session.delete(source_print)
            stats.records_updated += 1

        session.flush()
        if session.execute(select(Print.id).where(Print.set_id == source_set.id)).first() is None:
            session.delete(source_set)
            stats.records_updated += 1

    def _load_remote(self) -> dict:
        self._remote_json_cache.clear()
        started_at = time.monotonic()
        root_url = self._punkrecords_root_url()
        language = self._punkrecords_language()
        packs_url = self._build_url(root_url, f"{language}/packs.json")
        timeout = self._http_timeout()

        packs_fetch_started = time.monotonic()
        packs_payload = self._request_json(url=packs_url, timeout=timeout)
        self.logger.info(
            "ingest onepiece remote packs_loaded elapsed_s=%.2f",
            time.monotonic() - packs_fetch_started,
        )

        cards_payload_by_pack: dict[str, object] = {}
        pack_records = list(self._iter_pack_records(packs_payload))
        tree_index_fetch_started = time.monotonic()
        card_urls_by_pack = self._fetch_pack_card_file_urls_from_tree(
            root_url=root_url,
            language=language,
            timeout=timeout,
        )
        self.logger.info(
            "ingest onepiece remote tree_index_built packs_with_cards=%s elapsed_s=%.2f",
            len(card_urls_by_pack),
            time.monotonic() - tree_index_fetch_started,
        )

        if not card_urls_by_pack:
            raise ValueError("One Piece remote ingest tree resolved but found zero card json paths under english/cards")

        valid_cards_count = 0
        failed_cards_count = 0
        progress_every_packs = 25
        progress_every_cards = 200
        cards_fetch_started = time.monotonic()
        card_jobs: list[tuple[str, str]] = []
        for raw_pack in pack_records:
            pack_id = str(self._record_get(raw_pack, "id", "code", "pack_id", "set_code") or "").strip()
            if not pack_id:
                self.logger.warning("ingest onepiece remote pack_skipped missing_pack_id")
                continue

            lookup_keys = self._pack_lookup_keys(raw_pack)
            card_urls = self._resolve_pack_card_urls_from_tree(card_urls_by_pack=card_urls_by_pack, lookup_keys=lookup_keys)
            if not card_urls:
                continue

            cards_payload_by_pack[pack_id.lower()] = []
            for card_url in card_urls:
                card_jobs.append((pack_id.lower(), card_url))

            if len(cards_payload_by_pack) % progress_every_packs == 0:
                self.logger.info(
                    "ingest onepiece remote progress packs=%s/%s cards=%s elapsed_s=%.2f",
                    len(cards_payload_by_pack),
                    len(pack_records),
                    valid_cards_count,
                    time.monotonic() - cards_fetch_started,
                )

        max_workers = self._remote_max_workers()
        workers_used = min(max_workers, len(card_jobs)) if card_jobs else 0

        if card_jobs:
            for pack_key, payload in self._fetch_card_payloads_concurrently(card_jobs=card_jobs, timeout=timeout, max_workers=max_workers):
                if isinstance(payload, dict):
                    cards_payload_by_pack[pack_key].append(payload)
                    valid_cards_count += 1
                else:
                    failed_cards_count += 1

                if (valid_cards_count + failed_cards_count) % progress_every_cards == 0:
                    self.logger.info(
                        "ingest onepiece remote progress cards_downloaded=%s cards_failed=%s packs_processed=%s/%s workers=%s elapsed_s=%.2f",
                        valid_cards_count,
                        failed_cards_count,
                        len(cards_payload_by_pack),
                        len(pack_records),
                        workers_used,
                        time.monotonic() - cards_fetch_started,
                    )

        normalized = self._normalize_remote_payload(
            packs_payload=packs_payload,
            cards_payload_by_pack=cards_payload_by_pack,
            language=language,
        )
        self.logger.info(
            "ingest onepiece remote cards_loaded cards=%s cards_failed=%s workers=%s elapsed_s=%.2f total_elapsed_s=%.2f",
            valid_cards_count,
            failed_cards_count,
            workers_used,
            time.monotonic() - cards_fetch_started,
            time.monotonic() - started_at,
        )
        if pack_records and not normalized.get("cards"):
            raise ValueError("One Piece remote ingest resolved packs.json but found zero cards across all packs")
        return normalized

    def _remote_max_workers(self) -> int:
        raw = self._env("ONEPIECE_REMOTE_MAX_WORKERS", str(self._DEFAULT_REMOTE_MAX_WORKERS))
        try:
            parsed = int(raw)
        except ValueError:
            return self._DEFAULT_REMOTE_MAX_WORKERS
        return max(1, parsed)

    def _fetch_card_payloads_concurrently(
        self,
        *,
        card_jobs: list[tuple[str, str]],
        timeout: int,
        max_workers: int,
    ) -> list[tuple[str, object | None]]:
        thread_local = threading.local()

        def _session() -> requests.Session:
            session = getattr(thread_local, "session", None)
            if session is None:
                session = requests.Session()
                thread_local.session = session
            return session

        def _fetch(pack_key: str, card_url: str) -> tuple[str, object | None]:
            cached = self._remote_json_cache.get(card_url)
            if card_url in self._remote_json_cache:
                return pack_key, cached
            session = _session()
            try:
                response = session.get(card_url, timeout=timeout)
                response.raise_for_status()
                payload = response.json()
                self._remote_json_cache[card_url] = payload
                return pack_key, payload
            except requests.RequestException as exc:
                self.logger.warning(
                    "ingest onepiece remote request_failed url=%s reason=network_error detail=%s",
                    card_url,
                    exc,
                )
                self._remote_json_cache[card_url] = None
                return pack_key, None
            except ValueError:
                self.logger.warning("ingest onepiece remote request_failed url=%s reason=invalid_json", card_url)
                self._remote_json_cache[card_url] = None
                return pack_key, None

        results: list[tuple[str, object | None]] = []
        with ThreadPoolExecutor(max_workers=max(1, max_workers)) as executor:
            futures = [executor.submit(_fetch, pack_key, card_url) for pack_key, card_url in card_jobs]
            for future in as_completed(futures):
                results.append(future.result())
        return results

    def _same_logical_print(self, left: Print, right: Print) -> bool:
        return (
            left.set_id == right.set_id
            and left.language == right.language
            and left.variant == right.variant
            and normalize_collector_number(left.collector_number) == normalize_collector_number(right.collector_number)
        )

    def _reconcile_print_identifier(
        self,
        *,
        session,
        stats: IngestStats,
        print_row: Print,
        external_print_id: str,
    ) -> Print:
        identifier_for_print = session.execute(
            select(PrintIdentifier).where(
                PrintIdentifier.print_id == print_row.id,
                PrintIdentifier.source == "punk_records",
            )
        ).scalar_one_or_none()
        identifier_by_external = session.execute(
            select(PrintIdentifier).where(
                PrintIdentifier.source == "punk_records",
                PrintIdentifier.external_id == external_print_id,
            )
        ).scalar_one_or_none()

        if identifier_by_external is not None and identifier_by_external.print_id != print_row.id:
            existing_print = session.execute(select(Print).where(Print.id == identifier_by_external.print_id)).scalar_one_or_none()
            if existing_print is not None and self._same_logical_print(existing_print, print_row):
                self.logger.info(
                    "ingest onepiece identifier_collision_resolved strategy=reuse_existing_print external_id=%s existing_print_id=%s requested_print_id=%s",
                    external_print_id,
                    existing_print.id,
                    print_row.id,
                )
                print_row = existing_print
                identifier_for_print = identifier_by_external
            else:
                self.logger.warning(
                    "ingest onepiece identifier_collision_resolved strategy=move_identifier external_id=%s from_print_id=%s to_print_id=%s",
                    external_print_id,
                    identifier_by_external.print_id,
                    print_row.id,
                )
                identifier_by_external.print_id = print_row.id
                stats.records_updated += 1
                identifier_for_print = identifier_by_external

        if identifier_for_print is None:
            session.add(
                PrintIdentifier(
                    print_id=print_row.id,
                    source="punk_records",
                    external_id=external_print_id,
                )
            )
            stats.records_inserted += 1
            return print_row

        if identifier_for_print.external_id != external_print_id:
            conflicting = session.execute(
                select(PrintIdentifier).where(
                    PrintIdentifier.source == "punk_records",
                    PrintIdentifier.external_id == external_print_id,
                    PrintIdentifier.id != identifier_for_print.id,
                )
            ).scalar_one_or_none()
            if conflicting is not None:
                self.logger.warning(
                    "ingest onepiece identifier_collision_avoided action=skip_update print_id=%s external_id=%s conflicting_print_id=%s",
                    print_row.id,
                    external_print_id,
                    conflicting.print_id,
                )
                return print_row

            self.logger.warning(
                "ingest onepiece identifier_reassigned print_id=%s old_external_id=%s new_external_id=%s",
                print_row.id,
                identifier_for_print.external_id,
                external_print_id,
            )
            identifier_for_print.external_id = external_print_id
            stats.records_updated += 1

        return print_row

    @staticmethod
    def _github_api_repo_context(root_url: str) -> tuple[str, str, str, str] | None:
        pattern = r"^https://raw\.githubusercontent\.com/([^/]+)/([^/]+)/([^/]+)(?:/(.*))?$"
        match = re.match(pattern, root_url.strip().rstrip("/"))
        if not match:
            return None
        owner, repo, ref, suffix = match.groups()
        return owner, repo, ref, (suffix or "").strip("/")

    def _github_http(self) -> requests.Session:
        if self._github_http_session is None:
            session = requests.Session()
            session.headers.update(
                {
                    "Accept": "application/vnd.github+json",
                    "User-Agent": "api-project-onepiece-ingest/1.0",
                }
            )
            token = self._env("GITHUB_TOKEN", "") or self._env("ONEPIECE_GITHUB_TOKEN", "")
            if token:
                session.headers.update({"Authorization": f"Bearer {token}"})
            self._github_http_session = session
        return self._github_http_session

    def _request_json(self, *, url: str, timeout: int) -> object:
        if "api.github.com" in url:
            session = self._github_http()
            response = requests.get(url, timeout=timeout, headers=dict(session.headers))
        else:
            response = requests.get(url, timeout=timeout)
        response.raise_for_status()
        return response.json()

    def _fetch_remote_json(self, *, url: str, timeout: int) -> object | None:
        if url in self._remote_json_cache:
            return self._remote_json_cache[url]

        try:
            payload = self._request_json(url=url, timeout=timeout)
            self._remote_json_cache[url] = payload
            return payload
        except requests.HTTPError as exc:
            status_code = getattr(getattr(exc, "response", None), "status_code", None)
            if status_code in {403, 429}:
                remaining = str(getattr(exc.response, "headers", {}).get("X-RateLimit-Remaining") or "unknown")
                reset = str(getattr(exc.response, "headers", {}).get("X-RateLimit-Reset") or "unknown")
                self.logger.warning(
                    "ingest onepiece remote request_failed url=%s reason=rate_limited status=%s remaining=%s reset=%s",
                    url,
                    status_code,
                    remaining,
                    reset,
                )
                if "api.github.com" in url:
                    raise RuntimeError(
                        "One Piece remote ingest hit GitHub API rate limit "
                        f"(status={status_code}, remaining={remaining}, reset={reset}). "
                        "Set GITHUB_TOKEN (recommended) or retry after reset."
                    ) from exc
            else:
                self.logger.warning(
                    "ingest onepiece remote request_failed url=%s reason=http_error status=%s",
                    url,
                    status_code,
                )
            self._remote_json_cache[url] = None
            return None
        except requests.RequestException as exc:
            self.logger.warning(
                "ingest onepiece remote request_failed url=%s reason=network_error detail=%s",
                url,
                exc,
            )
            self._remote_json_cache[url] = None
            return None
        except ValueError:
            self.logger.warning("ingest onepiece remote request_failed url=%s reason=invalid_json", url)
            self._remote_json_cache[url] = None
            return None

    def _build_cards_tree_api_url(self, *, root_url: str) -> tuple[str, str] | None:
        github_context = self._github_api_repo_context(root_url)
        if github_context is None:
            return None
        owner, repo, ref, suffix = github_context
        return f"https://api.github.com/repos/{owner}/{repo}/git/trees/{ref}?recursive=1", suffix

    def _fetch_pack_card_file_urls_from_tree(
        self,
        *,
        root_url: str,
        language: str,
        timeout: int,
    ) -> dict[str, list[str]]:
        tree_context = self._build_cards_tree_api_url(root_url=root_url)
        if tree_context is None:
            self.logger.warning("ingest onepiece remote cards_tree_unavailable reason=unsupported_root_url")
            return {}

        tree_url, suffix = tree_context
        payload = self._fetch_remote_json(url=tree_url, timeout=timeout)
        if not isinstance(payload, dict):
            self.logger.warning("ingest onepiece remote cards_tree_unavailable reason=invalid_tree_payload")
            return {}

        tree_rows = payload.get("tree")
        if not isinstance(tree_rows, list):
            self.logger.warning("ingest onepiece remote cards_tree_unavailable reason=missing_tree")
            return {}

        base_path = f"{suffix}/" if suffix else ""
        cards_prefix = f"{base_path}{language}/cards/"
        urls_by_pack: dict[str, list[str]] = {}

        for row in tree_rows:
            if not isinstance(row, dict):
                continue
            if str(row.get("type") or "").strip().lower() != "blob":
                continue

            raw_path = str(row.get("path") or "").strip()
            if not raw_path.startswith(cards_prefix):
                continue
            relative_path = raw_path[len(cards_prefix) :]
            if "/" not in relative_path:
                continue

            pack_id, filename = relative_path.split("/", 1)
            if not pack_id or not filename.lower().endswith(".json"):
                continue

            urls_by_pack.setdefault(pack_id, []).append(self._build_url(root_url, raw_path))

        for key, urls in urls_by_pack.items():
            urls_by_pack[key] = sorted(set(urls))

        if not urls_by_pack:
            self.logger.warning("ingest onepiece remote cards_tree_unavailable reason=no_json_cards_found")
        return urls_by_pack

    @staticmethod
    def _pack_lookup_keys(raw_pack: dict) -> list[str]:
        keys: list[str] = []
        for field in ("id", "pack_id", "set_id", "code", "set_code"):
            value = str(raw_pack.get(field) or "").strip().lower()
            if value and value not in keys:
                keys.append(value)
        return keys

    @staticmethod
    def _resolve_pack_card_urls_from_tree(*, card_urls_by_pack: dict[str, list[str]], lookup_keys: list[str]) -> list[str]:
        for lookup_key in lookup_keys:
            direct = card_urls_by_pack.get(lookup_key)
            if direct:
                return direct
        return []


    @staticmethod
    def _normalize_language(value: str | None) -> str:
        raw = str(value or "").strip().lower()
        if raw in {"", "english", "en-us", "en-gb"}:
            return "en"
        if raw in {"japanese", "ja-jp"}:
            return "ja"
        return raw

    def _resolve_fixture_path(self, path: str | Path | None) -> Path:
        fixture_name = "onepiece_punkrecords_sample.json"
        backend_root = Path(__file__).resolve().parents[3]
        repo_root = backend_root.parent
        default_candidates = [
            backend_root / "data" / "fixtures" / fixture_name,
            repo_root / "backend" / "data" / "fixtures" / fixture_name,
        ]

        if path is not None:
            candidate = Path(path)
            if candidate.is_file():
                return candidate
            if candidate.is_dir():
                candidate = candidate / fixture_name
                if candidate.is_file():
                    return candidate

        for candidate in default_candidates:
            if candidate.is_file():
                return candidate

        raise FileNotFoundError(f"Unable to resolve fixture path for {fixture_name}")

    def load(self, path: str | Path | None = None, **kwargs) -> list[tuple[Path, dict, str]]:
        fixture = bool(kwargs.get("fixture", False))
        source_mode = self._source_mode(fixture=fixture)
        if source_mode == "fixture":
            fixture_path = self._resolve_fixture_path(path)
            raw = fixture_path.read_text(encoding="utf-8")
            payload = json.loads(raw)
            return [(fixture_path, payload, self.checksum(payload))]

        if isinstance(path, str) and path.startswith(("https://", "http://")):
            response = requests.get(path, timeout=self._http_timeout())
            response.raise_for_status()
            payload = self._normalize_remote_payload(
                packs_payload=[],
                cards_payload_by_pack={"remote": response.json()},
                language=self._punkrecords_language(),
            )
            source_path = Path("onepiece_remote.json")
            return [(source_path, payload, self.checksum(payload))]

        if source_mode == "remote":
            payload = self._load_remote()
            source_path = Path("onepiece_punkrecords_remote.json")
            return [(source_path, payload, self.checksum(payload))]

        return super().load(path, **kwargs)

    def _ensure_game(self, session, stats: IngestStats) -> Game:
        game = session.execute(select(Game).where(Game.slug == "onepiece")).scalar_one_or_none()
        if game is None:
            game = Game(slug="onepiece", name="ONE PIECE Card Game")
            session.add(game)
            session.flush()
            stats.records_inserted += 1
        return game

    def upsert(self, session, payload: dict, stats: IngestStats, **kwargs) -> dict:
        game = self._ensure_game(session, stats)
        language = self._normalize_language(str(payload.get("language") or "en"))

        touched = {"card_ids": set(), "set_ids": set(), "print_ids": set()}
        sets_by_code: dict[str, Set] = {}

        for item in payload.get("sets") or []:
            set_code = str(item.get("code") or "").strip().lower()
            if not set_code:
                continue
            remote_pack_id = str(item.get("id") or "").strip().lower()

            release_date = None
            if item.get("release_date"):
                release_date = date.fromisoformat(str(item["release_date"]))

            set_row = session.execute(select(Set).where(Set.game_id == game.id, Set.code == set_code)).scalar_one_or_none()
            if set_row is None:
                set_row = Set(
                    game_id=game.id,
                    code=set_code,
                    name=str(item.get("name") or set_code).strip(),
                    release_date=release_date,
                )
                session.add(set_row)
                session.flush()
                stats.records_inserted += 1
            else:
                changed = False
                set_name = str(item.get("name") or "").strip()
                if set_name and set_row.name != set_name:
                    set_row.name = set_name
                    changed = True
                if release_date and set_row.release_date != release_date:
                    set_row.release_date = release_date
                    changed = True
                if changed:
                    stats.records_updated += 1

            sets_by_code[set_code] = set_row
            touched["set_ids"].add(set_row.id)

            if remote_pack_id and remote_pack_id != set_code:
                alias_set = session.execute(
                    select(Set).where(Set.game_id == game.id, Set.code == remote_pack_id)
                ).scalar_one_or_none()
                if alias_set is not None and alias_set.id != set_row.id:
                    self._merge_set_into_canonical(
                        session=session,
                        stats=stats,
                        source_set=alias_set,
                        canonical_set=set_row,
                        language=language,
                    )

        for card_item in payload.get("cards") or []:
            card_name = str(card_item.get("name") or "").strip()
            card_key = str(card_item.get("id") or "").strip().lower()
            if not card_name or not card_key:
                continue

            card_row = session.execute(select(Card).where(Card.game_id == game.id, Card.card_key == card_key)).scalar_one_or_none()
            if card_row is None:
                card_row = session.execute(select(Card).where(Card.game_id == game.id, Card.name == card_name)).scalar_one_or_none()

            if card_row is None:
                card_row = Card(game_id=game.id, name=card_name, card_key=card_key)
                session.add(card_row)
                session.flush()
                stats.records_inserted += 1
            else:
                changed = False
                if card_row.name != card_name:
                    card_row.name = card_name
                    changed = True
                if card_row.card_key != card_key:
                    card_row.card_key = card_key
                    changed = True
                if changed:
                    stats.records_updated += 1

            touched["card_ids"].add(card_row.id)

            for print_item in card_item.get("prints") or []:
                set_code = str(print_item.get("set_code") or "").strip().lower()
                set_row = sets_by_code.get(set_code)
                if set_row is None:
                    continue

                collector_number = str(print_item.get("collector_number") or "").strip()
                collector_number_norm = normalize_collector_number(collector_number)
                if not collector_number_norm:
                    continue

                variant = normalize_variant(print_item.get("variant"))
                print_key = f"onepiece:{set_code}:{collector_number_norm}:{language}:{variant}"
                rarity = str(print_item.get("rarity") or "").strip() or None
                external_print_id = str(print_item.get("id") or "").strip() or None

                print_row = session.execute(select(Print).where(Print.print_key == print_key)).scalar_one_or_none()
                if print_row is None:
                    print_row = session.execute(
                        select(Print).where(
                            Print.set_id == set_row.id,
                            Print.card_id == card_row.id,
                            Print.collector_number == collector_number,
                            Print.language == language,
                            Print.is_foil.is_(False),
                            Print.variant == variant,
                        )
                    ).scalar_one_or_none()

                if print_row is None:
                    print_row = Print(
                        set_id=set_row.id,
                        card_id=card_row.id,
                        collector_number=collector_number,
                        language=language,
                        rarity=rarity,
                        is_foil=False,
                        variant=variant,
                        print_key=print_key,
                    )
                    session.add(print_row)
                    session.flush()
                    stats.records_inserted += 1
                else:
                    changed = False
                    if print_row.rarity != rarity:
                        print_row.rarity = rarity
                        changed = True
                    if print_row.print_key != print_key:
                        print_row.print_key = print_key
                        changed = True
                    if changed:
                        stats.records_updated += 1

                touched["print_ids"].add(print_row.id)

                if external_print_id:
                    print_row = self._reconcile_print_identifier(
                        session=session,
                        stats=stats,
                        print_row=print_row,
                        external_print_id=external_print_id,
                    )

                image_url = str(print_item.get("image_url") or "").strip()
                if image_url:
                    primary_image = session.execute(
                        select(PrintImage).where(PrintImage.print_id == print_row.id, PrintImage.is_primary.is_(True))
                    ).scalar_one_or_none()
                    if primary_image is None:
                        session.add(
                            PrintImage(
                                print_id=print_row.id,
                                url=image_url,
                                is_primary=True,
                                source="punk_records",
                            )
                        )
                        stats.records_inserted += 1
                    elif primary_image.url != image_url:
                        primary_image.url = image_url
                        primary_image.source = "punk_records"
                        stats.records_updated += 1

        return touched
