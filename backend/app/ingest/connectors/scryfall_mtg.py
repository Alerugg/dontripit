from __future__ import annotations

import json
import time
from datetime import date, datetime, timezone
from pathlib import Path

import requests
from sqlalchemy import select

from app.ingest.base import IngestStats, SourceConnector
from app.ingest.provenance import upsert_field_provenance
from app.models import Card, Game, Print, PrintIdentifier, PrintImage, Set


class ScryfallMtgConnector(SourceConnector):
    name = "scryfall_mtg"
    base_url = "https://api.scryfall.com"

    def load(self, path: str | Path | None = None, **kwargs) -> list[tuple[Path, dict, str]]:
        fixture = bool(kwargs.get("fixture", False))
        set_code = kwargs.get("set_code")
        limit = kwargs.get("limit")
        last_run_at = kwargs.get("last_run_at") if kwargs.get("incremental", True) else None

        if fixture:
            fixture_dir = Path(path) if path else Path(__file__).resolve().parents[3] / "data" / "fixtures" / "scryfall"
            raw_items = self._load_fixture(fixture_dir, set_code, limit)
        else:
            raw_items = self._load_remote(set_code, limit)

        out = []
        for file_path, payload in raw_items:
            updated_at = self._payload_updated_at(payload)
            if last_run_at and updated_at and updated_at <= last_run_at:
                continue
            out.append((file_path, payload, self.checksum(payload)))
        return out

    def _load_fixture(self, fixture_dir: Path, set_code: str | None, limit: int | None):
        payloads = []
        for file_path in sorted(fixture_dir.glob("*.json")):
            payload = json.loads(file_path.read_text(encoding="utf-8"))
            if payload.get("object") == "list" and payload.get("data"):
                first = payload["data"][0]
                if first.get("object") == "set":
                    for item in payload.get("data"):
                        if set_code and item.get("code") != set_code.lower():
                            continue
                        payloads.append((Path(f"set_{item.get('code')}.json"), {"object": "set", "set": item}))
                else:
                    for idx, card in enumerate(payload.get("data") or []):
                        if limit and idx >= limit:
                            break
                        payloads.append((Path(f"card_{card.get('id', idx)}.json"), card))
            elif payload.get("object") == "card":
                payloads.append((file_path, payload))
        return payloads

    def _load_remote(self, set_code: str | None, limit: int | None):
        items = []
        sets = self._request_json(f"{self.base_url}/sets").get("data") or []
        if set_code:
            sets = [item for item in sets if item.get("code") == set_code.lower()]
        for set_item in sets:
            code = set_item.get("code")
            if not code:
                continue
            url = f"{self.base_url}/cards/search"
            params = {"q": f"set:{code}", "unique": "prints", "order": "set", "dir": "asc"}
            count = 0
            while url:
                page = self._request_json(url, params=params)
                params = None
                for card in page.get("data") or []:
                    card["_set"] = set_item
                    items.append((Path(f"{code}_{card.get('id')}.json"), card))
                    count += 1
                    if limit and count >= limit:
                        break
                if limit and count >= limit:
                    break
                url = page.get("next_page") if page.get("has_more") else None
        return items

    def _request_json(self, url: str, params: dict | None = None) -> dict:
        wait_seconds = 0.2
        for _ in range(5):
            response = requests.get(url, params=params, timeout=30)
            if response.status_code in (429, 500, 502, 503, 504):
                time.sleep(wait_seconds)
                wait_seconds *= 2
                continue
            response.raise_for_status()
            time.sleep(0.12)
            return response.json()
        raise RuntimeError(f"Scryfall request failed after retries: {url}")

    def normalize(self, payload: dict, **kwargs) -> dict:
        if payload.get("object") == "set":
            return {"set": payload.get("set"), "card": None}
        set_payload = payload.get("_set") or {"code": payload.get("set"), "name": payload.get("set_name"), "released_at": payload.get("released_at")}
        return {"set": set_payload, "card": payload}

    def upsert(self, session, payload: dict, stats: IngestStats, **kwargs) -> dict:
        game = session.execute(select(Game).where(Game.slug == "mtg")).scalar_one_or_none()
        if game is None:
            game = Game(slug="mtg", name="Magic: The Gathering")
            session.add(game)
            session.flush()
            stats.records_inserted += 1

        set_payload = payload.get("set") or {}
        set_code = set_payload.get("code")
        if not set_code:
            return {}
        release_date = date.fromisoformat(set_payload["released_at"]) if set_payload.get("released_at") else None
        set_row = session.execute(select(Set).where(Set.game_id == game.id, Set.code == set_code)).scalar_one_or_none()
        if set_row is None:
            set_row = Set(game_id=game.id, code=set_code, name=set_payload.get("name") or set_code.upper(), release_date=release_date)
            session.add(set_row)
            session.flush()
            stats.records_inserted += 1

        card_payload = payload.get("card")
        if card_payload is None:
            return {}

        card_name = (card_payload.get("name") or "").strip()
        if not card_name:
            return {}

        card_row = session.execute(select(Card).where(Card.game_id == game.id, Card.name == card_name)).scalar_one_or_none()
        if card_row is None:
            card_row = Card(game_id=game.id, name=card_name)
            session.add(card_row)
            session.flush()
            stats.records_inserted += 1

        external_id = card_payload.get("id")
        identifier = session.execute(
            select(PrintIdentifier).where(PrintIdentifier.source == "scryfall", PrintIdentifier.external_id == external_id)
        ).scalar_one_or_none()
        print_row = session.execute(select(Print).where(Print.id == identifier.print_id)).scalar_one_or_none() if identifier else None
        if print_row is None:
            print_row = session.execute(
                select(Print).where(
                    Print.set_id == set_row.id,
                    Print.card_id == card_row.id,
                    Print.collector_number == (card_payload.get("collector_number") or ""),
                )
            ).scalar_one_or_none()

        if print_row is None:
            print_row = Print(
                card_id=card_row.id,
                set_id=set_row.id,
                collector_number=card_payload.get("collector_number") or "",
                language=card_payload.get("lang") or "en",
                rarity=card_payload.get("rarity") or "unknown",
                is_foil=bool(card_payload.get("foil", False)),
            )
            session.add(print_row)
            session.flush()
            stats.records_inserted += 1
        else:
            print_row.rarity = card_payload.get("rarity") or print_row.rarity
            print_row.language = card_payload.get("lang") or print_row.language
            stats.records_updated += 1

        if external_id and identifier is None:
            session.add(PrintIdentifier(print_id=print_row.id, source="scryfall", external_id=external_id))
            stats.records_inserted += 1

        oracle_id = card_payload.get("oracle_id")
        upsert_field_provenance(
            session,
            "print",
            print_row.id,
            kwargs.get("source_name", self.name),
            {
                "rarity": print_row.rarity,
                "language": print_row.language,
                "collector_number": print_row.collector_number,
                "oracle_id": oracle_id,
            },
        )

        image_uris = card_payload.get("image_uris") or {}
        image_url = image_uris.get("normal") or image_uris.get("large")
        if image_url:
            existing_image = session.execute(
                select(PrintImage).where(PrintImage.print_id == print_row.id, PrintImage.url == image_url)
            ).scalar_one_or_none()
            if existing_image is None:
                session.add(PrintImage(print_id=print_row.id, url=image_url, is_primary=True, source="scryfall"))
                stats.records_inserted += 1

        return {}

    def default_cursor(self, **kwargs) -> dict:
        return {"incremental": kwargs.get("incremental", True), "updated_at": datetime.now(timezone.utc).isoformat()}

    def _payload_updated_at(self, payload: dict):
        value = payload.get("updated_at")
        if not value:
            return None
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
