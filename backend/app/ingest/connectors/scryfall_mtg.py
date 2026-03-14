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


class ScryfallMtgConnector(SourceConnector):
    name = "scryfall_mtg"
    base_url = "https://api.scryfall.com"

    def should_bootstrap(self, session, source, **kwargs) -> bool:
        incremental = bool(kwargs.get("incremental", True))
        if not incremental:
            return False

        mtg_game_id = session.execute(select(Game.id).where(Game.slug == "mtg")).scalar_one_or_none()
        if mtg_game_id is None:
            return True

        has_mtg_cards = session.execute(select(func.count(Card.id)).where(Card.game_id == mtg_game_id)).scalar_one() > 0
        if not has_mtg_cards:
            return True

        has_source_records = session.execute(select(func.count()).select_from(SourceRecord).where(SourceRecord.source_id == source.id)).scalar_one() > 0
        return not has_source_records

    def load(self, path: str | Path | None = None, **kwargs) -> list[tuple[Path, dict, str]]:
        fixture = bool(kwargs.get("fixture", False))
        set_code = kwargs.get("set_code")
        limit = kwargs.get("limit")
        incremental = bool(kwargs.get("incremental", True))
        bootstrap = bool(kwargs.get("bootstrap", False))

        self.logger.info(
            "ingest scryfall load_start fixture=%s incremental=%s bootstrap=%s limit=%s set_code=%s",
            fixture,
            incremental,
            bootstrap,
            limit,
            set_code,
        )

        if fixture:
            fixture_path = self._resolve_fixture_path(path)
            raw_items = self._load_fixture(fixture_path, set_code=set_code, limit=limit)
        else:
            if incremental and not bootstrap:
                raw_items = self._load_incremental(limit=limit, last_run_at=kwargs.get("last_run_at"))
            else:
                raw_items = self._load_remote(limit=limit)

        payloads: list[tuple[Path, dict, str]] = []
        for idx, card in enumerate(raw_items):
            card_set_code = (card.get("set") or "").lower()
            if set_code and card_set_code != set_code.lower():
                continue
            payloads.append((Path(f"card_{card.get('id', idx)}.json"), card, self.checksum(card)))
        self.logger.info(
            "ingest scryfall load_done fixture=%s cards=%s limit=%s set_code=%s",
            fixture,
            len(payloads),
            limit,
            set_code,
        )
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
        seen_ids: set[str] = set()
        with requests.get(default_bulk["download_uri"], stream=True, timeout=120) as response:
            response.raise_for_status()
            response.raw.decode_content = True
            payload = json.load(response.raw)

        for card in payload or []:
            dedupe_id = str(card.get("id") or "").strip()
            if dedupe_id and dedupe_id in seen_ids:
                continue
            if dedupe_id:
                seen_ids.add(dedupe_id)
            cards.append(card)
            if limit and len(cards) >= limit:
                break
        return cards

    def _load_incremental(self, limit: int | None = None, last_run_at=None) -> list[dict]:
        cards: list[dict] = []
        seen_ids: set[str] = set()
        params: dict[str, str] = {"q": "game:paper", "order": "released", "dir": "desc"}
        if limit:
            params["unique"] = "prints"

        if last_run_at is not None:
            sync_date = last_run_at.date().isoformat()
            params["q"] = f"game:paper date>={sync_date}"

        url = f"{self.base_url}/cards/search"
        while url:
            payload = self._request_json(url, params=params)
            params = None
            for card in payload.get("data") or []:
                dedupe_id = str(card.get("id") or "").strip()
                if dedupe_id and dedupe_id in seen_ids:
                    continue
                if dedupe_id:
                    seen_ids.add(dedupe_id)
                cards.append(card)
                if limit and len(cards) >= limit:
                    return cards
            if not payload.get("has_more"):
                break
            url = payload.get("next_page")

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

    @staticmethod
    def _pick_primary_image_url(card_payload: dict) -> str | None:
        image_uris = card_payload.get("image_uris") or {}
        for key in ("png", "large", "normal", "small"):
            value = (image_uris.get(key) or "").strip()
            if value:
                return value

        card_faces = card_payload.get("card_faces") or []
        for face in card_faces:
            face_uris = face.get("image_uris") or {}
            for key in ("png", "large", "normal", "small"):
                value = (face_uris.get(key) or "").strip()
                if value:
                    return value
        return None

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
            return {"card_id": card_row.id, "set_id": set_row.id, "print_id": print_row.id}

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
            return {"card_id": card_row.id, "set_id": set_row.id, "print_id": print_row.id}

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
                    Print.language == self._normalize_language(card_payload.get("lang")),
                    Print.is_foil.is_(bool(card_payload.get("foil", False))),
                    Print.variant == "default",
                )
            ).scalar_one_or_none()

        if print_row is None:
            print_row = Print(
                card_id=card_row.id,
                set_id=set_row.id,
                collector_number=card_payload.get("collector_number") or "",
                language=self._normalize_language(card_payload.get("lang")),
                rarity=self._normalize_rarity(card_payload.get("rarity")),
                is_foil=bool(card_payload.get("foil", False)),
                scryfall_id=scryfall_id,
                variant="default",
            )
            session.add(print_row)
            session.flush()
            stats.records_inserted += 1
        else:
            changed = False
            rarity = self._normalize_rarity(card_payload.get("rarity"))
            language = self._normalize_language(card_payload.get("lang"))
            if print_row.rarity != rarity:
                print_row.rarity = rarity
                changed = True
            if print_row.language != language:
                print_row.language = language
                changed = True
            if print_row.variant != "default":
                print_row.variant = "default"
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

        image_url = self._pick_primary_image_url(card_payload)
        if image_url:
            primary_image = session.execute(
                select(PrintImage).where(PrintImage.print_id == print_row.id, PrintImage.is_primary.is_(True))
            ).scalar_one_or_none()
            if primary_image is None:
                session.add(PrintImage(print_id=print_row.id, url=image_url, is_primary=True, source="scryfall"))
                stats.records_inserted += 1
            elif primary_image.url != image_url:
                primary_image.url = image_url
                if primary_image.source != "scryfall":
                    primary_image.source = "scryfall"
                stats.records_updated += 1

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

        return {"card_id": card_row.id, "set_id": set_row.id, "print_id": print_row.id}

    def default_cursor(self, **kwargs) -> dict:
        return {
            "incremental": kwargs.get("incremental", True),
            "last_synced_at": datetime.now(timezone.utc).isoformat(),
            "strategy": "upsert_checksum",
        }

    @staticmethod
    def _normalize_language(value: object) -> str:
        language = str(value or "").strip().lower()
        return language or "en"

    @staticmethod
    def _normalize_rarity(value: object) -> str:
        rarity = str(value or "").strip()
        return rarity or "unknown"
