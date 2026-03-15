from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy import select

from app.ingest.base import IngestStats, SourceConnector
from app.ingest.connectors.riftbound_fallback import RiftboundFallbackBackend
from app.ingest.connectors.riftbound_official import RiftboundOfficialBackend
from app.ingest.connectors.riftbound_types import RiftboundBackend, RiftboundLogicalRecord
from app.ingest.normalization import normalize_variant
from app.models import Card, Game, Print, PrintIdentifier, PrintImage, Set


class RiftboundConnector(SourceConnector):
    name = "riftbound"
    mode = "configurable"
    _RIFTBOUND_IMAGE_DOMAIN = "images.riftbound.cards"
    _SET_PLACEHOLDERS: dict[str, str] = {
        "rb1": "/images/riftbound/rb1-placeholder.svg",
        "rb2": "/images/riftbound/rb2-placeholder.svg",
        "ogn": "/images/riftbound/ogn-placeholder.svg",
    }

    @staticmethod
    def _normalize_language(value: object) -> str:
        language = str(value or "").strip().lower()
        return language or "en"

    @staticmethod
    def _normalize_rarity(value: object) -> str:
        rarity = str(value or "").strip()
        return rarity or "unknown"

    @classmethod
    def _placeholder_for_set_code(cls, set_code: object) -> str:
        normalized = str(set_code or "").strip().lower()
        if normalized in cls._SET_PLACEHOLDERS:
            return cls._SET_PLACEHOLDERS[normalized]
        return cls._SET_PLACEHOLDERS["rb1"]

    @classmethod
    def _is_disallowed_image_url(cls, value: object) -> bool:
        return cls._RIFTBOUND_IMAGE_DOMAIN in str(value or "").strip().lower()

    @classmethod
    def _resolve_primary_image_url(cls, raw_image_url: object, set_code: object) -> str:
        image_url = str(raw_image_url or "").strip()
        if image_url and not cls._is_disallowed_image_url(image_url):
            return image_url
        return cls._placeholder_for_set_code(set_code)

    def _source_mode(self) -> str:
        mode = str(os.getenv("RIFTBOUND_SOURCE") or "auto").strip().lower()
        if mode not in {"official", "fallback", "auto"}:
            self.logger.warning("ingest riftbound invalid_source_mode=%s using=auto", mode)
            return "auto"
        return mode

    def _build_backends(self) -> tuple[RiftboundOfficialBackend, RiftboundFallbackBackend]:
        return RiftboundOfficialBackend(self.logger), RiftboundFallbackBackend(self.logger)

    @staticmethod
    def _is_official_degradable_error(exc: Exception) -> bool:
        message = str(exc)
        return any(token in message for token in ["status=401", "status=403", "RIFTBOUND_API_BASE_URL is required", "RIFTBOUND_API_KEY is required"])

    def _select_backend(self, *, fixture: bool = False) -> RiftboundBackend:
        official_backend, fallback_backend = self._build_backends()
        mode = self._source_mode()

        if fixture:
            self.logger.info("ingest riftbound backend_selected=%s reason=fixture_mode", fallback_backend.source_name)
            return fallback_backend

        if mode == "official":
            if not official_backend.is_configured():
                raise RuntimeError("RIFTBOUND_SOURCE=official but missing official configuration")
            self.logger.info("ingest riftbound backend_selected=official reason=env_explicit")
            return official_backend

        if mode == "fallback":
            self.logger.info("ingest riftbound backend_selected=fallback reason=env_explicit")
            return fallback_backend

        if official_backend.is_configured():
            self.logger.info("ingest riftbound backend_selected=official reason=auto_detected_credentials")
            return official_backend

        self.logger.info("ingest riftbound backend_selected=fallback reason=auto_missing_credentials")
        return fallback_backend

    @staticmethod
    def _logical_to_payload(record: RiftboundLogicalRecord) -> dict:
        set_code = record.set_code.strip().lower()
        return {
            "set": {
                "code": set_code,
                "name": record.set_name,
                "riftbound_id": record.metadata.get("set_external_id"),
            },
            "card": {
                "name": record.card_name,
                "riftbound_id": record.card_external_id,
            },
            "print": {
                "collector_number": record.collector_number,
                "set_code": set_code,
                "riftbound_id": record.print_external_id,
                "language": record.locale,
                "rarity": record.rarity,
                "variant": record.variant,
                "primary_image_url": record.image_url or record.thumbnail_url,
                "source_system": record.source_system,
                "raw_json": record.metadata,
            },
            "game_slug": record.game_slug,
        }

    def load(self, path: str | Path | None = None, **kwargs) -> list[tuple[Path, dict, str]]:
        fixture = bool(kwargs.get("fixture", False))
        limit = kwargs.get("limit")
        mode = self._source_mode()
        backend = self._select_backend(fixture=fixture)

        try:
            batch = backend.fetch_all(path=path, fixture=fixture, limit=limit)
        except RuntimeError as exc:
            if (
                not fixture
                and mode == "auto"
                and getattr(backend, "source_name", "") == "official"
                and self._is_official_degradable_error(exc)
            ):
                self.logger.warning(
                    "ingest riftbound auto_fallback_from_official reason=%s",
                    exc,
                )
                _, fallback_backend = self._build_backends()
                backend = fallback_backend
                batch = backend.fetch_all(path=path, fixture=fixture, limit=limit)
            else:
                raise

        logical_records = backend.to_logical_records(batch, path=path, fixture=fixture, limit=limit)

        payloads: list[tuple[Path, dict, str]] = []
        for idx, record in enumerate(logical_records):
            payload = self._logical_to_payload(record)
            payloads.append((Path(f"riftbound_print_{idx+1}.json"), payload, self.checksum(payload)))

        self.logger.info(
            "ingest riftbound load_done backend=%s fixture=%s records=%s limit=%s",
            backend.source_name,
            fixture,
            len(payloads),
            limit,
        )
        return payloads

    def normalize(self, payload: dict, **kwargs) -> dict:
        set_payload = payload.get("set") or {}
        card_payload = payload.get("card") or {}
        print_payload = payload.get("print") or {}
        return {
            "set": {
                "code": (set_payload.get("code") or print_payload.get("set_code") or "").strip().lower(),
                "name": (set_payload.get("name") or print_payload.get("set_name") or "").strip(),
                "riftbound_id": str(set_payload.get("riftbound_id")) if set_payload.get("riftbound_id") is not None else None,
            },
            "card": {
                "name": (card_payload.get("name") or print_payload.get("card_name") or "").strip(),
                "riftbound_id": str(card_payload.get("riftbound_id")) if card_payload.get("riftbound_id") is not None else None,
            },
            "print": {
                "collector_number": str(print_payload.get("collector_number") or "").strip(),
                "set_code": (print_payload.get("set_code") or "").strip().lower(),
                "riftbound_id": str(print_payload.get("riftbound_id")) if print_payload.get("riftbound_id") is not None else None,
                "language": self._normalize_language(print_payload.get("language") or card_payload.get("language")),
                "rarity": self._normalize_rarity(print_payload.get("rarity")),
                "raw_json": print_payload.get("raw_json") or print_payload,
                "variant": normalize_variant(print_payload.get("variant")),
                "primary_image_url": self._resolve_primary_image_url(
                    print_payload.get("primary_image_url"),
                    set_payload.get("code") or print_payload.get("set_code"),
                ),
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
        variant = normalize_variant(print_payload.get("variant"))
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

        image_url = self._resolve_primary_image_url(print_payload.get("primary_image_url"), set_code)
        primary_image = session.execute(
            select(PrintImage).where(PrintImage.print_id == print_row.id, PrintImage.is_primary.is_(True))
        ).scalar_one_or_none()
        image_source = str(print_payload.get("source_system") or "riftbound")
        if primary_image is None:
            session.add(PrintImage(print_id=print_row.id, url=image_url, is_primary=True, source=image_source))
            stats.records_inserted += 1
        elif primary_image.url != image_url or primary_image.source != image_source:
            primary_image.url = image_url
            primary_image.source = image_source
            stats.records_updated += 1

        return {"set_id": set_row.id, "card_id": card_row.id, "print_id": print_row.id}
