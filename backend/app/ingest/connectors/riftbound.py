from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import select

from app.ingest.base import IngestStats, SourceConnector
from app.models import Card, Game, Print, Set


class RiftboundConnector(SourceConnector):
    name = "riftbound"

    @staticmethod
    def _normalize_language(value: object) -> str:
        language = str(value or "").strip().lower()
        return language or "en"

    @staticmethod
    def _normalize_rarity(value: object) -> str:
        rarity = str(value or "").strip()
        return rarity or "common"

    def load(self, path: str | Path | None = None, **kwargs) -> list[tuple[Path, dict, str]]:
        if not bool(kwargs.get("fixture", True)):
            raise NotImplementedError("riftbound remote ingest is not implemented yet; use --fixture true")

        fixture_path = self._resolve_fixture_path(path)
        payload = json.loads(fixture_path.read_text(encoding="utf-8"))

        sets = {str(item.get("id") or item.get("code")): item for item in payload.get("sets") or []}
        cards = {str(item.get("id") or item.get("name")): item for item in payload.get("cards") or []}

        out: list[tuple[Path, dict, str]] = []
        for idx, print_item in enumerate(payload.get("prints") or []):
            record = {
                "set": sets.get(str(print_item.get("set_id"))) or sets.get(str(print_item.get("set_code"))) or {},
                "card": cards.get(str(print_item.get("card_id"))) or cards.get(str(print_item.get("card_name"))) or {},
                "print": print_item,
            }
            out.append((Path(f"riftbound_print_{idx+1}.json"), record, self.checksum(record)))
        return out

    def _resolve_fixture_path(self, path: str | Path | None) -> Path:
        fixture_name = "riftbound_sample.json"
        root = Path(__file__).resolve().parents[3]
        candidate = Path(path) if path else root / "data" / "fixtures" / fixture_name
        if candidate.is_file():
            return candidate
        for option in (root / str(candidate), root.parent / str(candidate), root / "data" / "fixtures" / fixture_name):
            if option.is_file():
                return option
            if option.is_dir() and (option / fixture_name).is_file():
                return option / fixture_name
        raise ValueError(f"Unable to resolve Riftbound fixture path: {path}")

    def normalize(self, payload: dict, **kwargs) -> dict:
        set_payload = payload.get("set") or {}
        card_payload = payload.get("card") or {}
        print_payload = payload.get("print") or {}
        return {
            "set": {
                "code": (set_payload.get("code") or print_payload.get("set_code") or "").strip().lower(),
                "name": (set_payload.get("name") or print_payload.get("set_name") or "").strip(),
                "riftbound_id": str(set_payload.get("id")) if set_payload.get("id") is not None else None,
            },
            "card": {
                "name": (card_payload.get("name") or print_payload.get("card_name") or "").strip(),
                "riftbound_id": str(card_payload.get("id")) if card_payload.get("id") is not None else None,
            },
            "print": {
                "collector_number": str(print_payload.get("collector_number") or "").strip(),
                "set_code": (print_payload.get("set_code") or "").strip().lower(),
                "riftbound_id": str(print_payload.get("id")) if print_payload.get("id") is not None else None,
                "language": self._normalize_language(print_payload.get("language") or card_payload.get("language")),
                "rarity": self._normalize_rarity(print_payload.get("rarity")),
                "raw_json": print_payload,
            },
        }

    def upsert(self, session, payload: dict, stats: IngestStats, **kwargs) -> dict:
        game = session.execute(select(Game).where(Game.slug == "riftbound")).scalar_one_or_none()
        if game is None:
            game = Game(slug="riftbound", name="Riftbound")
            session.add(game)
            session.flush()
            stats.records_inserted += 1

        set_payload = payload.get("set") or {}
        set_code = (set_payload.get("code") or "").strip().lower()
        if not set_code:
            return {}
        set_row = None
        rift_set_id = set_payload.get("riftbound_id")
        if rift_set_id:
            set_row = session.execute(select(Set).where(Set.game_id == game.id, Set.riftbound_id == rift_set_id)).scalar_one_or_none()
        if set_row is None:
            set_row = session.execute(select(Set).where(Set.game_id == game.id, Set.code == set_code)).scalar_one_or_none()
        if set_row is None:
            set_row = Set(game_id=game.id, code=set_code, name=set_payload.get("name") or set_code.upper(), riftbound_id=rift_set_id)
            session.add(set_row)
            session.flush()
            stats.records_inserted += 1

        card_payload = payload.get("card") or {}
        card_name = (card_payload.get("name") or "").strip()
        if not card_name:
            return {}
        rift_card_id = card_payload.get("riftbound_id")
        card_row = None
        if rift_card_id:
            card_row = session.execute(select(Card).where(Card.game_id == game.id, Card.riftbound_id == rift_card_id)).scalar_one_or_none()
        if card_row is None:
            card_row = session.execute(select(Card).where(Card.game_id == game.id, Card.name == card_name)).scalar_one_or_none()
        if card_row is None:
            card_row = Card(game_id=game.id, name=card_name, riftbound_id=rift_card_id)
            session.add(card_row)
            session.flush()
            stats.records_inserted += 1

        print_payload = payload.get("print") or {}
        collector_number = print_payload.get("collector_number") or "unknown"
        rift_print_id = print_payload.get("riftbound_id")
        language = self._normalize_language(print_payload.get("language"))
        rarity = self._normalize_rarity(print_payload.get("rarity"))
        print_row = None
        if rift_print_id:
            print_row = session.execute(select(Print).where(Print.riftbound_id == rift_print_id)).scalar_one_or_none()
        if print_row is None:
            print_row = session.execute(
                select(Print).where(
                    Print.set_id == set_row.id,
                    Print.card_id == card_row.id,
                    Print.collector_number == collector_number,
                    Print.variant == "default",
                )
            ).scalar_one_or_none()
        if print_row is None:
            print_row = Print(
                set_id=set_row.id,
                card_id=card_row.id,
                collector_number=collector_number,
                language=language,
                rarity=rarity,
                riftbound_id=rift_print_id,
                variant="default",
            )
            session.add(print_row)
            stats.records_inserted += 1
        else:
            changed = False
            if print_row.language != language:
                print_row.language = language
                changed = True
            if print_row.rarity != rarity:
                print_row.rarity = rarity
                changed = True
            if changed:
                stats.records_updated += 1
        return {"print_id": print_row.id}
