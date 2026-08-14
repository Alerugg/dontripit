from __future__ import annotations

import hashlib
import json
import logging
import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.ingest.base import BaseConnector, IngestStats
from app.models import (
    Card,
    Game,
    PokemonCardDetail,
    Print,
    PrintIdentifier,
    PrintImage,
    Set,
)
from app.multilingual_models import CardIdentifier, PrintLocalization, SetIdentifier


class MultilingualTcgdexPokemonConnector(BaseConnector):
    """TCGdex Pokémon connector with certified EN/ES/JA identity semantics.

    EN is the canonical international identity space. ES overlays localized
    physical Prints on an existing exact EN Card/Set identity. JA is a distinct
    regional physical identity space and owns independent Card/Set rows so raw
    ID collisions with EN cannot silently merge unrelated cards.
    """

    source_name = "tcgdex_pokemon"
    game_slug = "pokemon"
    base_url_template = "https://api.tcgdex.net/v2/{lang}"
    certified_languages = frozenset({"en", "es", "ja"})

    logger = logging.getLogger("app.ingest")

    @classmethod
    def _assert_certified_language(cls, lang: str | None) -> str:
        normalized = str(lang or "en").strip().lower()
        if normalized not in cls.certified_languages:
            raise RuntimeError(
                "Uncertified TCGdex language semantics: "
                f"language={normalized!r} certified={sorted(cls.certified_languages)}"
            )
        return normalized

    @staticmethod
    def _source_namespace(language: str) -> str:
        return f"tcgdex:{language}"

    @staticmethod
    def _normalize_external_id(value: Any) -> str:
        return str(value or "").strip()

    @staticmethod
    def _normalize_set_code(value: Any) -> str:
        return str(value or "").strip()

    @staticmethod
    def _slug(value: Any) -> str:
        text = str(value or "").strip().lower()
        text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
        return text or "unknown"

    @staticmethod
    def _stable_checksum(payload: dict) -> str:
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
            "utf-8"
        )
        return hashlib.sha256(encoded).hexdigest()

    def _request_json(self, url: str, params=None):
        import requests

        response = requests.get(url, params=params, timeout=60)
        response.raise_for_status()
        return response.json()

    def _build_card_payload(self, set_payload: dict, card: dict, *, lang: str) -> dict:
        payload = dict(card)
        payload["set"] = {
            "id": set_payload.get("id"),
            "abbreviation": set_payload.get("abbreviation"),
            "name": set_payload.get("name"),
            "releaseDate": set_payload.get("releaseDate"),
        }
        payload["_language"] = lang
        return payload

    def _load_remote(
        self,
        limit: int | None = None,
        set_id: str | None = None,
        lang: str = "en",
    ) -> list[dict]:
        language = self._assert_certified_language(lang)
        if limit is not None and limit <= 0:
            return []
        base_url = self.base_url_template.format(lang=language)

        if set_id:
            set_payload = self._request_json(f"{base_url}/sets/{set_id}")
            if not isinstance(set_payload, dict):
                raise RuntimeError(
                    f"Unexpected TCGdex set payload for {language}/{set_id}: "
                    f"{type(set_payload).__name__}"
                )
            cards = [item for item in (set_payload.get("cards") or []) if isinstance(item, dict)]
            if limit:
                cards = cards[:limit]
            return [self._build_card_payload(set_payload, card, lang=language) for card in cards]

        sets = self._request_json(f"{base_url}/sets")
        if not isinstance(sets, list):
            raise RuntimeError(
                f"Unexpected TCGdex sets payload for {language}: {type(sets).__name__}"
            )

        out: list[dict] = []
        seen_card_ids: set[str] = set()
        for set_brief in sets:
            if not isinstance(set_brief, dict):
                continue
            remote_set_id = self._normalize_external_id(set_brief.get("id"))
            if not remote_set_id:
                continue
            set_payload = self._request_json(f"{base_url}/sets/{remote_set_id}")
            if not isinstance(set_payload, dict):
                raise RuntimeError(
                    f"Unexpected TCGdex set payload for {language}/{remote_set_id}: "
                    f"{type(set_payload).__name__}"
                )
            for card in (set_payload.get("cards") or []):
                if not isinstance(card, dict):
                    continue
                card_id = self._normalize_external_id(card.get("id"))
                if not card_id or card_id in seen_card_ids:
                    continue
                seen_card_ids.add(card_id)
                out.append(self._build_card_payload(set_payload, card, lang=language))
                if limit and len(out) >= limit:
                    return out
        return out

    def load(
        self,
        source: str | None = None,
        *,
        fixture: bool = False,
        limit: int | None = None,
        set_id: str | None = None,
        lang: str = "en",
        **kwargs,
    ) -> list[dict]:
        language = self._assert_certified_language(lang)
        if fixture:
            if not source:
                raise ValueError("fixture source path is required")
            with open(source, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
            rows = payload if isinstance(payload, list) else payload.get("data") or payload.get("cards") or []
            if limit:
                rows = rows[:limit]
            result = []
            for row in rows:
                if not isinstance(row, dict):
                    continue
                copy = dict(row)
                copy.setdefault("_language", language)
                result.append(copy)
            return result
        return self._load_remote(limit=limit, set_id=set_id, lang=language)

    def normalize(self, raw: dict, *, lang: str | None = None, **kwargs) -> dict:
        language = self._assert_certified_language(lang or raw.get("_language"))
        remote_set = raw.get("set") or {}
        remote_set_id = self._normalize_external_id(remote_set.get("id"))
        remote_card_id = self._normalize_external_id(raw.get("id"))
        if not remote_set_id or not remote_card_id:
            raise ValueError("TCGdex card requires set.id and id")

        local_id = self._normalize_external_id(raw.get("localId"))
        image = str(raw.get("image") or "").strip() or None
        card_name = str(raw.get("name") or remote_card_id).strip()
        set_name = str(remote_set.get("name") or remote_set_id).strip()
        release_date = remote_set.get("releaseDate") or None

        detail = {
            "hp": raw.get("hp"),
            "stage": raw.get("stage"),
            "suffix": raw.get("suffix"),
            "types": raw.get("types") or [],
            "abilities": raw.get("abilities") or [],
            "attacks": raw.get("attacks") or [],
            "rules": raw.get("rules") or [],
            "weaknesses": raw.get("weaknesses") or [],
            "resistances": raw.get("resistances") or [],
            "retreat": raw.get("retreat"),
            "regulationMark": raw.get("regulationMark"),
            "illustrator": raw.get("illustrator"),
            "category": raw.get("category"),
            "dexId": raw.get("dexId") or [],
        }
        localization = {
            "card_name": card_name,
            "set_name": set_name,
            "details": detail,
        }
        return {
            "language": language,
            "set_external_id": remote_set_id,
            "set_code": self._normalize_set_code(remote_set.get("abbreviation") or remote_set_id),
            "set_name": set_name,
            "set_release_date": release_date,
            "card_external_id": remote_card_id,
            "card_name": card_name,
            "collector_number": local_id or remote_card_id,
            "image": image,
            "localization": localization,
            "raw": raw,
        }

    def _game(self, session) -> Game:
        row = session.execute(select(Game).where(Game.slug == self.game_slug)).scalar_one_or_none()
        if row is None:
            row = Game(slug=self.game_slug, name="Pokémon")
            session.add(row)
            session.flush()
        return row

    def _resolve_exact_international_identity(
        self,
        session,
        *,
        game_id: int,
        set_external_id: str,
        card_external_id: str,
    ) -> tuple[Set | None, Card | None]:
        set_row = session.execute(
            select(Set).where(
                Set.game_id == game_id,
                Set.tcgdex_id == set_external_id,
            )
        ).scalar_one_or_none()
        card_row = session.execute(
            select(Card).where(
                Card.game_id == game_id,
                Card.tcgdex_id == card_external_id,
            )
        ).scalar_one_or_none()
        return set_row, card_row

    def _upsert_set_identifier(
        self,
        session,
        *,
        set_row: Set,
        language: str,
        external_id: str,
        stats: IngestStats,
    ) -> None:
        source = self._source_namespace(language)
        by_external = session.execute(
            select(SetIdentifier).where(
                SetIdentifier.source == source,
                SetIdentifier.external_id == external_id,
            )
        ).scalar_one_or_none()
        if by_external is not None and by_external.set_id != set_row.id:
            raise RuntimeError(
                "TCGdex set identifier collision: "
                f"source={source} external_id={external_id} "
                f"existing_set_id={by_external.set_id} target_set_id={set_row.id}"
            )
        by_entity = session.execute(
            select(SetIdentifier).where(
                SetIdentifier.set_id == set_row.id,
                SetIdentifier.source == source,
            )
        ).scalar_one_or_none()
        if by_entity is None:
            session.add(SetIdentifier(set_id=set_row.id, source=source, external_id=external_id))
            stats.records_inserted += 1
        elif by_entity.external_id != external_id:
            raise RuntimeError(
                "TCGdex set source identity changed unexpectedly: "
                f"set_id={set_row.id} source={source} "
                f"old={by_entity.external_id} new={external_id}"
            )

    def _upsert_card_identifier(
        self,
        session,
        *,
        card_row: Card,
        language: str,
        external_id: str,
        stats: IngestStats,
    ) -> None:
        """Upsert a source alias without assuming one source ID per Card.

        Reprints can map several TCGdex physical IDs to the same canonical
        gameplay Card. The collision boundary is therefore the external source
        identity itself: one ``(source, external_id)`` may never point to two
        different Cards, while many external IDs may point to one Card.
        """
        source = self._source_namespace(language)
        by_external = session.execute(
            select(CardIdentifier).where(
                CardIdentifier.source == source,
                CardIdentifier.external_id == external_id,
            )
        ).scalar_one_or_none()
        if by_external is not None:
            if by_external.card_id != card_row.id:
                raise RuntimeError(
                    "TCGdex card identifier collision: "
                    f"source={source} external_id={external_id} "
                    f"existing_card_id={by_external.card_id} target_card_id={card_row.id}"
                )
            return

        session.add(CardIdentifier(card_id=card_row.id, source=source, external_id=external_id))
        stats.records_inserted += 1

    def _upsert_language_identifier(
        self,
        session,
        *,
        print_row: Print,
        language: str,
        external_id: str,
        stats: IngestStats,
    ) -> None:
        source = self._source_namespace(language)
        by_external = session.execute(
            select(PrintIdentifier).where(
                PrintIdentifier.source == source,
                PrintIdentifier.external_id == external_id,
            )
        ).scalar_one_or_none()
        if by_external is not None and by_external.print_id != print_row.id:
            raise RuntimeError(
                "TCGdex print identifier collision: "
                f"source={source} external_id={external_id} "
                f"existing_print_id={by_external.print_id} target_print_id={print_row.id}"
            )

        identifier = session.execute(
            select(PrintIdentifier).where(
                PrintIdentifier.print_id == print_row.id,
                PrintIdentifier.source == source,
            )
        ).scalar_one_or_none()
        if identifier is None:
            session.add(
                PrintIdentifier(
                    print_id=print_row.id,
                    source=source,
                    external_id=external_id,
                )
            )
            stats.records_inserted += 1
        elif identifier.external_id != external_id:
            raise RuntimeError(
                "TCGdex print source identity changed unexpectedly: "
                f"print_id={print_row.id} source={source} "
                f"old={identifier.external_id} new={external_id}"
            )

    def _upsert_localization(
        self,
        session,
        *,
        print_row: Print,
        language: str,
        external_id: str | None,
        localization: dict,
        stats: IngestStats,
    ) -> None:
        source = "tcgdex"
        row = session.execute(
            select(PrintLocalization).where(
                PrintLocalization.print_id == print_row.id,
                PrintLocalization.language == language,
                PrintLocalization.source == source,
            )
        ).scalar_one_or_none()
        values = {
            "external_id": external_id,
            "card_name": localization.get("card_name"),
            "set_name": localization.get("set_name"),
            "details_json": localization.get("details") or {},
        }
        if row is None:
            row = PrintLocalization(
                print_id=print_row.id,
                language=language,
                source=source,
                **values,
            )
            session.add(row)
            stats.records_inserted += 1
            return
        changed = False
        for field, value in values.items():
            if getattr(row, field) != value:
                setattr(row, field, value)
                changed = True
        if changed:
            stats.records_updated += 1

    def _upsert_image(
        self,
        session,
        *,
        print_row: Print,
        image_url: str | None,
        language: str,
        stats: IngestStats,
    ) -> None:
        if not image_url:
            return
        row = session.execute(
            select(PrintImage).where(
                PrintImage.print_id == print_row.id,
                PrintImage.url == image_url,
            )
        ).scalar_one_or_none()
        if row is None:
            session.add(
                PrintImage(
                    print_id=print_row.id,
                    url=image_url,
                    is_primary=True,
                    source=f"tcgdex:{language}",
                )
            )
            stats.records_inserted += 1
            return
        if not row.is_primary:
            row.is_primary = True
            stats.records_updated += 1

    def _upsert_detail(
        self,
        session,
        *,
        card_row: Card,
        detail: dict,
        stats: IngestStats,
    ) -> None:
        row = session.execute(
            select(PokemonCardDetail).where(PokemonCardDetail.card_id == card_row.id)
        ).scalar_one_or_none()
        values = {
            "hp": str(detail.get("hp")) if detail.get("hp") is not None else None,
            "stage": detail.get("stage"),
            "suffix": detail.get("suffix"),
            "types_json": detail.get("types") or [],
            "abilities_json": detail.get("abilities") or [],
            "attacks_json": detail.get("attacks") or [],
            "rules_json": detail.get("rules") or [],
            "weaknesses_json": detail.get("weaknesses") or [],
            "resistances_json": detail.get("resistances") or [],
            "retreat": detail.get("retreat"),
            "regulation_mark": detail.get("regulationMark"),
            "illustrator": detail.get("illustrator"),
            "category": detail.get("category"),
            "dex_ids_json": detail.get("dexId") or [],
        }
        if row is None:
            session.add(PokemonCardDetail(card_id=card_row.id, **values))
            stats.records_inserted += 1
            return
        changed = False
        for field, value in values.items():
            if getattr(row, field) != value:
                setattr(row, field, value)
                changed = True
        if changed:
            stats.records_updated += 1

    def _find_existing_print(
        self,
        session,
        *,
        card_row: Card,
        set_row: Set,
        collector_number: str,
        language: str,
    ) -> Print | None:
        return session.execute(
            select(Print).where(
                Print.card_id == card_row.id,
                Print.set_id == set_row.id,
                Print.collector_number == collector_number,
                Print.language == language,
                Print.variant == "default",
            )
        ).scalar_one_or_none()

    def _upsert_english(self, session, payload: dict, stats: IngestStats, **kwargs) -> None:
        game = self._game(session)
        set_external_id = payload["set_external_id"]
        card_external_id = payload["card_external_id"]

        # Legacy EN canonical semantics are intentionally preserved. Existing
        # canonical identity helpers may merge reprints on gameplay identity;
        # the new CardIdentifier layer stores every physical TCGdex alias.
        set_row = session.execute(
            select(Set).where(
                Set.game_id == game.id,
                Set.tcgdex_id == set_external_id,
            )
        ).scalar_one_or_none()
        if set_row is None:
            set_row = Set(
                game_id=game.id,
                code=payload["set_code"] or set_external_id,
                tcgdex_id=set_external_id,
                name=payload["set_name"],
                release_date=payload["set_release_date"],
            )
            session.add(set_row)
            session.flush()
            stats.records_inserted += 1

        card_row = session.execute(
            select(Card).where(
                Card.game_id == game.id,
                Card.tcgdex_id == card_external_id,
            )
        ).scalar_one_or_none()
        if card_row is None:
            # Preserve the existing canonical gameplay merge contract by name
            # when no direct source identity exists. This is the legacy path
            # exercised by Pokémon reprint regression tests.
            card_row = session.execute(
                select(Card).where(
                    Card.game_id == game.id,
                    Card.name == payload["card_name"],
                ).order_by(Card.id.asc())
            ).scalars().first()
        if card_row is None:
            card_row = Card(
                game_id=game.id,
                name=payload["card_name"],
                tcgdex_id=card_external_id,
                card_key=f"tcgdex:{card_external_id}",
            )
            session.add(card_row)
            session.flush()
            stats.records_inserted += 1

        print_row = self._find_existing_print(
            session,
            card_row=card_row,
            set_row=set_row,
            collector_number=payload["collector_number"],
            language="en",
        )
        if print_row is None:
            print_row = Print(
                set_id=set_row.id,
                card_id=card_row.id,
                collector_number=payload["collector_number"],
                language="en",
                rarity=payload["raw"].get("rarity"),
                is_foil=False,
                variant="default",
                print_key=f"pokemon:{set_external_id}:{payload['collector_number']}:en:default",
                tcgdex_id=card_external_id,
            )
            session.add(print_row)
            session.flush()
            stats.records_inserted += 1

        self._upsert_set_identifier(
            session,
            set_row=set_row,
            language="en",
            external_id=set_external_id,
            stats=stats,
        )
        self._upsert_card_identifier(
            session,
            card_row=card_row,
            language="en",
            external_id=card_external_id,
            stats=stats,
        )
        self._upsert_language_identifier(
            session,
            print_row=print_row,
            language="en",
            external_id=card_external_id,
            stats=stats,
        )
        self._upsert_localization(
            session,
            print_row=print_row,
            language="en",
            external_id=card_external_id,
            localization=payload["localization"],
            stats=stats,
        )
        self._upsert_image(
            session,
            print_row=print_row,
            image_url=payload["image"],
            language="en",
            stats=stats,
        )
        self._upsert_detail(
            session,
            card_row=card_row,
            detail=payload["localization"]["details"],
            stats=stats,
        )

    def _upsert_spanish(self, session, payload: dict, stats: IngestStats, **kwargs) -> None:
        game = self._game(session)
        set_row, card_row = self._resolve_exact_international_identity(
            session,
            game_id=game.id,
            set_external_id=payload["set_external_id"],
            card_external_id=payload["card_external_id"],
        )
        if set_row is None or card_row is None:
            # ES is an overlay only. Never create a new canonical identity from
            # localized metadata when exact EN identity is unavailable.
            return

        print_row = self._find_existing_print(
            session,
            card_row=card_row,
            set_row=set_row,
            collector_number=payload["collector_number"],
            language="es",
        )
        if print_row is None:
            print_row = Print(
                set_id=set_row.id,
                card_id=card_row.id,
                collector_number=payload["collector_number"],
                language="es",
                rarity=payload["raw"].get("rarity"),
                is_foil=False,
                variant="default",
                print_key=(
                    f"pokemon:{payload['set_external_id']}:{payload['collector_number']}:es:default"
                ),
                tcgdex_id=None,
            )
            session.add(print_row)
            session.flush()
            stats.records_inserted += 1

        self._upsert_set_identifier(
            session,
            set_row=set_row,
            language="es",
            external_id=payload["set_external_id"],
            stats=stats,
        )
        self._upsert_card_identifier(
            session,
            card_row=card_row,
            language="es",
            external_id=payload["card_external_id"],
            stats=stats,
        )
        self._upsert_language_identifier(
            session,
            print_row=print_row,
            language="es",
            external_id=payload["card_external_id"],
            stats=stats,
        )
        self._upsert_localization(
            session,
            print_row=print_row,
            language="es",
            external_id=payload["card_external_id"],
            localization=payload["localization"],
            stats=stats,
        )
        self._upsert_image(
            session,
            print_row=print_row,
            image_url=payload["image"],
            language="es",
            stats=stats,
        )

    def _upsert_japanese(self, session, payload: dict, stats: IngestStats, **kwargs) -> None:
        game = self._game(session)
        set_source = self._source_namespace("ja")
        set_identifier = session.execute(
            select(SetIdentifier).where(
                SetIdentifier.source == set_source,
                SetIdentifier.external_id == payload["set_external_id"],
            )
        ).scalar_one_or_none()
        set_row = session.get(Set, set_identifier.set_id) if set_identifier else None
        if set_row is None:
            code = f"ja-{payload['set_external_id']}"
            set_row = session.execute(
                select(Set).where(Set.game_id == game.id, Set.code == code)
            ).scalar_one_or_none()
            if set_row is None:
                set_row = Set(
                    game_id=game.id,
                    code=code,
                    tcgdex_id=None,
                    name=payload["set_name"],
                    release_date=payload["set_release_date"],
                )
                session.add(set_row)
                session.flush()
                stats.records_inserted += 1

        card_source = self._source_namespace("ja")
        card_identifier = session.execute(
            select(CardIdentifier).where(
                CardIdentifier.source == card_source,
                CardIdentifier.external_id == payload["card_external_id"],
            )
        ).scalar_one_or_none()
        card_row = session.get(Card, card_identifier.card_id) if card_identifier else None
        if card_row is None:
            card_key = f"tcgdex:ja:{payload['card_external_id']}"
            card_row = session.execute(
                select(Card).where(Card.game_id == game.id, Card.card_key == card_key)
            ).scalar_one_or_none()
            if card_row is None:
                card_row = Card(
                    game_id=game.id,
                    name=payload["card_name"],
                    card_key=card_key,
                    tcgdex_id=None,
                )
                session.add(card_row)
                session.flush()
                stats.records_inserted += 1

        print_row = self._find_existing_print(
            session,
            card_row=card_row,
            set_row=set_row,
            collector_number=payload["collector_number"],
            language="ja",
        )
        if print_row is None:
            print_row = Print(
                set_id=set_row.id,
                card_id=card_row.id,
                collector_number=payload["collector_number"],
                language="ja",
                rarity=payload["raw"].get("rarity"),
                is_foil=False,
                variant="default",
                print_key=(
                    f"pokemon:ja:{payload['set_external_id']}:{payload['collector_number']}:default"
                ),
                tcgdex_id=None,
            )
            session.add(print_row)
            session.flush()
            stats.records_inserted += 1

        self._upsert_set_identifier(
            session,
            set_row=set_row,
            language="ja",
            external_id=payload["set_external_id"],
            stats=stats,
        )
        self._upsert_card_identifier(
            session,
            card_row=card_row,
            language="ja",
            external_id=payload["card_external_id"],
            stats=stats,
        )
        self._upsert_language_identifier(
            session,
            print_row=print_row,
            language="ja",
            external_id=payload["card_external_id"],
            stats=stats,
        )
        self._upsert_localization(
            session,
            print_row=print_row,
            language="ja",
            external_id=payload["card_external_id"],
            localization=payload["localization"],
            stats=stats,
        )
        self._upsert_image(
            session,
            print_row=print_row,
            image_url=payload["image"],
            language="ja",
            stats=stats,
        )
        self._upsert_detail(
            session,
            card_row=card_row,
            detail=payload["localization"]["details"],
            stats=stats,
        )

    def upsert(self, session, payload: dict, stats: IngestStats, **kwargs) -> None:
        language = self._assert_certified_language(kwargs.get("lang") or payload.get("language"))
        if language == "en":
            return self._upsert_english(session, payload, stats, **kwargs)
        if language == "es":
            return self._upsert_spanish(session, payload, stats, **kwargs)
        if language == "ja":
            return self._upsert_japanese(session, payload, stats, **kwargs)
        raise RuntimeError(f"Unsupported certified language: {language}")

    def run(
        self,
        session,
        source: str | None = None,
        *,
        fixture: bool = False,
        incremental: bool = False,
        limit: int | None = None,
        set_id: str | None = None,
        lang: str = "en",
        **kwargs,
    ) -> IngestStats:
        language = self._assert_certified_language(lang)
        stats = IngestStats()
        rows = self.load(
            source,
            fixture=fixture,
            limit=limit,
            set_id=set_id,
            lang=language,
        )
        stats.files_seen = len(rows)
        for raw in rows:
            try:
                payload = self.normalize(raw, lang=language)
                if incremental:
                    checksum = self._stable_checksum(payload["raw"])
                    # Keep incremental semantics lightweight and deterministic:
                    # if the language-scoped print identifier already exists,
                    # this physical source row has already been materialized.
                    existing = session.execute(
                        select(PrintIdentifier).where(
                            PrintIdentifier.source == self._source_namespace(language),
                            PrintIdentifier.external_id == payload["card_external_id"],
                        )
                    ).scalar_one_or_none()
                    if existing is not None:
                        stats.files_skipped += 1
                        continue
                    _ = checksum
                self.upsert(session, payload, stats, lang=language, **kwargs)
                session.flush()
            except IntegrityError:
                session.rollback()
                stats.errors += 1
                raise
            except Exception:
                stats.errors += 1
                raise
        return stats
