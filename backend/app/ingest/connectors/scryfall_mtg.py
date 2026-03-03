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

        if fixture:
            fixture_path = self._resolve_fixture_path(path)
            raw_items = self._load_fixture(fixture_path, set_code=set_code, limit=limit)
        else:
            raw_items = self._load_remote(limit=limit)

        payloads: list[tuple[Path, dict, str]] = []
        for idx, card in enumerate(raw_items):
            card_set_code = (card.get("set") or "").lower()
            if set_code and card_set_code != set_code.lower():
                continue
            payloads.append((Path(f"card_{card.get('id', idx)}.json"), card, self.checksum(card)))
        return payloads

    def _resolve_fixture_path(self, path: str | Path | None) -> Path:
        if path is None:
            return Path(__file__).resolve().parents[3] / "data" / "fixtures" / "scryfall_mtg_sample.json"
        candidate = Path(path)
        if candidate.exists():
            return candidate
        repo_root = Path(__file__).resolve().parents[3]
        for option in (repo_root / str(path), repo_root / "backend" / str(path)):
            if option.exists():
                return option
        return candidate

    def _load_fixture(self, fixture_path: Path, set_code: str | None, limit: int | None) -> list[dict]:
        payload = json.loads(fixture_path.read_text(encoding="utf-8"))
        cards = payload.get("data") if payload.get("object") == "list" else [payload]
        out: list[dict] = []
        for card in cards or []:
            if set_code and (card.get("set") or "").lower() != set_code.lower():
                continue
            out.append(card)
            if limit and len(out) >= limit:
                break
        return out

    def _load_remote(self, limit: int | None = None) -> list[dict]:
        bulk_list = self._request_json(f"{self.base_url}/bulk-data")
        default_bulk = next((item for item in bulk_list.get("data") or [] if item.get("type") == "default_cards"), None)
        if default_bulk is None or not default_bulk.get("download_uri"):
            raise RuntimeError("Scryfall default_cards bulk endpoint unavailable")

        cards: list[dict] = []
        with requests.get(default_bulk["download_uri"], stream=True, timeout=120) as response:
            response.raise_for_status()
            response.raw.decode_content = True
            payload = json.load(response.raw)

        for card in payload or []:
            cards.append(card)
            if limit and len(cards) >= limit:
                break
        return cards

    def _request_json(self, url: str, params: dict | None = None) -> dict:
        wait_seconds = 0.3
        for _ in range(6):
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
        return {
            "set": {
                "code": (payload.get("set") or "").lower(),
                "name": payload.get("set_name") or (payload.get("set") or "").upper(),
                "released_at": payload.get("released_at"),
            },
            "card": payload,
        }

    def upsert(self, session, payload: dict, stats: IngestStats, **kwargs) -> dict:
        game = session.execute(select(Game).where(Game.slug == "mtg")).scalar_one_or_none()
        if game is None:
            game = Game(slug="mtg", name="Magic: The Gathering")
            session.add(game)
            session.flush()
            stats.records_inserted += 1

        set_payload = payload.get("set") or {}
        set_code = (set_payload.get("code") or "").lower()
        if not set_code:
            return {}

        release_date = date.fromisoformat(set_payload["released_at"]) if set_payload.get("released_at") else None
        set_row = session.execute(select(Set).where(Set.game_id == game.id, Set.code == set_code)).scalar_one_or_none()
        if set_row is None:
            set_row = Set(game_id=game.id, code=set_code, name=set_payload.get("name") or set_code.upper(), release_date=release_date)
            session.add(set_row)
            session.flush()
            stats.records_inserted += 1
        else:
            changed = False
            new_name = set_payload.get("name")
            if new_name and set_row.name != new_name:
                set_row.name = new_name
                changed = True
            if release_date and set_row.release_date != release_date:
                set_row.release_date = release_date
                changed = True
            if changed:
                stats.records_updated += 1

        card_payload = payload.get("card") or {}
        card_name = (card_payload.get("name") or "").strip()
        if not card_name:
            return {}

        oracle_id = card_payload.get("oracle_id")
        if oracle_id:
            card_row = session.execute(select(Card).where(Card.game_id == game.id, Card.oracle_id == oracle_id)).scalar_one_or_none()
        else:
            card_row = session.execute(select(Card).where(Card.game_id == game.id, Card.name == card_name)).scalar_one_or_none()

        if card_row is None:
            card_row = Card(game_id=game.id, name=card_name, oracle_id=oracle_id)
            session.add(card_row)
            session.flush()
            stats.records_inserted += 1
        else:
            changed = False
            if card_row.name != card_name:
                card_row.name = card_name
                changed = True
            if oracle_id and card_row.oracle_id != oracle_id:
                card_row.oracle_id = oracle_id
                changed = True
            if changed:
                stats.records_updated += 1

        scryfall_id = card_payload.get("id")
        if scryfall_id:
            print_row = session.execute(select(Print).where(Print.scryfall_id == scryfall_id)).scalar_one_or_none()
        else:
            print_row = None

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
                scryfall_id=scryfall_id,
            )
            session.add(print_row)
            session.flush()
            stats.records_inserted += 1
        else:
            changed = False
            rarity = card_payload.get("rarity")
            language = card_payload.get("lang")
            if rarity and print_row.rarity != rarity:
                print_row.rarity = rarity
                changed = True
            if language and print_row.language != language:
                print_row.language = language
                changed = True
            if scryfall_id and print_row.scryfall_id != scryfall_id:
                print_row.scryfall_id = scryfall_id
                changed = True
            if changed:
                stats.records_updated += 1

        if scryfall_id:
            identifier = session.execute(
                select(PrintIdentifier).where(PrintIdentifier.print_id == print_row.id, PrintIdentifier.source == "scryfall")
            ).scalar_one_or_none()
            if identifier is None:
                session.add(PrintIdentifier(print_id=print_row.id, source="scryfall", external_id=scryfall_id))
                stats.records_inserted += 1
            elif identifier.external_id != scryfall_id:
                identifier.external_id = scryfall_id
                stats.records_updated += 1

        image_uris = card_payload.get("image_uris") or {}
        image_url = image_uris.get("normal") or image_uris.get("large")
        if image_url:
            existing_image = session.execute(
                select(PrintImage).where(PrintImage.print_id == print_row.id, PrintImage.url == image_url)
            ).scalar_one_or_none()
            if existing_image is None:
                session.add(PrintImage(print_id=print_row.id, url=image_url, is_primary=True, source="scryfall"))
                stats.records_inserted += 1

        upsert_field_provenance(
            session,
            "print",
            print_row.id,
            kwargs.get("source_name", self.name),
            {
                "rarity": print_row.rarity,
                "language": print_row.language,
                "collector_number": print_row.collector_number,
                "oracle_id": card_row.oracle_id,
                "scryfall_id": print_row.scryfall_id,
            },
        )

        return {}

    def default_cursor(self, **kwargs) -> dict:
        return {
            "incremental": kwargs.get("incremental", True),
            "last_synced_at": datetime.now(timezone.utc).isoformat(),
            "strategy": "upsert_checksum",
        }
