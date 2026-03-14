from __future__ import annotations

import json
import time
from pathlib import Path

import requests
from sqlalchemy import select

from app.ingest.base import IngestStats, SourceConnector
from app.models import Card, Game, Print, PrintIdentifier, PrintImage, Set


class RiftboundConnector(SourceConnector):
    name = "riftbound"
    base_urls = ("https://api.riftbound.com/v1", "https://api.riftbound.com")

    @staticmethod
    def _normalize_language(value: object) -> str:
        language = str(value or "").strip().lower()
        return language or "en"

    @staticmethod
    def _normalize_rarity(value: object) -> str:
        rarity = str(value or "").strip()
        return rarity or "unknown"

    def load(self, path: str | Path | None = None, **kwargs) -> list[tuple[Path, dict, str]]:
        fixture = bool(kwargs.get("fixture", False))
        limit = kwargs.get("limit")

        self.logger.info("ingest riftbound load_start fixture=%s limit=%s", fixture, limit)
        records = self._load_fixture(path, limit=limit) if fixture else self._load_remote(limit=limit)

        payloads: list[tuple[Path, dict, str]] = []
        for idx, record in enumerate(records):
            payloads.append((Path(f"riftbound_print_{idx+1}.json"), record, self.checksum(record)))

        self.logger.info("ingest riftbound load_done fixture=%s records=%s limit=%s", fixture, len(payloads), limit)
        return payloads

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

    def _load_fixture(self, path: str | Path | None, limit: int | None) -> list[dict]:
        fixture_path = self._resolve_fixture_path(path)
        payload = json.loads(fixture_path.read_text(encoding="utf-8"))

        sets = {str(item.get("id") or item.get("code")): item for item in payload.get("sets") or []}
        cards = {str(item.get("id") or item.get("name")): item for item in payload.get("cards") or []}

        out: list[dict] = []
        seen_print_ids: set[str] = set()
        for idx, print_item in enumerate(payload.get("prints") or []):
            dedupe_id = str(print_item.get("id") or "").strip()
            if dedupe_id and dedupe_id in seen_print_ids:
                continue
            if dedupe_id:
                seen_print_ids.add(dedupe_id)

            record = {
                "set": sets.get(str(print_item.get("set_id"))) or sets.get(str(print_item.get("set_code"))) or {},
                "card": cards.get(str(print_item.get("card_id"))) or cards.get(str(print_item.get("card_name"))) or {},
                "print": print_item,
            }
            out.append(record)
            if limit and len(out) >= limit:
                break
            if len(out) == 1 or len(out) % 25 == 0:
                self.logger.info("ingest riftbound load_progress fixture=true processed=%s", len(out))
        return out

    def _load_remote(self, limit: int | None = None) -> list[dict]:
        payload = None
        last_error: Exception | None = None
        for base_url in self.base_urls:
            endpoint = f"{base_url.rstrip('/')}/catalog"
            self.logger.info("ingest riftbound remote_fetch_start endpoint=%s", endpoint)
            try:
                payload = self._request_json(endpoint)
                if isinstance(payload, dict):
                    break
                raise RuntimeError("Riftbound remote payload must be a JSON object")
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                self.logger.warning("ingest riftbound remote_fetch_failed endpoint=%s error=%s", endpoint, exc)

        if not isinstance(payload, dict):
            raise RuntimeError(f"Riftbound remote catalog unavailable from configured endpoints: {last_error}")

        sets = {str(item.get("id") or item.get("code")): item for item in payload.get("sets") or []}
        cards = {str(item.get("id") or item.get("name")): item for item in payload.get("cards") or []}

        out: list[dict] = []
        seen_print_ids: set[str] = set()
        for print_item in payload.get("prints") or []:
            dedupe_id = str(print_item.get("id") or "").strip()
            if dedupe_id and dedupe_id in seen_print_ids:
                continue
            if dedupe_id:
                seen_print_ids.add(dedupe_id)

            out.append(
                {
                    "set": sets.get(str(print_item.get("set_id"))) or sets.get(str(print_item.get("set_code"))) or {},
                    "card": cards.get(str(print_item.get("card_id"))) or cards.get(str(print_item.get("card_name"))) or {},
                    "print": print_item,
                }
            )
            if limit and len(out) >= limit:
                break
            if len(out) == 1 or len(out) % 25 == 0:
                self.logger.info("ingest riftbound load_progress fixture=false processed=%s", len(out))

        self.logger.info("ingest riftbound remote_fetch_done prints=%s", len(out))
        return out

    def _request_json(self, url: str, params: dict | None = None) -> dict:
        wait_seconds = 0.5
        last_error: Exception | None = None
        for attempt in range(1, 6):
            started = time.perf_counter()
            try:
                response = requests.get(url, params=params, timeout=30)
                elapsed_ms = round((time.perf_counter() - started) * 1000)
                if response.status_code in (429, 500, 502, 503, 504):
                    self.logger.warning(
                        "ingest riftbound request_retry url=%s status=%s attempt=%s elapsed_ms=%s wait_seconds=%s",
                        url,
                        response.status_code,
                        attempt,
                        elapsed_ms,
                        wait_seconds,
                    )
                    time.sleep(wait_seconds)
                    wait_seconds *= 2
                    continue
                response.raise_for_status()
                self.logger.info(
                    "ingest riftbound request_done url=%s status=%s attempt=%s elapsed_ms=%s",
                    url,
                    response.status_code,
                    attempt,
                    elapsed_ms,
                )
                return response.json()
            except requests.RequestException as exc:
                last_error = exc
                if attempt >= 5:
                    break
                time.sleep(wait_seconds)
                wait_seconds *= 2

        raise RuntimeError(f"Riftbound request failed after retries: {url} last_error={last_error}")

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
                "primary_image_url": (print_payload.get("primary_image_url") or "").strip() or None,
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
        else:
            changed = False
            if set_payload.get("name") and set_row.name != set_payload.get("name"):
                set_row.name = set_payload.get("name")
                changed = True
            if rift_set_id and set_row.riftbound_id != rift_set_id:
                set_row.riftbound_id = rift_set_id
                changed = True
            if changed:
                stats.records_updated += 1

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
        else:
            changed = False
            if card_row.name != card_name:
                card_row.name = card_name
                changed = True
            if rift_card_id and card_row.riftbound_id != rift_card_id:
                card_row.riftbound_id = rift_card_id
                changed = True
            if changed:
                stats.records_updated += 1

        print_payload = payload.get("print") or {}
        collector_number = print_payload.get("collector_number") or "unknown"
        rift_print_id = print_payload.get("riftbound_id")
        language = self._normalize_language(print_payload.get("language"))
        rarity = self._normalize_rarity(print_payload.get("rarity"))
        variant = "default"
        print_row = None
        if rift_print_id:
            print_row = session.execute(select(Print).where(Print.riftbound_id == rift_print_id)).scalar_one_or_none()
        if print_row is None:
            print_row = session.execute(
                select(Print).where(
                    Print.set_id == set_row.id,
                    Print.card_id == card_row.id,
                    Print.collector_number == collector_number,
                    Print.language == language,
                    Print.is_foil.is_(False),
                    Print.variant == variant,
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
                variant=variant,
            )
            session.add(print_row)
            session.flush()
            stats.records_inserted += 1
        else:
            changed = False
            if print_row.language != language:
                print_row.language = language
                changed = True
            if print_row.rarity != rarity:
                print_row.rarity = rarity
                changed = True
            if print_row.variant != variant:
                print_row.variant = variant
                changed = True
            if rift_print_id and print_row.riftbound_id != rift_print_id:
                print_row.riftbound_id = rift_print_id
                changed = True
            if changed:
                stats.records_updated += 1

        if rift_print_id:
            identifier = session.execute(
                select(PrintIdentifier).where(PrintIdentifier.print_id == print_row.id, PrintIdentifier.source == "riftbound")
            ).scalar_one_or_none()
            if identifier is None:
                session.add(PrintIdentifier(print_id=print_row.id, source="riftbound", external_id=rift_print_id))
                stats.records_inserted += 1
            elif identifier.external_id != rift_print_id:
                identifier.external_id = rift_print_id
                stats.records_updated += 1

        image_url = (print_payload.get("primary_image_url") or "").strip()
        if image_url:
            primary_image = session.execute(
                select(PrintImage).where(PrintImage.print_id == print_row.id, PrintImage.is_primary.is_(True))
            ).scalar_one_or_none()
            if primary_image is None:
                session.add(PrintImage(print_id=print_row.id, url=image_url, is_primary=True, source="riftbound"))
                stats.records_inserted += 1
            elif primary_image.url != image_url:
                primary_image.url = image_url
                if primary_image.source != "riftbound":
                    primary_image.source = "riftbound"
                stats.records_updated += 1

        return {"set_id": set_row.id, "card_id": card_row.id, "print_id": print_row.id}
