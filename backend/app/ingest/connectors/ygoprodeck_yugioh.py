from __future__ import annotations

import json
import re
import time
from pathlib import Path

import requests
from sqlalchemy import select

from app.ingest.base import IngestStats, SourceConnector
from app.models import Card, Game, Print, Set


class YgoProDeckYugiohConnector(SourceConnector):
    name = "ygoprodeck_yugioh"
    base_url = "https://db.ygoprodeck.com/api/v7"

    def load(self, path: str | Path | None = None, **kwargs) -> list[tuple[Path, dict, str]]:
        fixture = bool(kwargs.get("fixture", False))
        limit = kwargs.get("limit")

        if fixture:
            fixture_path = self._resolve_fixture_path(path)
            cards = self._load_fixture(fixture_path, limit=limit)
        else:
            cards = self._load_remote(limit=limit, base_url=kwargs.get("base_url") or self.base_url)

        payloads: list[tuple[Path, dict, str]] = []
        for idx, card in enumerate(cards):
            payloads.append((Path(f"yugioh_card_{card.get('id', idx)}.json"), card, self.checksum(card)))
        return payloads

    def _resolve_fixture_path(self, path: str | Path | None) -> Path:
        fixture_name = "ygoprodeck_yugioh_sample.json"
        root = Path(__file__).resolve().parents[3]
        candidate = Path(path) if path else root / "data" / "fixtures" / fixture_name
        if candidate.is_file():
            return candidate
        for option in (root / str(candidate), root.parent / str(candidate), root / "data" / "fixtures" / fixture_name):
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

    def _load_remote(self, limit: int | None = None, base_url: str | None = None) -> list[dict]:
        endpoint = f"{base_url or self.base_url}/cardinfo.php"
        payload = self._request_json(endpoint)
        cards = payload.get("data") or []
        if limit:
            return cards[:limit]
        return cards

    def _request_json(self, url: str, params: dict | None = None):
        wait_seconds = 0.3
        for _ in range(6):
            response = requests.get(url, params=params, timeout=30)
            if response.status_code in (429, 500, 502, 503, 504):
                time.sleep(wait_seconds)
                wait_seconds *= 2
                continue
            response.raise_for_status()
            return response.json()
        raise RuntimeError(f"YGOProDeck request failed after retries: {url}")

    @staticmethod
    def _normalize_language(value: object) -> str:
        language = str(value or "").strip().lower()
        return language or "en"

    @staticmethod
    def _normalize_rarity(value: object) -> str | None:
        rarity = str(value or "").strip()
        return rarity or None

    @staticmethod
    def _variant_from_rarity(value: object) -> str:
        rarity = str(value or "").strip().lower()
        if not rarity:
            return "default"
        rarity = rarity.replace(" ", "-")
        rarity = re.sub(r"[^a-z0-9-]", "", rarity)
        rarity = re.sub(r"-+", "-", rarity).strip("-")
        return rarity or "default"

    def normalize(self, payload: dict, **kwargs) -> dict:
        card_sets = payload.get("card_sets") or []
        normalized_sets = []
        normalized_prints = []
        for idx, set_payload in enumerate(card_sets):
            set_code = (set_payload.get("set_code") or "").strip().lower()
            set_name = (set_payload.get("set_name") or set_code or "unknown").strip()
            set_external_id = (set_payload.get("set_code") or "").strip() or None
            collector_number = (set_payload.get("set_code") or f"{payload.get('id', '')}-{idx+1}").strip()
            print_external_id = f"{payload.get('id')}::{set_payload.get('set_code')}::{idx+1}"
            normalized_sets.append({"code": set_code or f"set-{idx+1}", "name": set_name, "yugioh_id": set_external_id})
            normalized_prints.append(
                {
                    "set_code": set_code or f"set-{idx+1}",
                    "collector_number": collector_number,
                    "rarity": self._normalize_rarity(set_payload.get("set_rarity")),
                    "language": self._normalize_language(set_payload.get("set_language") or payload.get("language")),
                    "yugioh_id": print_external_id,
                }
            )

        if not normalized_sets:
            fallback_code = f"misc-{payload.get('id')}"
            normalized_sets = [{"code": fallback_code, "name": "Misc", "yugioh_id": None}]
            normalized_prints = [
                {
                    "set_code": fallback_code,
                    "collector_number": str(payload.get("id") or fallback_code),
                    "rarity": None,
                    "language": "en",
                    "yugioh_id": str(payload.get("id") or fallback_code),
                }
            ]

        return {
            "card": {
                "name": (payload.get("name") or "").strip(),
                "yugoprodeck_id": str(payload.get("id")) if payload.get("id") is not None else None,
            },
            "sets": normalized_sets,
            "prints": normalized_prints,
        }

    def upsert(self, session, payload: dict, stats: IngestStats, **kwargs) -> dict:
        game = session.execute(select(Game).where(Game.slug == "yugioh")).scalar_one_or_none()
        if game is None:
            game = Game(slug="yugioh", name="Yu-Gi-Oh!")
            session.add(game)
            session.flush()
            stats.records_inserted += 1

        card_payload = payload.get("card") or {}
        card_name = (card_payload.get("name") or "").strip()
        if not card_name:
            return {}
        ygo_card_id = card_payload.get("yugoprodeck_id")

        card_row = None
        if ygo_card_id:
            card_row = session.execute(
                select(Card).where(Card.game_id == game.id, Card.yugoprodeck_id == ygo_card_id)
            ).scalar_one_or_none()
        if card_row is None:
            card_row = session.execute(select(Card).where(Card.game_id == game.id, Card.name == card_name)).scalar_one_or_none()

        if card_row is None:
            card_row = Card(game_id=game.id, name=card_name, yugoprodeck_id=ygo_card_id)
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
            if changed:
                stats.records_updated += 1

        sets_by_code: dict[str, Set] = {}
        for item in payload.get("sets") or []:
            code = (item.get("code") or "").lower().strip()
            if not code:
                continue
            ygo_set_id = item.get("yugioh_id")
            set_row = None
            if ygo_set_id:
                set_row = session.execute(select(Set).where(Set.game_id == game.id, Set.yugioh_id == ygo_set_id)).scalar_one_or_none()
            if set_row is None:
                set_row = session.execute(select(Set).where(Set.game_id == game.id, Set.code == code)).scalar_one_or_none()

            if set_row is None:
                set_row = Set(game_id=game.id, code=code, name=item.get("name") or code.upper(), yugioh_id=ygo_set_id)
                session.add(set_row)
                session.flush()
                stats.records_inserted += 1
            else:
                changed = False
                if item.get("name") and set_row.name != item.get("name"):
                    set_row.name = item.get("name")
                    changed = True
                if ygo_set_id and set_row.yugioh_id != ygo_set_id:
                    set_row.yugioh_id = ygo_set_id
                    changed = True
                if changed:
                    stats.records_updated += 1
            sets_by_code[code] = set_row

        for item in payload.get("prints") or []:
            set_code = (item.get("set_code") or "").lower().strip()
            set_row = sets_by_code.get(set_code)
            if not set_row:
                continue
            collector_number = str(item.get("collector_number") or "").strip() or "unknown"
            ygo_print_id = item.get("yugioh_id")
            normalized_language = self._normalize_language(item.get("language"))
            normalized_rarity = self._normalize_rarity(item.get("rarity"))
            variant = self._variant_from_rarity(item.get("rarity"))

            print_row = None
            if ygo_print_id:
                print_row = session.execute(select(Print).where(Print.yugioh_id == ygo_print_id)).scalar_one_or_none()
            if print_row is None:
                print_row = session.execute(
                    select(Print).where(
                        Print.set_id == set_row.id,
                        Print.card_id == card_row.id,
                        Print.collector_number == collector_number,
                        Print.language == normalized_language,
                        Print.is_foil.is_(False),
                        Print.variant == variant,
                    )
                ).scalar_one_or_none()

            if print_row is None:
                print_row = Print(
                    set_id=set_row.id,
                    card_id=card_row.id,
                    collector_number=collector_number,
                    rarity=normalized_rarity,
                    language=normalized_language,
                    variant=variant,
                    yugioh_id=ygo_print_id,
                )
                session.add(print_row)
                stats.records_inserted += 1
            else:
                changed = False
                if ygo_print_id and print_row.yugioh_id != ygo_print_id:
                    print_row.yugioh_id = ygo_print_id
                    changed = True
                if print_row.rarity != normalized_rarity:
                    print_row.rarity = normalized_rarity
                    changed = True
                if print_row.language != normalized_language:
                    print_row.language = normalized_language
                    changed = True
                if print_row.variant != variant:
                    print_row.variant = variant
                    changed = True
                if changed:
                    stats.records_updated += 1

        return {"card_id": card_row.id}
