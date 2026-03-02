from __future__ import annotations

import hashlib
import json
import time
from datetime import date
from pathlib import Path

import requests
from sqlalchemy import select

from app.ingest.base import SourceConnector
from app.models import Card, Game, Print, PrintIdentifier, PrintImage, Set


class ScryfallMtgConnector(SourceConnector):
    name = "scryfall"
    base_url = "https://api.scryfall.com"

    def load(self, path: Path | None = None, **kwargs) -> list[tuple[Path, dict]]:
        set_code = kwargs.get("set_code")
        limit = kwargs.get("limit")
        fixture = bool(kwargs.get("fixture", False))

        if fixture:
            fixture_dir = path or (Path(__file__).resolve().parents[3] / "data" / "fixtures" / "scryfall")
            return self._load_fixture(fixture_dir, set_code=set_code, limit=limit)

        return self._load_remote(set_code=set_code, limit=limit)

    def _load_fixture(self, fixture_dir: Path, set_code: str | None, limit: int | None) -> list[tuple[Path, dict]]:
        files = sorted(fixture_dir.glob("*.json"))
        payloads: list[tuple[Path, dict]] = []
        for file_path in files:
            with file_path.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
            if payload.get("object") == "list" and payload.get("data"):
                if payload["data"] and payload["data"][0].get("object") == "set":
                    payloads.extend(self._expand_sets_page(payload, set_code=set_code))
                else:
                    payloads.extend(self._expand_cards_page(payload, limit=limit))
            elif payload.get("object") == "card":
                payloads.append((file_path, payload))
        return payloads

    def _load_remote(self, set_code: str | None, limit: int | None) -> list[tuple[Path, dict]]:
        items: list[tuple[Path, dict]] = []
        for set_item in self._fetch_sets(set_code=set_code):
            code = set_item.get("code")
            if not code:
                continue
            count = 0
            for card in self._fetch_cards_for_set(code):
                card["_set"] = set_item
                items.append((Path(f"scryfall_{code}_{card.get('id', 'unknown')}.json"), card))
                count += 1
                if limit is not None and count >= limit:
                    break
        return items

    def _request_json(self, url: str, params: dict | None = None) -> dict:
        max_retries = 5
        wait_time = 0.5
        for attempt in range(max_retries):
            try:
                response = requests.get(url, params=params, timeout=20)
                if response.status_code == 429:
                    retry_after = float(response.headers.get("Retry-After", "1"))
                    time.sleep(retry_after)
                    continue
                response.raise_for_status()
                time.sleep(0.15)
                return response.json()
            except requests.RequestException:
                if attempt == max_retries - 1:
                    raise
                time.sleep(wait_time)
                wait_time *= 2
        raise RuntimeError("unreachable retry block")

    def _fetch_sets(self, set_code: str | None) -> list[dict]:
        payload = self._request_json(f"{self.base_url}/sets")
        data = payload.get("data") or []
        if set_code:
            data = [item for item in data if item.get("code") == set_code.lower()]
        return data

    def _fetch_cards_for_set(self, set_code: str):
        url = f"{self.base_url}/cards/search"
        params = {"q": f"set:{set_code}", "order": "set", "unique": "prints", "dir": "asc"}
        while url:
            payload = self._request_json(url, params=params)
            for card in payload.get("data") or []:
                yield card
            if payload.get("has_more"):
                url = payload.get("next_page")
                params = None
            else:
                url = None

    def _expand_sets_page(self, payload: dict, set_code: str | None) -> list[tuple[Path, dict]]:
        out = []
        for item in payload.get("data") or []:
            if set_code and item.get("code") != set_code.lower():
                continue
            out.append((Path(f"fixture_set_{item.get('code', 'unknown')}.json"), {"object": "set", "set": item}))
        return out

    def _expand_cards_page(self, payload: dict, limit: int | None) -> list[tuple[Path, dict]]:
        out = []
        count = 0
        for card in payload.get("data") or []:
            out.append((Path(f"fixture_card_{card.get('id', 'unknown')}.json"), card))
            count += 1
            if limit is not None and count >= limit:
                break
        return out

    def normalize(self, payload: dict, **kwargs) -> dict:
        if payload.get("object") == "set":
            return {"set": payload.get("set"), "card": None}
        return {"set": payload.get("_set"), "card": payload}

    def upsert(self, session, payload: dict) -> dict:
        stats = {"games": 0, "sets": 0, "cards": 0, "prints": 0, "images": 0, "identifiers": 0, "updates": 0}

        game = session.execute(select(Game).where(Game.slug == "mtg")).scalar_one_or_none()
        if game is None:
            game = Game(slug="mtg", name="Magic: The Gathering")
            session.add(game)
            session.flush()
            stats["games"] += 1

        set_payload = payload.get("set") or {}
        set_code = (set_payload.get("code") or "").lower()
        if not set_code:
            return stats

        release_date = date.fromisoformat(set_payload["released_at"]) if set_payload.get("released_at") else None
        set_row = session.execute(select(Set).where(Set.game_id == game.id, Set.code == set_code)).scalar_one_or_none()
        if set_row is None:
            set_row = Set(game_id=game.id, code=set_code, name=set_payload.get("name") or set_code.upper(), release_date=release_date)
            session.add(set_row)
            session.flush()
            stats["sets"] += 1

        card_payload = payload.get("card")
        if card_payload is None:
            return stats

        card_name = (card_payload.get("name") or "").strip()
        if not card_name:
            return stats

        card_row = session.execute(select(Card).where(Card.game_id == game.id, Card.name == card_name)).scalar_one_or_none()
        if card_row is None:
            card_row = Card(game_id=game.id, name=card_name)
            session.add(card_row)
            session.flush()
            stats["cards"] += 1

        external_id = card_payload.get("id")
        print_variants = self._print_variants(card_payload)
        for is_foil in print_variants:
            print_row = None
            if external_id:
                identifier_lookup = session.execute(
                    select(PrintIdentifier).where(PrintIdentifier.source == self.name, PrintIdentifier.external_id == self._external_id(external_id, is_foil))
                ).scalar_one_or_none()
                if identifier_lookup is not None:
                    print_row = session.execute(select(Print).where(Print.id == identifier_lookup.print_id)).scalar_one_or_none()

            if print_row is None:
                print_row = session.execute(
                    select(Print).where(
                        Print.set_id == set_row.id,
                        Print.collector_number == (card_payload.get("collector_number") or ""),
                        Print.language == (card_payload.get("lang") or "en"),
                        Print.is_foil.is_(is_foil),
                    )
                ).scalar_one_or_none()

            if print_row is None:
                print_row = Print(
                    card_id=card_row.id,
                    set_id=set_row.id,
                    collector_number=card_payload.get("collector_number") or "",
                    language=card_payload.get("lang") or "en",
                    rarity=card_payload.get("rarity") or "unknown",
                    is_foil=is_foil,
                )
                session.add(print_row)
                session.flush()
                stats["prints"] += 1
            else:
                changed = False
                if print_row.card_id != card_row.id:
                    print_row.card_id = card_row.id
                    changed = True
                rarity = card_payload.get("rarity") or "unknown"
                if print_row.rarity != rarity:
                    print_row.rarity = rarity
                    changed = True
                if changed:
                    stats["updates"] += 1

            if external_id:
                external_variant = self._external_id(external_id, is_foil)
                existing_id = session.execute(
                    select(PrintIdentifier).where(PrintIdentifier.source == self.name, PrintIdentifier.external_id == external_variant)
                ).scalar_one_or_none()
                if existing_id is None:
                    session.add(PrintIdentifier(print_id=print_row.id, source=self.name, external_id=external_variant))
                    stats["identifiers"] += 1

            self._upsert_images(session, print_row.id, card_payload, stats)

        return stats

    def _external_id(self, external_id: str, is_foil: bool) -> str:
        return f"{external_id}:foil" if is_foil else f"{external_id}:nonfoil"

    def _print_variants(self, card_payload: dict) -> list[bool]:
        has_foil = bool(card_payload.get("foil"))
        has_nonfoil = bool(card_payload.get("nonfoil"))
        if has_foil and has_nonfoil:
            return [False, True]
        if has_foil:
            return [True]
        return [False]

    def _upsert_images(self, session, print_id: int, card_payload: dict, stats: dict) -> None:
        image_urls: list[str] = []
        image_uris = card_payload.get("image_uris") or {}
        if image_uris.get("normal"):
            image_urls.append(image_uris["normal"])
        elif image_uris.get("large"):
            image_urls.append(image_uris["large"])

        if not image_urls:
            for face in card_payload.get("card_faces") or []:
                face_uris = face.get("image_uris") or {}
                if face_uris.get("normal"):
                    image_urls.append(face_uris["normal"])
                elif face_uris.get("large"):
                    image_urls.append(face_uris["large"])

        for index, url in enumerate(image_urls):
            existing = session.execute(select(PrintImage).where(PrintImage.print_id == print_id, PrintImage.url == url)).scalar_one_or_none()
            if existing is None:
                session.add(PrintImage(print_id=print_id, url=url, is_primary=index == 0, source="scryfall"))
                stats["images"] += 1

    @staticmethod
    def checksum(payload: dict) -> str:
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()
