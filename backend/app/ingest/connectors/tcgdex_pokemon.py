from __future__ import annotations

import json
import time
from datetime import date, datetime, timezone
from pathlib import Path

import requests
from sqlalchemy import func, select

from app.ingest.base import IngestStats, SourceConnector
from app.ingest.provenance import upsert_field_provenance
from app.models import Card, Game, Print, PrintIdentifier, PrintImage, Set, SourceRecord


class TcgdexPokemonConnector(SourceConnector):
    name = "tcgdex_pokemon"
    base_url = "https://api.tcgdex.net/v2/en"

    def should_bootstrap(self, session, source, **kwargs) -> bool:
        incremental = bool(kwargs.get("incremental", True))
        if not incremental:
            return False

        pokemon_game_id = session.execute(select(Game.id).where(Game.slug == "pokemon")).scalar_one_or_none()
        if pokemon_game_id is None:
            return True

        has_pokemon_cards = session.execute(select(func.count(Card.id)).where(Card.game_id == pokemon_game_id)).scalar_one() > 0
        if not has_pokemon_cards:
            return True

        has_source_records = (
            session.execute(select(func.count()).select_from(SourceRecord).where(SourceRecord.source_id == source.id)).scalar_one() > 0
        )
        return not has_source_records

    def load(self, path: str | Path | None = None, **kwargs) -> list[tuple[Path, dict, str]]:
        fixture = bool(kwargs.get("fixture", False))
        limit = kwargs.get("limit")

        if fixture:
            fixture_path = self._resolve_fixture_path(path)
            cards = self._load_fixture(fixture_path, limit=limit)
        else:
            cards = self._load_remote(limit=limit)

        payloads: list[tuple[Path, dict, str]] = []
        for idx, card in enumerate(cards):
            payloads.append((Path(f"tcgdex_card_{card.get('id', idx)}.json"), card, self.checksum(card)))
        return payloads

    def _resolve_fixture_path(self, path: str | Path | None) -> Path:
        if path is None:
            return Path(__file__).resolve().parents[3] / "data" / "fixtures" / "tcgdex_pokemon_sample.json"
        candidate = Path(path)
        if candidate.exists():
            return candidate
        repo_root = Path(__file__).resolve().parents[3]
        for option in (repo_root / str(path), repo_root / "backend" / str(path)):
            if option.exists():
                return option
        return candidate

    def _load_fixture(self, fixture_path: Path, limit: int | None) -> list[dict]:
        payload = json.loads(fixture_path.read_text(encoding="utf-8"))
        cards = payload.get("cards") or []
        out: list[dict] = []
        for card in cards:
            out.append(card)
            if limit and len(out) >= limit:
                break
        return out

    def _load_remote(self, limit: int | None = None) -> list[dict]:
        sets = self._request_json(f"{self.base_url}/sets")
        out: list[dict] = []

        for item in sets:
            set_id = item.get("id")
            if not set_id:
                continue
            set_payload = self._request_json(f"{self.base_url}/sets/{set_id}")
            cards = set_payload.get("cards") or []
            for card in cards:
                combined = {
                    "set": {
                        "id": set_payload.get("id"),
                        "abbreviation": set_payload.get("abbreviation"),
                        "name": set_payload.get("name"),
                        "releaseDate": set_payload.get("releaseDate"),
                    },
                    "id": card.get("id"),
                    "localId": card.get("localId"),
                    "name": card.get("name"),
                    "image": card.get("image"),
                }
                out.append(combined)
                if limit and len(out) >= limit:
                    return out
        return out

    def _request_json(self, url: str, params: dict | None = None):
        wait_seconds = 0.3
        for _ in range(6):
            response = requests.get(url, params=params, timeout=30)
            if response.status_code in (429, 500, 502, 503, 504):
                time.sleep(wait_seconds)
                wait_seconds *= 2
                continue
            response.raise_for_status()
            time.sleep(0.1)
            return response.json()
        raise RuntimeError(f"TCGdex request failed after retries: {url}")

    def normalize(self, payload: dict, **kwargs) -> dict:
        set_payload = payload.get("set") or {}
        return {
            "set": {
                "code": (set_payload.get("abbreviation") or set_payload.get("id") or "").lower(),
                "name": set_payload.get("name") or (set_payload.get("id") or "").upper(),
                "released_at": set_payload.get("releaseDate"),
                "tcgdex_id": set_payload.get("id"),
            },
            "card": {
                "id": payload.get("id"),
                "name": payload.get("name"),
                "collector_number": payload.get("localId"),
                "image": payload.get("image"),
            },
        }

    def upsert(self, session, payload: dict, stats: IngestStats, **kwargs) -> dict:
        game = session.execute(select(Game).where(Game.slug == "pokemon")).scalar_one_or_none()
        if game is None:
            game = Game(slug="pokemon", name="Pokémon")
            session.add(game)
            session.flush()
            stats.records_inserted += 1

        set_payload = payload.get("set") or {}
        set_code = (set_payload.get("code") or "").lower()
        set_tcgdex_id = set_payload.get("tcgdex_id")
        if not set_code:
            return {}

        release_date = date.fromisoformat(set_payload["released_at"]) if set_payload.get("released_at") else None
        set_row = None
        if set_tcgdex_id:
            set_row = session.execute(select(Set).where(Set.game_id == game.id, Set.tcgdex_id == set_tcgdex_id)).scalar_one_or_none()
        if set_row is None:
            set_row = session.execute(select(Set).where(Set.game_id == game.id, Set.code == set_code)).scalar_one_or_none()

        if set_row is None:
            set_row = Set(
                game_id=game.id,
                code=set_code,
                tcgdex_id=set_tcgdex_id,
                name=set_payload.get("name") or set_code.upper(),
                release_date=release_date,
            )
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
            if set_tcgdex_id and set_row.tcgdex_id != set_tcgdex_id:
                set_row.tcgdex_id = set_tcgdex_id
                changed = True
            if changed:
                stats.records_updated += 1

        card_payload = payload.get("card") or {}
        card_name = (card_payload.get("name") or "").strip()
        tcgdex_card_id = card_payload.get("id")
        if not card_name:
            return {}

        card_row = None
        if tcgdex_card_id:
            card_row = session.execute(select(Card).where(Card.game_id == game.id, Card.tcgdex_id == tcgdex_card_id)).scalar_one_or_none()
        if card_row is None:
            card_row = session.execute(select(Card).where(Card.game_id == game.id, Card.name == card_name)).scalar_one_or_none()

        if card_row is None:
            card_row = Card(game_id=game.id, name=card_name, tcgdex_id=tcgdex_card_id)
            session.add(card_row)
            session.flush()
            stats.records_inserted += 1
        else:
            changed = False
            if card_row.name != card_name:
                card_row.name = card_name
                changed = True
            if tcgdex_card_id and card_row.tcgdex_id != tcgdex_card_id:
                card_row.tcgdex_id = tcgdex_card_id
                changed = True
            if changed:
                stats.records_updated += 1

        tcgdex_print_id = tcgdex_card_id
        if tcgdex_print_id:
            print_row = session.execute(select(Print).where(Print.tcgdex_id == tcgdex_print_id)).scalar_one_or_none()
        else:
            print_row = None

        collector_number = card_payload.get("collector_number") or ""
        if print_row is None:
            print_row = session.execute(
                select(Print).where(
                    Print.set_id == set_row.id,
                    Print.card_id == card_row.id,
                    Print.collector_number == collector_number,
                )
            ).scalar_one_or_none()

        if print_row is None:
            print_row = Print(
                card_id=card_row.id,
                set_id=set_row.id,
                collector_number=collector_number,
                language="en",
                rarity="unknown",
                is_foil=False,
                tcgdex_id=tcgdex_print_id,
            )
            session.add(print_row)
            session.flush()
            stats.records_inserted += 1
        else:
            changed = False
            if tcgdex_print_id and print_row.tcgdex_id != tcgdex_print_id:
                print_row.tcgdex_id = tcgdex_print_id
                changed = True
            if changed:
                stats.records_updated += 1

        if tcgdex_print_id:
            identifier = session.execute(
                select(PrintIdentifier).where(PrintIdentifier.print_id == print_row.id, PrintIdentifier.source == "tcgdex")
            ).scalar_one_or_none()
            if identifier is None:
                session.add(PrintIdentifier(print_id=print_row.id, source="tcgdex", external_id=tcgdex_print_id))
                stats.records_inserted += 1
            elif identifier.external_id != tcgdex_print_id:
                identifier.external_id = tcgdex_print_id
                stats.records_updated += 1

        image_base = card_payload.get("image")
        image_url = f"{image_base}/high.webp" if image_base else None
        if image_url:
            existing_image = session.execute(
                select(PrintImage).where(PrintImage.print_id == print_row.id, PrintImage.url == image_url)
            ).scalar_one_or_none()
            if existing_image is None:
                session.add(PrintImage(print_id=print_row.id, url=image_url, is_primary=True, source="tcgdex"))
                stats.records_inserted += 1

        upsert_field_provenance(
            session,
            "print",
            print_row.id,
            kwargs.get("source_name", self.name),
            {
                "collector_number": print_row.collector_number,
                "tcgdex_id": print_row.tcgdex_id,
                "card_tcgdex_id": card_row.tcgdex_id,
            },
        )
        return {}

    def default_cursor(self, **kwargs) -> dict:
        return {
            "incremental": kwargs.get("incremental", True),
            "last_synced_at": datetime.now(timezone.utc).isoformat(),
            "strategy": "upsert_checksum",
        }
