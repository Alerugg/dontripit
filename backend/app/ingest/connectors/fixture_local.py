from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from sqlalchemy import select

from app.models import Card, Game, Print, PrintIdentifier, PrintImage, Set
from app.ingest.base import SourceConnector


class FixtureLocalConnector(SourceConnector):
    name = "fixture_local"

    def load(self, path: Path | None, **kwargs) -> list[tuple[Path, dict]]:
        if path is None:
            raise ValueError("fixture_local requires --path")
        files = sorted(path.glob("*.json")) if path.is_dir() else [path]
        payloads = []
        for item in files:
            with item.open("r", encoding="utf-8") as f:
                payloads.append((item, json.load(f)))
        return payloads

    def normalize(self, payload: dict, **kwargs) -> dict:
        return {
            "game": payload.get("game"),
            "sets": payload.get("sets") or [],
            "cards": payload.get("cards") or [],
            "prints": payload.get("prints") or [],
        }

    def upsert(self, session, payload: dict) -> dict:
        stats = {"games": 0, "sets": 0, "cards": 0, "prints": 0, "images": 0, "identifiers": 0, "updates": 0}

        game_payload = payload.get("game") or {}
        game_slug = game_payload.get("slug")
        game_name = game_payload.get("name")
        if game_slug and game_name:
            game = session.execute(select(Game).where(Game.slug == game_slug)).scalar_one_or_none()
            if game is None:
                game = Game(slug=game_slug, name=game_name)
                session.add(game)
                session.flush()
                stats["games"] += 1
            elif game.name != game_name:
                game.name = game_name
                stats["updates"] += 1
        else:
            game = session.execute(select(Game).where(Game.slug == "pokemon")).scalar_one_or_none()

        if game is None:
            raise RuntimeError("Fixture needs game.slug/name or an existing pokemon game")

        sets_by_code = {}
        for item in payload.get("sets", []):
            code = item.get("code")
            name = item.get("name")
            if not code or not name:
                continue
            set_row = session.execute(select(Set).where(Set.game_id == game.id, Set.code == code)).scalar_one_or_none()
            release_date = date.fromisoformat(item["release_date"]) if item.get("release_date") else None
            if set_row is None:
                set_row = Set(game_id=game.id, code=code, name=name, release_date=release_date)
                session.add(set_row)
                session.flush()
                stats["sets"] += 1
            else:
                changed = False
                if set_row.name != name:
                    set_row.name = name
                    changed = True
                if set_row.release_date != release_date:
                    set_row.release_date = release_date
                    changed = True
                if changed:
                    stats["updates"] += 1
            sets_by_code[code] = set_row

        cards_by_name = {}
        for item in payload.get("cards", []):
            card_name = item.get("name")
            if not card_name:
                continue
            card_row = session.execute(select(Card).where(Card.game_id == game.id, Card.name == card_name)).scalar_one_or_none()
            if card_row is None:
                card_row = Card(game_id=game.id, name=card_name)
                session.add(card_row)
                session.flush()
                stats["cards"] += 1
            cards_by_name[card_name] = card_row

        for item in payload.get("prints", []):
            card_name = item.get("card_name")
            set_code = item.get("set_code")
            if not card_name or not set_code:
                continue

            card_row = cards_by_name.get(card_name)
            if card_row is None:
                card_row = session.execute(select(Card).where(Card.game_id == game.id, Card.name == card_name)).scalar_one_or_none()
                if card_row is None:
                    card_row = Card(game_id=game.id, name=card_name)
                    session.add(card_row)
                    session.flush()
                    stats["cards"] += 1
                cards_by_name[card_name] = card_row

            set_row = sets_by_code.get(set_code)
            if set_row is None:
                set_row = session.execute(select(Set).where(Set.game_id == game.id, Set.code == set_code)).scalar_one_or_none()
                if set_row is None:
                    continue
                sets_by_code[set_code] = set_row

            print_row = session.execute(
                select(Print).where(
                    Print.set_id == set_row.id,
                    Print.collector_number == item.get("collector_number"),
                    Print.language == item.get("language"),
                    Print.is_foil.is_(bool(item.get("is_foil", False))),
                )
            ).scalar_one_or_none()

            if print_row is None:
                print_row = Print(
                    card_id=card_row.id,
                    set_id=set_row.id,
                    collector_number=item.get("collector_number", ""),
                    language=item.get("language", "EN"),
                    rarity=item.get("rarity", "unknown"),
                    is_foil=bool(item.get("is_foil", False)),
                )
                session.add(print_row)
                session.flush()
                stats["prints"] += 1
            else:
                changed = False
                if print_row.card_id != card_row.id:
                    print_row.card_id = card_row.id
                    changed = True
                if print_row.rarity != item.get("rarity", print_row.rarity):
                    print_row.rarity = item.get("rarity", print_row.rarity)
                    changed = True
                if changed:
                    stats["updates"] += 1

            for image in item.get("images", []):
                image_url = image.get("url")
                if not image_url:
                    continue
                is_primary = bool(image.get("is_primary", False))
                source = image.get("source", self.name)

                existing = session.execute(
                    select(PrintImage).where(PrintImage.print_id == print_row.id, PrintImage.url == image_url)
                ).scalar_one_or_none()

                if is_primary:
                    session.query(PrintImage).filter(PrintImage.print_id == print_row.id, PrintImage.is_primary.is_(True)).update(
                        {"is_primary": False}
                    )

                if existing is None:
                    session.add(PrintImage(print_id=print_row.id, url=image_url, is_primary=is_primary, source=source))
                    stats["images"] += 1
                else:
                    if existing.is_primary != is_primary or existing.source != source:
                        existing.is_primary = is_primary
                        existing.source = source
                        stats["updates"] += 1

            for identifier in item.get("identifiers", []):
                source = identifier.get("source")
                external_id = identifier.get("external_id")
                if not source or not external_id:
                    continue

                existing_identifier = session.execute(
                    select(PrintIdentifier).where(
                        PrintIdentifier.source == source,
                        PrintIdentifier.external_id == external_id,
                    )
                ).scalar_one_or_none()
                if existing_identifier is None:
                    session.add(PrintIdentifier(print_id=print_row.id, source=source, external_id=external_id))
                    stats["identifiers"] += 1
                elif existing_identifier.print_id != print_row.id:
                    existing_identifier.print_id = print_row.id
                    stats["updates"] += 1

        return stats
