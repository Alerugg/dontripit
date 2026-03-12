from __future__ import annotations

import json
import time
from math import inf
from pathlib import Path
import requests
from sqlalchemy import select

from app.ingest.base import IngestStats, SourceConnector
from app.ingest.normalization import (
    build_card_key,
    build_print_key,
    canonical_text_slug,
    normalize_collector_number,
    normalize_finish,
    normalize_language,
    normalize_rarity,
    normalize_variant,
    trim_or_none,
)
from app.models import Card, Game, Print, PrintImage, Set


class YgoProDeckYugiohConnector(SourceConnector):
    name = "ygoprodeck_yugioh"
    uses_normalized_payload = True
    base_url = "https://db.ygoprodeck.com/api/v7"
    default_headers = {
        "User-Agent": "API-PROJECT/1.0 (+https://github.com/Alerugg/API-PROJECT)",
        "Accept": "application/json",
    }

    def load(
        self, path: str | Path | None = None, **kwargs
    ) -> list[tuple[Path, dict, str]]:
        fixture = bool(kwargs.get("fixture", False))
        limit = kwargs.get("limit")

        if fixture:
            fixture_path = self._resolve_fixture_path(path)
            cards = self._load_fixture(fixture_path, limit=limit)
        else:
            cards = self._load_remote(
                limit=limit,
                base_url=kwargs.get("base_url") or self.base_url,
                page_size=kwargs.get("page_size"),
            )

        payloads: list[tuple[Path, dict, str]] = []
        for idx, card in enumerate(cards):
            payloads.append(
                (
                    Path(f"yugioh_card_{card.get('id', idx)}.json"),
                    card,
                    self.checksum(card),
                )
            )
        return payloads

    def _resolve_fixture_path(self, path: str | Path | None) -> Path:
        fixture_name = "ygoprodeck_yugioh_sample.json"
        root = Path(__file__).resolve().parents[3]
        candidate = Path(path) if path else root / "data" / "fixtures" / fixture_name
        if candidate.is_file():
            return candidate
        for option in (
            root / str(candidate),
            root.parent / str(candidate),
            root / "data" / "fixtures" / fixture_name,
        ):
            if option.is_file():
                return option
            if option.is_dir() and (option / fixture_name).is_file():
                return option / fixture_name
        raise ValueError(f"Unable to resolve Yugioh fixture path: {path}")

    def _load_fixture(self, fixture_path: Path, limit: int | None = None) -> list[dict]:
        payload = json.loads(fixture_path.read_text(encoding="utf-8"))
        cards = payload.get("data") or []
        if limit:
            return cards[:limit]
        return cards

    def _load_remote(
        self,
        limit: int | None = None,
        base_url: str | None = None,
        page_size: int | None = None,
    ) -> list[dict]:
        endpoint = f"{base_url or self.base_url}/cardinfo.php"
        normalized_page_size = max(int(page_size or 500), 1)
        requested_limit = None if limit is None else max(int(limit), 0)
        target = inf if requested_limit is None else requested_limit

        cards: list[dict] = []
        seen_keys: set[str] = set()
        pages_requested = 0
        downloaded_cards = 0
        duplicate_cards = 0
        offset = 0

        while len(cards) < target:
            remaining = None if requested_limit is None else requested_limit - len(cards)
            if remaining is not None and remaining <= 0:
                break

            batch_size = (
                normalized_page_size
                if remaining is None
                else min(normalized_page_size, max(remaining, 1))
            )
            payload = self._request_json(
                endpoint,
                params={"num": batch_size, "offset": offset},
            )
            pages_requested += 1

            page_cards = payload.get("data") or []
            downloaded_cards += len(page_cards)
            if not page_cards:
                break

            for card in page_cards:
                dedupe_key = str(card.get("id") or self.checksum(card))
                if dedupe_key in seen_keys:
                    duplicate_cards += 1
                    continue
                seen_keys.add(dedupe_key)
                cards.append(card)
                if len(cards) >= target:
                    break

            if len(page_cards) < batch_size:
                break
            offset += batch_size

        print(
            "[ygoprodeck_yugioh] load_remote_done "
            f"pages={pages_requested} downloaded={downloaded_cards} "
            f"deduped={duplicate_cards} limit={requested_limit} "
            f"page_size={normalized_page_size} returned={len(cards)}",
            flush=True,
        )
        return cards

    def _request_json(self, url: str, params: dict | None = None):
        wait_seconds = 1.0
        last_error: Exception | None = None
        status_for_retry = {403, 429, 500, 502, 503, 504}
        for attempt in range(1, 6):
            try:
                response = requests.get(
                    url,
                    params=params,
                    headers=self.default_headers,
                    timeout=45,
                )
                if response.status_code in status_for_retry:
                    body_preview = response.text[:240].replace("\n", " ")
                    print(
                        f"[ygoprodeck_yugioh] retryable_status attempt={attempt} status={response.status_code} wait={wait_seconds:.1f}s body={body_preview}",
                        flush=True,
                    )
                    time.sleep(wait_seconds)
                    wait_seconds *= 2
                    continue
                response.raise_for_status()
                return response.json()
            except requests.RequestException as exc:
                last_error = exc
                if attempt >= 5:
                    break
                print(
                    f"[ygoprodeck_yugioh] request_error attempt={attempt} wait={wait_seconds:.1f}s error={exc}",
                    flush=True,
                )
                time.sleep(wait_seconds)
                wait_seconds *= 2

        raise RuntimeError(
            f"YGOProDeck request failed after retries: {url} last_error={last_error}"
        )

    @classmethod
    def _derive_variant(cls, set_payload: dict) -> str:
        raw_variant = (
            set_payload.get("set_rarity")
            or set_payload.get("rarity")
            or set_payload.get("set_rarity_code")
            or set_payload.get("set_rarity_short")
            or ""
        )
        return normalize_variant(raw_variant)

    @staticmethod
    def _pick_best_image_url(images: list[dict] | None) -> str | None:
        """Select the best available image URL from YGOProDeck image variants."""
        candidates: list[str] = []
        for image in images or []:
            for field in ("image_url", "image_url_small", "image_url_cropped"):
                value = (image.get(field) or "").strip()
                if value:
                    candidates.append(value)
        return candidates[0] if candidates else None

    def normalize(self, payload: dict, **kwargs) -> dict:
        card_name = trim_or_none(payload.get("name")) or ""
        card_external_id = trim_or_none(payload.get("id"))
        card_external_ids = []
        if card_external_id:
            card_external_ids.append(
                {
                    "source": "ygoprodeck",
                    "id_type": "card_id",
                    "value": card_external_id,
                }
            )
        card_key = build_card_key(
            game_slug="yugioh",
            canonical_name=card_name,
            identity_hints={},
            external_ids=card_external_ids,
        )

        card_sets = payload.get("card_sets") or []
        normalized_sets_by_key: dict[str, dict] = {}
        normalized_prints = []
        seen_print_keys: set[str] = set()
        for idx, set_payload in enumerate(card_sets):
            set_code = (set_payload.get("set_code") or "").strip().lower()
            set_name = (set_payload.get("set_name") or set_code or "unknown").strip()
            set_external_id = (set_payload.get("set_code") or "").strip() or None
            collector_number_raw = (
                set_payload.get("set_code") or f"{payload.get('id', '')}-{idx+1}"
            ).strip()
            print_external_id = f"{payload.get('id')}::{set_payload.get('set_code')}::{idx+1}"
            normalized_set_code = set_code or f"set-{idx+1}"
            normalized_set_source_key = normalized_set_code
            normalized_sets_by_key.setdefault(
                normalized_set_source_key,
                {
                    "source_key": normalized_set_source_key,
                    "code": normalized_set_code,
                    "name": set_name,
                    "external_ids": ([{"source": "ygoprodeck", "id_type": "set_code", "value": set_external_id}] if set_external_id else []),
                    "raw": set_payload,
                },
            )

            collector_number_norm = normalize_collector_number(collector_number_raw)
            normalized_language = normalize_language(set_payload.get("set_language") or payload.get("language"))
            normalized_variant = self._derive_variant(set_payload)
            finish = normalize_finish(is_foil=False, variant=normalized_variant)
            print_key = build_print_key(
                card_key=card_key,
                set_code=normalized_set_code,
                collector_number=collector_number_norm,
                language=normalized_language,
                finish=finish,
                variant=normalized_variant,
            )
            if print_key in seen_print_keys:
                continue
            seen_print_keys.add(print_key)

            normalized_prints.append(
                {
                    "source_key": print_external_id,
                    "set_source_key": normalized_set_source_key,
                    "collector_number": collector_number_raw,
                    "collector_number_norm": collector_number_norm,
                    "rarity": normalize_rarity(set_payload.get("set_rarity")),
                    "variant_key": normalized_variant,
                    "language": normalized_language,
                    "finish": finish,
                    "print_key": print_key,
                    "external_ids": [{"source": "ygoprodeck", "id_type": "print_id", "value": print_external_id}],
                    "raw": set_payload,
                }
            )

        if not normalized_sets_by_key:
            fallback_code = f"misc-{payload.get('id')}"
            normalized_sets_by_key[fallback_code] = {
                "source_key": fallback_code,
                "code": fallback_code,
                "name": "Misc",
                "external_ids": [],
                "raw": {},
            }
            fallback_print_key = build_print_key(
                card_key=card_key,
                set_code=fallback_code,
                collector_number=str(payload.get("id") or fallback_code),
                language="en",
                finish="nonfoil",
                variant="default",
            )
            normalized_prints = [
                {
                    "source_key": str(payload.get("id") or fallback_code),
                    "set_source_key": fallback_code,
                    "collector_number": str(payload.get("id") or fallback_code),
                    "collector_number_norm": normalize_collector_number(str(payload.get("id") or fallback_code)),
                    "language": "en",
                    "finish": "nonfoil",
                    "rarity": "unknown",
                    "variant_key": "default",
                    "print_key": fallback_print_key,
                    "external_ids": [{"source": "ygoprodeck", "id_type": "print_id", "value": str(payload.get("id") or fallback_code)}],
                    "raw": {},
                }
            ]

        card_image_url = self._pick_best_image_url(payload.get("card_images"))

        normalized_images = []
        if card_image_url:
            for print_item in normalized_prints:
                normalized_images.append(
                    {
                        "print_source_key": print_item["source_key"],
                        "url": card_image_url,
                        "is_primary": True,
                        "source": "ygoprodeck",
                        "image_type": "card",
                    }
                )

        first_set = next(iter(normalized_sets_by_key.values()))
        legacy_sets = [
            {
                "code": item.get("code"),
                "name": item.get("name"),
                "yugioh_id": next((ext.get("value") for ext in item.get("external_ids") or [] if ext.get("id_type") == "set_code"), None),
            }
            for item in normalized_sets_by_key.values()
        ]
        legacy_prints = [
            {
                "set_code": item.get("set_source_key"),
                "collector_number": item.get("collector_number"),
                "rarity": item.get("rarity"),
                "variant": item.get("variant_key"),
                "language": item.get("language"),
                "yugioh_id": next((ext.get("value") for ext in item.get("external_ids") or [] if ext.get("id_type") == "print_id"), None),
            }
            for item in normalized_prints
        ]

        return {
            "normalized_game": {"slug": "yugioh", "name": "Yu-Gi-Oh!"},
            "normalized_set": first_set,
            "normalized_card": {
                "source_key": card_external_id or canonical_text_slug(card_name),
                "canonical_name": card_name,
                "name_normalized": canonical_text_slug(card_name),
                "card_key": card_key,
                "identity_hints": {},
                "external_ids": card_external_ids,
                "raw": payload,
            },
            "normalized_sets": list(normalized_sets_by_key.values()),
            "normalized_prints": normalized_prints,
            "normalized_images": normalized_images,
            "normalized_external_ids": [],
            "source_metadata": {"connector": self.name},
            "card": {"name": card_name, "yugoprodeck_id": card_external_id},
            "sets": legacy_sets,
            "prints": legacy_prints,
            "card_image_url": card_image_url,
        }

    def upsert(self, session, payload: dict, stats: IngestStats, **kwargs) -> dict:
        game = session.execute(
            select(Game).where(Game.slug == "yugioh")
        ).scalar_one_or_none()
        if game is None:
            game = Game(slug="yugioh", name="Yu-Gi-Oh!")
            session.add(game)
            session.flush()
            stats.records_inserted += 1

        normalized = payload.get("_normalized_contract")
        if normalized is None:
            raise ValueError("normalized contract not parsed for ygoprodeck connector")

        card_name = normalized.normalized_card.canonical_name
        if not card_name:
            return {}
        ygo_card_id = next((item.value for item in normalized.normalized_card.external_ids if item.id_type == "card_id"), None)
        card_key = normalized.normalized_card.card_key

        card_row = None
        if ygo_card_id:
            card_row = session.execute(
                select(Card).where(
                    Card.game_id == game.id, Card.yugoprodeck_id == ygo_card_id
                )
            ).scalar_one_or_none()
        if card_row is None:
            card_row = session.execute(
                select(Card).where(Card.game_id == game.id, Card.name == card_name)
            ).scalar_one_or_none()

        if card_row is None:
            card_row = Card(game_id=game.id, name=card_name, yugoprodeck_id=ygo_card_id, card_key=card_key)
            session.add(card_row)
            session.flush()
            stats.records_inserted += 1
        else:
            changed = False
            if ygo_card_id and card_row.yugoprodeck_id != ygo_card_id:
                card_row.yugoprodeck_id = ygo_card_id
                changed = True
            if card_row.name != card_name:
                card_row.name = card_name
                changed = True
            if card_row.card_key != card_key:
                card_row.card_key = card_key
                changed = True
            if changed:
                stats.records_updated += 1

        sets_by_code: dict[str, Set] = {}
        normalized_sets = normalized.normalized_sets or [normalized.normalized_set]
        for item in normalized_sets:
            code = item.code.lower().strip()
            if not code:
                continue
            ygo_set_id = next((ext.value for ext in item.external_ids if ext.id_type == "set_code"), None)
            set_row = None
            if ygo_set_id:
                set_row = session.execute(
                    select(Set).where(
                        Set.game_id == game.id, Set.yugioh_id == ygo_set_id
                    )
                ).scalar_one_or_none()
            if set_row is None:
                set_row = session.execute(
                    select(Set).where(Set.game_id == game.id, Set.code == code)
                ).scalar_one_or_none()

            if set_row is None:
                set_row = Set(
                    game_id=game.id,
                    code=code,
                    name=item.name or code.upper(),
                    yugioh_id=ygo_set_id,
                )
                session.add(set_row)
                session.flush()
                stats.records_inserted += 1
            else:
                changed = False
                if item.name and set_row.name != item.name:
                    set_row.name = item.name
                    changed = True
                if ygo_set_id and set_row.yugioh_id != ygo_set_id:
                    set_row.yugioh_id = ygo_set_id
                    changed = True
                if changed:
                    stats.records_updated += 1
            sets_by_code[code] = set_row

        images_by_print_source_key = {img.print_source_key: img for img in normalized.normalized_images if img.is_primary}

        for item in normalized.normalized_prints:
            set_code = item.set_source_key.lower().strip()
            set_row = sets_by_code.get(set_code)
            if not set_row:
                continue
            collector_number = item.collector_number or item.collector_number_norm or "unknown"
            ygo_print_id = next((ext.value for ext in item.external_ids if ext.id_type == "print_id"), None)
            normalized_language = normalize_language(item.language)
            normalized_rarity = normalize_rarity(item.rarity)
            variant = normalize_variant(item.variant_key)
            print_key = item.print_key

            print_row = session.execute(
                select(Print).where(
                    Print.set_id == set_row.id,
                    Print.collector_number == collector_number,
                    Print.language == normalized_language,
                    Print.is_foil.is_(False),
                    Print.variant == variant,
                )
            ).scalar_one_or_none()
            if print_row is None and ygo_print_id:
                print_row = session.execute(
                    select(Print).where(Print.yugioh_id == ygo_print_id)
                ).scalar_one_or_none()
            if print_row is None and print_key:
                print_row = session.execute(select(Print).where(Print.print_key == print_key)).scalar_one_or_none()

            if print_row is None:
                print_row = Print(
                    set_id=set_row.id,
                    card_id=card_row.id,
                    collector_number=collector_number,
                    rarity=normalized_rarity,
                    language=normalized_language,
                    variant=variant,
                    yugioh_id=ygo_print_id,
                    print_key=print_key,
                )
                session.add(print_row)
                stats.records_inserted += 1
            else:
                changed = False
                if print_row.card_id != card_row.id:
                    print_row.card_id = card_row.id
                    changed = True
                if print_row.set_id != set_row.id:
                    print_row.set_id = set_row.id
                    changed = True
                if ygo_print_id and print_row.yugioh_id != ygo_print_id:
                    print_row.yugioh_id = ygo_print_id
                    changed = True
                if print_row.rarity != normalized_rarity:
                    print_row.rarity = normalized_rarity
                    changed = True
                if print_row.language != normalized_language:
                    print_row.language = normalized_language
                    changed = True
                if variant != "default" and print_row.variant != variant:
                    print_row.variant = variant
                    changed = True
                if print_key and print_row.print_key != print_key:
                    print_row.print_key = print_key
                    changed = True
                if changed:
                    stats.records_updated += 1

            image = images_by_print_source_key.get(item.source_key)
            if image and image.url:
                primary_image = session.execute(
                    select(PrintImage).where(
                        PrintImage.print_id == print_row.id,
                        PrintImage.is_primary.is_(True),
                    )
                ).scalar_one_or_none()
                if primary_image is None:
                    session.add(
                        PrintImage(
                            print_id=print_row.id,
                            url=image.url,
                            is_primary=True,
                            source="ygoprodeck",
                        )
                    )
                    stats.records_inserted += 1
                elif primary_image.url != image.url:
                    primary_image.url = image.url
                    if primary_image.source != "ygoprodeck":
                        primary_image.source = "ygoprodeck"
                    stats.records_updated += 1

        return {"card_id": card_row.id}
