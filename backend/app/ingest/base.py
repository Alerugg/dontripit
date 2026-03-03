from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from sqlalchemy import select

from app.models import Card, Game, Print, PrintIdentifier, PrintImage, Set, Source, SourceRecord


@dataclass
class IngestStats:
    files_seen: int = 0
    files_skipped: int = 0
    records_inserted: int = 0
    records_updated: int = 0


class SourceConnector:
    name = "base"

    def load(self, path: str) -> list[tuple[Path, dict, str]]:
        root = Path(path)
        if not root.exists():
            repo_root = Path(__file__).resolve().parents[3]
            alt_candidates = [
                repo_root / path,
                repo_root / "backend" / path,
            ]
            for alt_root in alt_candidates:
                if alt_root.exists():
                    root = alt_root
                    break
        payloads: list[tuple[Path, dict, str]] = []
        for file_path in sorted(root.glob("*.json")):
            raw = file_path.read_bytes()
            checksum = hashlib.sha256(raw).hexdigest()
            payloads.append((file_path, json.loads(raw.decode("utf-8")), checksum))
        return payloads

    def normalize(self, payload: dict) -> dict:
        return {
            "game": payload.get("game") or {},
            "sets": payload.get("sets") or [],
            "cards": payload.get("cards") or [],
            "prints": payload.get("prints") or [],
        }

    def ensure_source(self, session):
        source = session.execute(select(Source).where(Source.name == self.name)).scalar_one_or_none()
        if source:
            return source
        source = Source(name=self.name, description=f"Connector source for {self.name}")
        session.add(source)
        session.flush()
        return source

    def upsert(self, session, normalized: dict, stats: IngestStats) -> None:
        game_payload = normalized.get("game") or {}
        game = None
        if game_payload.get("slug"):
            game = session.execute(select(Game).where(Game.slug == game_payload["slug"])).scalar_one_or_none()
            if game:
                game.name = game_payload.get("name", game.name)
                stats.records_updated += 1
            else:
                game = Game(slug=game_payload["slug"], name=game_payload.get("name", game_payload["slug"]))
                session.add(game)
                session.flush()
                stats.records_inserted += 1

        if game is None:
            return

        set_map = {}
        for item in normalized.get("sets", []):
            code = item.get("code")
            if not code:
                continue
            set_obj = session.execute(select(Set).where(Set.game_id == game.id, Set.code == code)).scalar_one_or_none()
            release_date = item.get("release_date")
            if isinstance(release_date, str):
                release_date = date.fromisoformat(release_date)
            if set_obj:
                set_obj.name = item.get("name", set_obj.name)
                set_obj.release_date = release_date or set_obj.release_date
                stats.records_updated += 1
            else:
                set_obj = Set(game_id=game.id, code=code, name=item.get("name", code), release_date=release_date)
                session.add(set_obj)
                session.flush()
                stats.records_inserted += 1
            set_map[code] = set_obj

        card_map = {}
        for item in normalized.get("cards", []):
            name = item.get("name")
            if not name:
                continue
            card = session.execute(select(Card).where(Card.game_id == game.id, Card.name == name)).scalar_one_or_none()
            if card:
                stats.records_updated += 1
            else:
                card = Card(game_id=game.id, name=name)
                session.add(card)
                session.flush()
                stats.records_inserted += 1
            card_map[name] = card

        for item in normalized.get("prints", []):
            set_code = item.get("set_code")
            card_name = item.get("card_name")
            collector_number = item.get("collector_number")
            if not (set_code and card_name and collector_number):
                continue
            set_obj = set_map.get(set_code) or session.execute(
                select(Set).where(Set.game_id == game.id, Set.code == set_code)
            ).scalar_one_or_none()
            card = card_map.get(card_name) or session.execute(
                select(Card).where(Card.game_id == game.id, Card.name == card_name)
            ).scalar_one_or_none()
            if not (set_obj and card):
                continue

            print_obj = session.execute(
                select(Print).where(
                    Print.set_id == set_obj.id,
                    Print.card_id == card.id,
                    Print.collector_number == collector_number,
                )
            ).scalar_one_or_none()
            if print_obj:
                print_obj.language = item.get("language", print_obj.language)
                print_obj.rarity = item.get("rarity", print_obj.rarity)
                print_obj.is_foil = bool(item.get("is_foil", print_obj.is_foil))
                stats.records_updated += 1
            else:
                print_obj = Print(
                    set_id=set_obj.id,
                    card_id=card.id,
                    collector_number=collector_number,
                    language=item.get("language"),
                    rarity=item.get("rarity"),
                    is_foil=bool(item.get("is_foil", False)),
                )
                session.add(print_obj)
                session.flush()
                stats.records_inserted += 1

            images = item.get("images") or []
            if any(img.get("is_primary") for img in images):
                session.query(PrintImage).filter(PrintImage.print_id == print_obj.id).update({"is_primary": False})
            for image in images:
                url = image.get("url")
                if not url:
                    continue
                existing = session.execute(
                    select(PrintImage).where(PrintImage.print_id == print_obj.id, PrintImage.url == url)
                ).scalar_one_or_none()
                if existing:
                    existing.is_primary = bool(image.get("is_primary", existing.is_primary))
                    existing.source = image.get("source", existing.source)
                    stats.records_updated += 1
                else:
                    session.add(
                        PrintImage(
                            print_id=print_obj.id,
                            url=url,
                            is_primary=bool(image.get("is_primary", False)),
                            source=image.get("source", self.name),
                        )
                    )
                    stats.records_inserted += 1

            for identifier in item.get("identifiers") or []:
                src = identifier.get("source")
                ext = identifier.get("external_id")
                if not (src and ext):
                    continue
                existing = session.execute(
                    select(PrintIdentifier).where(PrintIdentifier.print_id == print_obj.id, PrintIdentifier.source == src)
                ).scalar_one_or_none()
                if existing:
                    existing.external_id = ext
                    stats.records_updated += 1
                else:
                    session.add(PrintIdentifier(print_id=print_obj.id, source=src, external_id=ext))
                    stats.records_inserted += 1

    def run(self, session, path: str) -> IngestStats:
        stats = IngestStats()
        source = self.ensure_source(session)
        for file_path, payload, checksum in self.load(path):
            stats.files_seen += 1
            exists = session.execute(
                select(SourceRecord).where(SourceRecord.source_id == source.id, SourceRecord.checksum == checksum)
            ).scalar_one_or_none()
            if exists:
                stats.files_skipped += 1
                continue
            session.add(SourceRecord(source_id=source.id, checksum=checksum, raw_json=payload))
            normalized = self.normalize(payload)
            self.upsert(session, normalized, stats)
        return stats
