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
from app.models import Card, Game, Print, PrintImage, Set, SourceRecord


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


    def should_skip_existing_record(self, existing_record: SourceRecord, **kwargs) -> bool:
        session = kwargs.get("session")
        if session is None:
            return True

        raw_payload = existing_record.raw_json or {}
        yugoprodeck_id = trim_or_none(raw_payload.get("id"))
        if not yugoprodeck_id:
            return True

        has_payload_primary_image = self._pick_best_image_url(raw_payload) is not None

        game_id = session.execute(select(Game.id).where(Game.slug == "yugioh")).scalar_one_or_none()
        if game_id is None:
            return False

        card_row = session.execute(
            select(Card).where(Card.game_id == game_id, Card.yugoprodeck_id == yugoprodeck_id)
        ).scalar_one_or_none()
        if card_row is None:
            return False

        normalized = self.normalize(raw_payload)
        normalized_prints = normalized.get("normalized_prints") or []
        if not normalized_prints:
            if not has_payload_primary_image:
                return True
            has_any_primary = session.execute(
                select(PrintImage.id)
                .join(Print, Print.id == PrintImage.print_id)
                .where(Print.card_id == card_row.id, PrintImage.is_primary.is_(True))
                .limit(1)
            ).scalar_one_or_none()
            return has_any_primary is not None

        sets_by_code = {
            row.code.lower().strip(): row
            for row in session.execute(select(Set).where(Set.game_id == game_id)).scalars().all()
        }

        for item in normalized_prints:
            set_code = (item.get("set_source_key") or "").lower().strip()
            set_row = sets_by_code.get(set_code)
            if set_row is None:
                return False

            print_row = self._find_existing_print(
                session,
                set_id=set_row.id,
                card_id=card_row.id,
                collector_number=item.get("collector_number") or item.get("collector_number_norm") or "unknown",
                collector_number_norm=item.get("collector_number_norm") or normalize_collector_number(item.get("collector_number") or "unknown"),
                normalized_language=normalize_language(item.get("language")),
                normalized_rarity=normalize_rarity(item.get("rarity")),
                variant=normalize_variant(item.get("variant_key")),
                ygo_print_id=next(
                    (ext.get("value") for ext in item.get("external_ids") or [] if ext.get("id_type") == "print_id"),
                    None,
                ),
                print_key=item.get("print_key"),
            )
            if print_row is None:
                print(
                    "[ygoprodeck_yugioh] incremental_rehydrate_required "
                    f"reason=missing_print yugoprodeck_id={yugoprodeck_id} "
                    f"set_code={set_code} collector_number={item.get('collector_number')}",
                    flush=True,
                )
                return False
            if print_row.card_id != card_row.id:
                return False
            incoming_print_key = trim_or_none(item.get("print_key"))
            if incoming_print_key and print_row.print_key != incoming_print_key:
                print(
                    "[ygoprodeck_yugioh] incremental_rehydrate_required "
                    f"reason=missing_or_mismatched_print_key yugoprodeck_id={yugoprodeck_id} "
                    f"print_id={print_row.id} incoming_print_key={incoming_print_key} "
                    f"current_print_key={print_row.print_key}",
                    flush=True,
                )
                return False

            incoming_ygo_print_id = next(
                (ext.get("value") for ext in item.get("external_ids") or [] if ext.get("id_type") == "print_id"),
                None,
            )
            if incoming_ygo_print_id:
                duplicate_rows = session.execute(
                    select(Print).where(Print.yugioh_id == incoming_ygo_print_id).order_by(Print.id.asc())
                ).scalars().all()
                for duplicate_row in duplicate_rows:
                    if duplicate_row.card_id != card_row.id:
                        return False
                    if incoming_print_key and duplicate_row.print_key != incoming_print_key:
                        return False
                    if has_payload_primary_image and not self._has_primary_image(session, duplicate_row.id):
                        return False

            if has_payload_primary_image and not self._has_primary_image(session, print_row.id):
                print(
                    "[ygoprodeck_yugioh] incremental_rehydrate_required "
                    f"reason=missing_primary_image yugoprodeck_id={yugoprodeck_id} "
                    f"print_id={print_row.id}",
                    flush=True,
                )
                return False

        return True

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

    def _prioritize_print_candidates(
        self,
        rows: list[Print],
        *,
        set_id: int,
        card_id: int,
        collector_number: str,
        collector_number_norm: str,
        incoming_variant: str,
        ygo_print_id: str | None,
        print_key: str | None,
    ) -> list[Print]:
        incoming_is_specific_variant = incoming_variant != "default"

        def _rank(row: Print) -> tuple[int, int, int, int, int, int, int, int]:
            row_collector = row.collector_number or ""
            row_collector_norm = normalize_collector_number(row_collector)
            row_variant = normalize_variant(row.variant)

            ygo_rank = 0 if ygo_print_id and row.yugioh_id == ygo_print_id else 1
            print_key_rank = 0 if print_key and row.print_key == print_key else 1
            set_rank = 0 if row.set_id == set_id else 1
            card_rank = 0 if row.card_id == card_id else 1
            exact_collector_rank = 0 if row_collector == collector_number else 1
            normalized_collector_rank = 0 if row_collector_norm == collector_number_norm else 1
            if incoming_is_specific_variant:
                variant_rank = 0 if row_variant == incoming_variant else 1
            else:
                variant_rank = 0 if row_variant != "default" else 1

            return (
                ygo_rank,
                set_rank,
                card_rank,
                exact_collector_rank,
                normalized_collector_rank,
                variant_rank,
                print_key_rank,
                row.id or 0,
            )

        return sorted(rows, key=_rank)

    @staticmethod
    def _has_primary_image(session, print_id: int) -> bool:
        return (
            session.execute(
                select(PrintImage.id)
                .where(PrintImage.print_id == print_id, PrintImage.is_primary.is_(True))
                .limit(1)
            ).scalar_one_or_none()
            is not None
        )

    def _collapse_duplicate_ygo_print_rows(
        self,
        session,
        *,
        print_row: Print,
        set_id: int,
        card_id: int,
        collector_number: str,
        collector_number_norm: str,
        variant: str,
        ygo_print_id: str,
        print_key: str | None,
        stats: IngestStats,
    ) -> Print:
        duplicate_rows = session.execute(
            select(Print).where(Print.yugioh_id == ygo_print_id).order_by(Print.id.asc())
        ).scalars().all()
        if len(duplicate_rows) <= 1:
            return print_row

        canonical_row = self._choose_first(
            self._prioritize_print_candidates(
                duplicate_rows,
                set_id=set_id,
                card_id=card_id,
                collector_number=collector_number,
                collector_number_norm=collector_number_norm,
                incoming_variant=variant,
                ygo_print_id=ygo_print_id,
                print_key=print_key,
            ),
            label="duplicate_prints",
            context=f"yugioh_id={ygo_print_id}",
        )
        if canonical_row is None:
            return print_row

        for row in duplicate_rows:
            if row.id == canonical_row.id:
                continue
            changed = False
            if row.yugioh_id == ygo_print_id:
                row.yugioh_id = None
                changed = True
            if print_key and row.print_key == print_key:
                row.print_key = None
                changed = True
            if changed:
                stats.records_updated += 1

        return canonical_row

    def _find_existing_print(
        self,
        session,
        *,
        set_id: int,
        card_id: int,
        collector_number: str,
        collector_number_norm: str,
        normalized_language: str,
        normalized_rarity: str,
        variant: str,
        ygo_print_id: str | None,
        print_key: str | None,
    ) -> Print | None:
        print_candidates = session.execute(
            select(Print).where(
                Print.set_id == set_id,
                Print.collector_number == collector_number,
                Print.language == normalized_language,
                Print.is_foil.is_(False),
            ).order_by(Print.id.asc())
        ).scalars().all()
        if ygo_print_id:
            print_candidates.extend(
                session.execute(
                    select(Print).where(Print.yugioh_id == ygo_print_id).order_by(Print.id.asc())
                ).scalars().all()
            )
        if print_key:
            print_candidates.extend(
                session.execute(
                    select(Print).where(Print.print_key == print_key).order_by(Print.id.asc())
                ).scalars().all()
            )

        unique_prints = {row.id: row for row in print_candidates}
        ordered_prints = self._prioritize_print_candidates(
            [unique_prints[key] for key in sorted(unique_prints)],
            set_id=set_id,
            card_id=card_id,
            collector_number=collector_number,
            collector_number_norm=collector_number_norm,
            incoming_variant=variant,
            ygo_print_id=ygo_print_id,
            print_key=print_key,
        )
        print_row = self._choose_first(
            ordered_prints,
            label="prints",
            context=f"set_id={set_id}, collector_number={collector_number}, yugioh_id={ygo_print_id}, print_key={print_key}",
        )
        if print_row is not None:
            return print_row

        fallback_candidates = session.execute(
            select(Print).where(
                Print.set_id == set_id,
                Print.card_id == card_id,
                Print.language == normalized_language,
                Print.is_foil.is_(False),
            ).order_by(Print.id.asc())
        ).scalars().all()
        normalized_matches = [
            row
            for row in fallback_candidates
            if normalize_collector_number(row.collector_number) == collector_number_norm
        ]
        if normalized_matches:
            return self._choose_first(
                self._prioritize_print_candidates(
                    normalized_matches,
                    set_id=set_id,
                    card_id=card_id,
                    collector_number=collector_number,
                    collector_number_norm=collector_number_norm,
                    incoming_variant=variant,
                    ygo_print_id=ygo_print_id,
                    print_key=print_key,
                ),
                label="prints_fallback",
                context=f"set_id={set_id}, card_id={card_id}, collector_number_norm={collector_number_norm}",
            )

        def _digits_tail(value: str) -> str:
            digits = "".join(ch for ch in value if ch.isdigit())
            return str(int(digits)) if digits else ""

        incoming_tail = _digits_tail(collector_number_norm)
        if incoming_tail:
            tail_matches = [
                row
                for row in fallback_candidates
                if _digits_tail(normalize_collector_number(row.collector_number)) == incoming_tail
            ]
            if tail_matches:
                return self._choose_first(
                    self._prioritize_print_candidates(
                        tail_matches,
                        set_id=set_id,
                        card_id=card_id,
                        collector_number=collector_number,
                        collector_number_norm=collector_number_norm,
                        incoming_variant=variant,
                        ygo_print_id=ygo_print_id,
                        print_key=print_key,
                    ),
                    label="prints_tail_fallback",
                    context=f"set_id={set_id}, card_id={card_id}, collector_number_norm={collector_number_norm}",
                )

        if len(fallback_candidates) == 1:
            return fallback_candidates[0]

        return None

    @staticmethod
    def _pick_best_image_url(payload: dict) -> str | None:
        """Select the best available image URL from YGOProDeck payload variants."""
        candidates: list[str] = []

        for image in payload.get("card_images") or []:
            for field in ("image_url", "image_url_small", "image_url_cropped"):
                value = (image.get(field) or "").strip()
                if value:
                    candidates.append(value)

        # Defensive fallback for payload variants that flatten image fields.
        for field in ("image_url", "image_url_small", "image_url_cropped"):
            value = (payload.get(field) or "").strip()
            if value:
                candidates.append(value)

        return candidates[0] if candidates else None

    @staticmethod
    def _choose_first(rows: list, *, label: str, context: str) -> object | None:
        if not rows:
            return None
        if len(rows) > 1:
            print(
                f"[ygoprodeck_yugioh] duplicate_{label} count={len(rows)} context={context}",
                flush=True,
            )
        return rows[0]

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

        card_image_url = self._pick_best_image_url(payload)

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
        touched_set_ids: set[int] = set()
        touched_print_ids: set[int] = set()
        touched_print_rows: list[Print] = []

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
            matching_cards = session.execute(
                select(Card).where(
                    Card.game_id == game.id, Card.yugoprodeck_id == ygo_card_id
                ).order_by(Card.id.asc())
            ).scalars().all()
            card_row = self._choose_first(
                matching_cards,
                label="cards",
                context=f"yugoprodeck_id={ygo_card_id}",
            )
        if card_row is None:
            matching_cards = session.execute(
                select(Card).where(Card.game_id == game.id, Card.name == card_name).order_by(Card.id.asc())
            ).scalars().all()
            card_row = self._choose_first(
                matching_cards,
                label="cards",
                context=f"name={card_name}",
            )

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
                matching_sets = session.execute(
                    select(Set).where(
                        Set.game_id == game.id, Set.yugioh_id == ygo_set_id
                    ).order_by(Set.id.asc())
                ).scalars().all()
                set_row = self._choose_first(
                    matching_sets,
                    label="sets",
                    context=f"set_code={code}, yugioh_id={ygo_set_id}",
                )
            if set_row is None:
                matching_sets = session.execute(
                    select(Set).where(Set.game_id == game.id, Set.code == code).order_by(Set.id.asc())
                ).scalars().all()
                set_row = self._choose_first(
                    matching_sets,
                    label="sets",
                    context=f"set_code={code}",
                )

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
            if set_row.id is not None:
                touched_set_ids.add(set_row.id)

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
            print_key = trim_or_none(item.print_key) or build_print_key(
                game_slug="yugioh",
                set_code=set_row.code,
                collector_number=collector_number,
                language=normalized_language,
                finish="nonfoil",
                variant=variant,
            )

            print_row = self._find_existing_print(
                session,
                set_id=set_row.id,
                card_id=card_row.id,
                collector_number=collector_number,
                collector_number_norm=item.collector_number_norm,
                normalized_language=normalized_language,
                normalized_rarity=normalized_rarity,
                variant=variant,
                ygo_print_id=ygo_print_id,
                print_key=print_key,
            )
            if print_row is not None and ygo_print_id:
                print_row = self._collapse_duplicate_ygo_print_rows(
                    session,
                    print_row=print_row,
                    set_id=set_row.id,
                    card_id=card_row.id,
                    collector_number=collector_number,
                    collector_number_norm=item.collector_number_norm,
                    variant=variant,
                    ygo_print_id=ygo_print_id,
                    print_key=print_key,
                    stats=stats,
                )

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
                touched_print_rows.append(print_row)
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
                existing_rarity = normalize_rarity(print_row.rarity)
                incoming_rarity_is_specific = normalized_rarity != "unknown"
                existing_rarity_is_specific = existing_rarity != "unknown"
                if incoming_rarity_is_specific and existing_rarity_is_specific:
                    pass
                elif incoming_rarity_is_specific or not existing_rarity_is_specific:
                    if print_row.rarity != normalized_rarity:
                        print_row.rarity = normalized_rarity
                        changed = True

                if print_row.language != normalized_language:
                    print_row.language = normalized_language
                    changed = True

                existing_variant = normalize_variant(print_row.variant)
                incoming_variant_is_specific = variant != "default"
                existing_variant_is_specific = existing_variant != "default"
                if incoming_variant_is_specific and existing_variant_is_specific:
                    pass
                elif incoming_variant_is_specific or not existing_variant_is_specific:
                    if print_row.variant != variant:
                        print_row.variant = variant
                        changed = True
                if print_key and print_row.print_key != print_key:
                    print_row.print_key = print_key
                    changed = True
                if changed:
                    stats.records_updated += 1
                touched_print_rows.append(print_row)

            image = images_by_print_source_key.get(item.source_key)
            if image and image.url:
                if print_row.id is None:
                    session.flush()
                primary_images = session.execute(
                    select(PrintImage).where(
                        PrintImage.print_id == print_row.id,
                        PrintImage.is_primary.is_(True),
                    ).order_by(PrintImage.id.asc())
                ).scalars().all()
                primary_image = self._choose_first(
                    primary_images,
                    label="print_images",
                    context=f"print_id={print_row.id}",
                )
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

                for extra_primary in primary_images[1:]:
                    if extra_primary.is_primary:
                        extra_primary.is_primary = False
                        stats.records_updated += 1

            if print_row.id is not None:
                touched_print_ids.add(print_row.id)

        session.flush()
        if card_row.id is None:
            raise ValueError("card row id missing after upsert flush")

        touched_set_ids.update(row.id for row in sets_by_code.values() if row.id is not None)
        touched_print_ids.update(row.id for row in touched_print_rows if row.id is not None)

        return {
            "card_id": card_row.id,
            "set_ids": touched_set_ids,
            "print_ids": touched_print_ids,
        }
