from __future__ import annotations

import json
import os
import re
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
        for raw_pack in self._iter_pack_records(packs_payload):
            title_parts = raw_pack.get("title_parts") if isinstance(raw_pack.get("title_parts"), dict) else {}
            set_code = str(
                self._record_get(raw_pack, "code", "pack_id", "set_code", "id")
                or title_parts.get("label")
                or raw_pack.get("raw_title")
                or ""
            ).strip().lower()
            if not set_code:
                continue
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
                set_code = str(self._record_get(raw_card, "pack_id", "set_code", "set", "set_id") or pack_code).strip().lower()
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
                if not variant:
                    variant = "default"

                cards_by_key[card_id]["prints"].append(
                    {
                        "id": str(self._record_get(raw_card, "id", "code", "external_id") or "").strip() or None,
                        "set_code": set_code,
                        "collector_number": collector,
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

    def _load_remote(self) -> dict:
        root_url = self._punkrecords_root_url()
        language = self._punkrecords_language()
        packs_url = self._build_url(root_url, f"{language}/packs.json")
        timeout = self._http_timeout()

        packs_response = requests.get(packs_url, timeout=timeout)
        packs_response.raise_for_status()
        packs_payload = packs_response.json()

        cards_payload_by_pack: dict[str, object] = {}
        pack_records = list(self._iter_pack_records(packs_payload))
        for raw_pack in pack_records:
            pack_id = str(self._record_get(raw_pack, "id", "code", "pack_id", "set_code") or "").strip()
            if not pack_id:
                self.logger.warning("ingest onepiece remote pack_skipped missing_pack_id")
                continue

            card_urls = self._fetch_pack_card_file_urls(
                root_url=root_url,
                language=language,
                timeout=timeout,
                pack_id=pack_id or "unknown",
            )
            if not card_urls:
                continue

            cards_payload_by_pack[pack_id.lower()] = []
            for card_url in card_urls:
                payload = self._fetch_remote_json(url=card_url, timeout=timeout)
                if isinstance(payload, dict):
                    cards_payload_by_pack[pack_id.lower()].append(payload)

        normalized = self._normalize_remote_payload(
            packs_payload=packs_payload,
            cards_payload_by_pack=cards_payload_by_pack,
            language=language,
        )
        if pack_records and not normalized.get("cards"):
            raise ValueError("One Piece remote ingest resolved packs.json but found zero cards across all packs")
        return normalized

    @staticmethod
    def _github_api_repo_context(root_url: str) -> tuple[str, str, str, str] | None:
        pattern = r"^https://raw\.githubusercontent\.com/([^/]+)/([^/]+)/([^/]+)(?:/(.*))?$"
        match = re.match(pattern, root_url.strip().rstrip("/"))
        if not match:
            return None
        owner, repo, ref, suffix = match.groups()
        return owner, repo, ref, (suffix or "").strip("/")

    def _build_cards_directory_api_url(self, *, root_url: str, language: str, pack_id: str) -> str | None:
        override_base = self._env("ONEPIECE_PUNKRECORDS_CONTENTS_API_BASE_URL", "")
        if override_base:
            return self._build_url(override_base, f"{language}/cards/{pack_id}")

        github_context = self._github_api_repo_context(root_url)
        if github_context is None:
            return None
        owner, repo, ref, suffix = github_context
        path_prefix = f"{suffix}/" if suffix else ""
        path = f"{path_prefix}{language}/cards/{pack_id}"
        return f"https://api.github.com/repos/{owner}/{repo}/contents/{path}?ref={ref}"

    def _fetch_remote_json(self, *, url: str, timeout: int) -> object | None:
        try:
            response = requests.get(url, timeout=timeout)
            response.raise_for_status()
            return response.json()
        except (requests.RequestException, ValueError):
            return None

    def _fetch_pack_card_file_urls(
        self,
        *,
        root_url: str,
        language: str,
        timeout: int,
        pack_id: str,
    ) -> list[str]:
        directory_api_url = self._build_cards_directory_api_url(root_url=root_url, language=language, pack_id=pack_id)
        if not directory_api_url:
            self.logger.warning("ingest onepiece remote pack_skipped pack=%s reason=missing_directory_api", pack_id)
            return []

        payload = self._fetch_remote_json(url=directory_api_url, timeout=timeout)
        if not isinstance(payload, list):
            self.logger.warning("ingest onepiece remote pack_skipped pack=%s reason=invalid_directory_listing", pack_id)
            return []

        file_urls: list[str] = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            if str(item.get("type") or "").lower() != "file":
                continue
            name = str(item.get("name") or "").strip().lower()
            if not name.endswith(".json"):
                continue
            download_url = str(item.get("download_url") or "").strip()
            path = str(item.get("path") or "").strip()
            candidate = download_url or (self._build_url(root_url, path) if path else "")
            if candidate:
                file_urls.append(candidate)

        if not file_urls:
            self.logger.warning("ingest onepiece remote pack_skipped pack=%s reason=no_json_files", pack_id)
        return sorted(file_urls)

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
        language = str(payload.get("language") or "en").strip().lower() or "en"

        touched = {"card_ids": set(), "set_ids": set(), "print_ids": set()}
        sets_by_code: dict[str, Set] = {}

        for item in payload.get("sets") or []:
            set_code = str(item.get("code") or "").strip().lower()
            if not set_code:
                continue

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
                    identifier = session.execute(
                        select(PrintIdentifier).where(
                            PrintIdentifier.print_id == print_row.id,
                            PrintIdentifier.source == "punk_records",
                        )
                    ).scalar_one_or_none()
                    if identifier is None:
                        session.add(
                            PrintIdentifier(
                                print_id=print_row.id,
                                source="punk_records",
                                external_id=external_print_id,
                            )
                        )
                        stats.records_inserted += 1
                    elif identifier.external_id != external_print_id:
                        identifier.external_id = external_print_id
                        stats.records_updated += 1

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
