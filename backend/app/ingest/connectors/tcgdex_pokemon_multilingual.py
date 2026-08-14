from __future__ import annotations

import hashlib
from datetime import date

from sqlalchemy import select

from app.ingest.base import IngestStats
from app.ingest.connectors.tcgdex_pokemon import TcgdexPokemonConnector
from app.ingest.normalization import normalize_language
from app.ingest.provenance import upsert_field_provenance
from app.models import Card, Game, Print, PrintIdentifier, PrintImage, Set
from app.multilingual_models import (
    CardIdentifier,
    PrintLocalization,
    SetIdentifier,
)


class MultilingualTcgdexPokemonConnector(TcgdexPokemonConnector):
    """Language-safe TCGdex writer for the certified EN/ES/JA scope.

    Live TCGdex validation on 2026-08-14 proved two different source semantics:
    EN/ES share international card IDs, while JA is a separate physical catalog
    whose IDs may even collide with unrelated EN cards. Therefore:

    * EN is the legacy canonical international catalog.
    * ES overlays localized content onto the exact EN card/set identity.
    * JA owns independent Card/Set/Print rows and only language-qualified source
      identifiers; it never resolves through global legacy ``tcgdex_id`` fields.
    """

    CERTIFIED_LANGUAGES = {"en", "es", "ja"}

    @staticmethod
    def _source_namespace(language: str) -> str:
        return f"tcgdex:{normalize_language(language)}"

    @staticmethod
    def _regional_set_code(language: str, external_id: str) -> str:
        base = f"{language}-{external_id}".lower()
        if len(base) <= 50:
            return base
        digest = hashlib.sha1(base.encode("utf-8")).hexdigest()[:10]
        return f"{base[:39]}-{digest}"

    @staticmethod
    def _regional_card_key(language: str, external_id: str) -> str:
        return f"tcgdex:{language}:{external_id}"[:255]

    def _assert_certified_language(self, language: str) -> str:
        normalized = normalize_language(language)
        if normalized not in self.CERTIFIED_LANGUAGES:
            raise RuntimeError(
                f"Uncertified TCGdex language semantics: {normalized!r}. "
                "Only EN/ES/JA are enabled by multilingual-v1."
            )
        return normalized

    def _build_card_payload(self, set_payload: dict, card_payload: dict, *, lang: str = "en") -> dict:
        language = self._assert_certified_language(lang)
        payload = super()._build_card_payload(set_payload, card_payload, lang=language)
        payload["_language"] = language
        return payload

    def load(self, path=None, **kwargs):
        language = self._assert_certified_language(kwargs.get("lang", "en"))
        rows = super().load(path, **kwargs)
        enriched = []
        for file_path, payload, _checksum in rows:
            localized_payload = dict(payload)
            localized_payload["_language"] = self._assert_certified_language(
                localized_payload.get("_language") or language
            )
            enriched.append((file_path, localized_payload, self.checksum(localized_payload)))
        return enriched

    def normalize(self, payload: dict, **kwargs) -> dict:
        normalized = super().normalize(payload, **kwargs)
        language = self._assert_certified_language(
            payload.get("_language") or kwargs.get("lang") or "en"
        )
        card_payload = normalized.get("card") or {}
        set_payload = normalized.get("set") or {}
        normalized["language"] = language
        normalized["localization"] = {
            "card_name": card_payload.get("name"),
            "set_name": set_payload.get("name"),
            "details": {
                "hp": card_payload.get("hp"),
                "stage": card_payload.get("stage"),
                "suffix": card_payload.get("suffix"),
                "evolves_from": card_payload.get("evolves_from"),
                "types": card_payload.get("types"),
                "abilities": card_payload.get("abilities"),
                "attacks": card_payload.get("attacks"),
                "rules": card_payload.get("rules"),
                "effect": card_payload.get("effect"),
            },
        }
        return normalized

    def _find_print(
        self,
        session,
        set_id: int,
        card_id: int,
        collector_number: str,
        tcgdex_print_id: str | None,
        language: str = "en",
        is_foil: bool = False,
        variant: str = "default",
    ) -> Print | None:
        language = self._assert_certified_language(language)
        if tcgdex_print_id:
            source = self._source_namespace(language)
            identifier = session.execute(
                select(PrintIdentifier).where(
                    PrintIdentifier.source == source,
                    PrintIdentifier.external_id == tcgdex_print_id,
                )
            ).scalar_one_or_none()
            if identifier is not None:
                return session.get(Print, identifier.print_id)

            if language == "en":
                row = session.execute(
                    select(Print).where(Print.tcgdex_id == tcgdex_print_id)
                ).scalar_one_or_none()
                if row is not None:
                    return row

        return session.execute(
            select(Print).where(
                Print.set_id == set_id,
                Print.collector_number == collector_number,
                Print.language == language,
                Print.is_foil.is_(is_foil),
                Print.variant == variant,
            )
        ).scalar_one_or_none()

    def _set_by_source(self, session, *, language: str, external_id: str) -> Set | None:
        identifier = session.execute(
            select(SetIdentifier).where(
                SetIdentifier.source == self._source_namespace(language),
                SetIdentifier.external_id == external_id,
            )
        ).scalar_one_or_none()
        return session.get(Set, identifier.set_id) if identifier is not None else None

    def _card_by_source(self, session, *, language: str, external_id: str) -> Card | None:
        identifier = session.execute(
            select(CardIdentifier).where(
                CardIdentifier.source == self._source_namespace(language),
                CardIdentifier.external_id == external_id,
            )
        ).scalar_one_or_none()
        return session.get(Card, identifier.card_id) if identifier is not None else None

    def _find_entities_for_language(self, session, game_id: int, payload: dict):
        language = self._assert_certified_language(payload.get("language") or "en")
        set_payload = payload.get("set") or {}
        card_payload = payload.get("card") or {}
        set_external_id = (set_payload.get("tcgdex_id") or "").strip()
        card_external_id = (card_payload.get("id") or "").strip()

        if language == "ja":
            set_row = (
                self._set_by_source(session, language=language, external_id=set_external_id)
                if set_external_id
                else None
            )
            card_row = (
                self._card_by_source(session, language=language, external_id=card_external_id)
                if card_external_id
                else None
            )
            return set_row, card_row

        # EN/ES share the international TCGdex identity. Use only exact legacy
        # TCGdex IDs here; never fall back to localized code/name heuristics.
        set_row = None
        card_row = None
        if set_external_id:
            set_row = session.execute(
                select(Set).where(
                    Set.game_id == game_id,
                    Set.tcgdex_id == set_external_id,
                )
            ).scalar_one_or_none()
        if card_external_id:
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
        source = self._source_namespace(language)
        by_external = session.execute(
            select(CardIdentifier).where(
                CardIdentifier.source == source,
                CardIdentifier.external_id == external_id,
            )
        ).scalar_one_or_none()
        if by_external is not None and by_external.card_id != card_row.id:
            raise RuntimeError(
                "TCGdex card identifier collision: "
                f"source={source} external_id={external_id} "
                f"existing_card_id={by_external.card_id} target_card_id={card_row.id}"
            )
        by_entity = session.execute(
            select(CardIdentifier).where(
                CardIdentifier.card_id == card_row.id,
                CardIdentifier.source == source,
            )
        ).scalar_one_or_none()
        if by_entity is None:
            session.add(CardIdentifier(card_id=card_row.id, source=source, external_id=external_id))
            stats.records_inserted += 1
        elif by_entity.external_id != external_id:
            raise RuntimeError(
                "TCGdex card source identity changed unexpectedly: "
                f"card_id={card_row.id} source={source} "
                f"old={by_entity.external_id} new={external_id}"
            )

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
            session.add(
                PrintLocalization(
                    print_id=print_row.id,
                    language=language,
                    source=source,
                    **values,
                )
            )
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
        language: str,
        card_payload: dict,
        stats: IngestStats,
    ) -> None:
        image_url = self._primary_image_url_from_base(card_payload.get("image"))
        if not image_url:
            return
        source = self._source_namespace(language)
        image = session.execute(
            select(PrintImage).where(
                PrintImage.print_id == print_row.id,
                PrintImage.is_primary.is_(True),
            )
        ).scalar_one_or_none()
        if image is None:
            session.add(
                PrintImage(
                    print_id=print_row.id,
                    url=image_url,
                    is_primary=True,
                    source=source,
                )
            )
            stats.records_inserted += 1
        elif image.url != image_url or image.source != source:
            image.url = image_url
            image.source = source
            stats.records_updated += 1

    def _localized_state_complete(self, session, normalized: dict) -> bool:
        language = self._assert_certified_language(normalized.get("language") or "en")
        game = self._find_pokemon_game(session)
        if game is None:
            return False

        set_payload = normalized.get("set") or {}
        card_payload = normalized.get("card") or {}
        set_row, card_row = self._find_entities_for_language(session, game.id, normalized)
        collector_number = (card_payload.get("collector_number") or "").strip()
        external_id = (card_payload.get("id") or "").strip()
        set_external_id = (set_payload.get("tcgdex_id") or "").strip()
        if (
            set_row is None
            or card_row is None
            or not collector_number
            or not external_id
            or not set_external_id
        ):
            return False

        print_row = self._find_print(
            session,
            set_row.id,
            card_row.id,
            collector_number,
            external_id,
            language=language,
            is_foil=False,
            variant="default",
        )
        if print_row is None:
            return False
        if language == "en" and print_row.tcgdex_id != external_id:
            return False
        if language != "en" and print_row.tcgdex_id is not None:
            return False

        source = self._source_namespace(language)
        print_identifier = session.execute(
            select(PrintIdentifier).where(
                PrintIdentifier.print_id == print_row.id,
                PrintIdentifier.source == source,
                PrintIdentifier.external_id == external_id,
            )
        ).scalar_one_or_none()
        card_identifier = session.execute(
            select(CardIdentifier).where(
                CardIdentifier.card_id == card_row.id,
                CardIdentifier.source == source,
                CardIdentifier.external_id == external_id,
            )
        ).scalar_one_or_none()
        set_identifier = session.execute(
            select(SetIdentifier).where(
                SetIdentifier.set_id == set_row.id,
                SetIdentifier.source == source,
                SetIdentifier.external_id == set_external_id,
            )
        ).scalar_one_or_none()
        localization = session.execute(
            select(PrintLocalization).where(
                PrintLocalization.print_id == print_row.id,
                PrintLocalization.language == language,
                PrintLocalization.source == "tcgdex",
            )
        ).scalar_one_or_none()
        return all(
            row is not None
            for row in (print_identifier, card_identifier, set_identifier, localization)
        )

    def should_skip_existing_record(self, existing_record, **kwargs) -> bool:
        session = kwargs.get("session")
        if session is None:
            return False
        normalized = self.normalize(existing_record.raw_json or {}, **kwargs)
        return self._localized_state_complete(session, normalized)

    def _create_or_update_ja_entities(
        self,
        session,
        *,
        game: Game,
        payload: dict,
        stats: IngestStats,
    ) -> tuple[Set, Card] | tuple[None, None]:
        language = "ja"
        set_payload = payload.get("set") or {}
        card_payload = payload.get("card") or {}
        set_external_id = (set_payload.get("tcgdex_id") or "").strip()
        card_external_id = (card_payload.get("id") or "").strip()
        card_name = (card_payload.get("name") or "").strip()
        if not set_external_id or not card_external_id or not card_name:
            return None, None

        set_row = self._set_by_source(
            session, language=language, external_id=set_external_id
        )
        release_date = (
            date.fromisoformat(set_payload["released_at"])
            if set_payload.get("released_at")
            else None
        )
        if set_row is None:
            set_row = Set(
                game_id=game.id,
                code=self._regional_set_code(language, set_external_id),
                tcgdex_id=None,
                name=set_payload.get("name") or set_external_id,
                release_date=release_date,
            )
            session.add(set_row)
            session.flush()
            stats.records_inserted += 1
        else:
            changed = False
            new_name = (set_payload.get("name") or "").strip()
            if new_name and set_row.name != new_name:
                set_row.name = new_name
                changed = True
            if release_date and set_row.release_date != release_date:
                set_row.release_date = release_date
                changed = True
            if set_row.tcgdex_id is not None:
                raise RuntimeError(
                    f"Japanese regional set unexpectedly owns global tcgdex_id: set_id={set_row.id}"
                )
            if changed:
                stats.records_updated += 1

        self._upsert_set_identifier(
            session,
            set_row=set_row,
            language=language,
            external_id=set_external_id,
            stats=stats,
        )

        card_row = self._card_by_source(
            session, language=language, external_id=card_external_id
        )
        regional_card_key = self._regional_card_key(language, card_external_id)
        if card_row is None:
            card_row = Card(
                game_id=game.id,
                name=card_name,
                card_key=regional_card_key,
                tcgdex_id=None,
            )
            session.add(card_row)
            session.flush()
            stats.records_inserted += 1
        else:
            changed = False
            if card_row.name != card_name:
                card_row.name = card_name
                changed = True
            if card_row.card_key != regional_card_key:
                card_row.card_key = regional_card_key
                changed = True
            if card_row.tcgdex_id is not None:
                raise RuntimeError(
                    f"Japanese regional card unexpectedly owns global tcgdex_id: card_id={card_row.id}"
                )
            if changed:
                stats.records_updated += 1

        self._upsert_card_identifier(
            session,
            card_row=card_row,
            language=language,
            external_id=card_external_id,
            stats=stats,
        )
        return set_row, card_row

    def _upsert_overlay(self, session, payload: dict, stats: IngestStats, *, language: str) -> dict:
        game = self._find_pokemon_game(session)
        if game is None:
            self.logger.warning(
                "ingest tcgdex localized skip reason=missing_canonical_game language=%s",
                language,
            )
            return {}

        set_payload = payload.get("set") or {}
        card_payload = payload.get("card") or {}
        set_row, card_row = self._find_entities_for_language(session, game.id, payload)
        if set_row is None or card_row is None:
            self.logger.warning(
                "ingest tcgdex localized skip reason=missing_exact_en_identity language=%s set_id=%s card_id=%s",
                language,
                set_payload.get("tcgdex_id"),
                card_payload.get("id"),
            )
            return {}

        collector_number = (card_payload.get("collector_number") or "").strip()
        external_id = (card_payload.get("id") or "").strip()
        set_external_id = (set_payload.get("tcgdex_id") or "").strip()
        if not collector_number or not external_id or not set_external_id:
            return {}

        self._upsert_set_identifier(
            session,
            set_row=set_row,
            language=language,
            external_id=set_external_id,
            stats=stats,
        )
        self._upsert_card_identifier(
            session,
            card_row=card_row,
            language=language,
            external_id=external_id,
            stats=stats,
        )

        print_row = self._find_print(
            session,
            set_row.id,
            card_row.id,
            collector_number,
            external_id,
            language=language,
            is_foil=False,
            variant="default",
        )
        if print_row is None:
            print_row = Print(
                card_id=card_row.id,
                set_id=set_row.id,
                collector_number=collector_number,
                language=language,
                rarity="unknown",
                is_foil=False,
                tcgdex_id=None,
                variant="default",
            )
            session.add(print_row)
            session.flush()
            stats.records_inserted += 1
        elif print_row.tcgdex_id is not None:
            raise RuntimeError(
                "Non-English overlay print unexpectedly owns global tcgdex_id: "
                f"print_id={print_row.id} language={language} tcgdex_id={print_row.tcgdex_id}"
            )

        self._upsert_language_identifier(
            session,
            print_row=print_row,
            language=language,
            external_id=external_id,
            stats=stats,
        )
        self._upsert_image(
            session,
            print_row=print_row,
            language=language,
            card_payload=card_payload,
            stats=stats,
        )
        self._upsert_localization(
            session,
            print_row=print_row,
            language=language,
            external_id=external_id,
            localization=payload.get("localization") or {},
            stats=stats,
        )
        upsert_field_provenance(
            session,
            "print",
            print_row.id,
            self._source_namespace(language),
            {
                "collector_number": print_row.collector_number,
                "language": language,
                "localized_card_name": (payload.get("localization") or {}).get("card_name"),
                "localized_set_name": (payload.get("localization") or {}).get("set_name"),
            },
        )
        return {"card_id": card_row.id, "set_id": set_row.id, "print_id": print_row.id}

    def _upsert_japanese(self, session, payload: dict, stats: IngestStats) -> dict:
        game = self._find_pokemon_game(session)
        if game is None:
            game = Game(slug="pokemon", name="Pokémon")
            session.add(game)
            session.flush()
            stats.records_inserted += 1

        set_row, card_row = self._create_or_update_ja_entities(
            session, game=game, payload=payload, stats=stats
        )
        if set_row is None or card_row is None:
            return {}

        card_payload = payload.get("card") or {}
        collector_number = (card_payload.get("collector_number") or "").strip()
        external_id = (card_payload.get("id") or "").strip()
        if not collector_number or not external_id:
            return {}

        print_row = self._find_print(
            session,
            set_row.id,
            card_row.id,
            collector_number,
            external_id,
            language="ja",
            is_foil=False,
            variant="default",
        )
        if print_row is None:
            print_row = Print(
                card_id=card_row.id,
                set_id=set_row.id,
                collector_number=collector_number,
                language="ja",
                rarity="unknown",
                is_foil=False,
                tcgdex_id=None,
                variant="default",
            )
            session.add(print_row)
            session.flush()
            stats.records_inserted += 1
        elif print_row.tcgdex_id is not None:
            raise RuntimeError(
                f"Japanese print unexpectedly owns global tcgdex_id: print_id={print_row.id}"
            )

        self._upsert_language_identifier(
            session,
            print_row=print_row,
            language="ja",
            external_id=external_id,
            stats=stats,
        )
        self._upsert_image(
            session,
            print_row=print_row,
            language="ja",
            card_payload=card_payload,
            stats=stats,
        )
        self._upsert_localization(
            session,
            print_row=print_row,
            language="ja",
            external_id=external_id,
            localization=payload.get("localization") or {},
            stats=stats,
        )
        upsert_field_provenance(
            session,
            "print",
            print_row.id,
            self._source_namespace("ja"),
            {
                "collector_number": print_row.collector_number,
                "language": "ja",
                "regional_identity": True,
                "localized_card_name": (payload.get("localization") or {}).get("card_name"),
                "localized_set_name": (payload.get("localization") or {}).get("set_name"),
            },
        )
        return {"card_id": card_row.id, "set_id": set_row.id, "print_id": print_row.id}

    def _upsert_english(self, session, payload: dict, stats: IngestStats, **kwargs) -> dict:
        super().upsert(session, payload, stats, **kwargs)

        game = session.execute(
            select(Game).where(Game.slug == "pokemon")
        ).scalar_one_or_none()
        if game is None:
            return {}
        set_payload = payload.get("set") or {}
        card_payload = payload.get("card") or {}
        set_row, card_row = self._find_entities_for_language(session, game.id, payload)
        collector_number = (card_payload.get("collector_number") or "").strip()
        external_id = (card_payload.get("id") or "").strip()
        set_external_id = (set_payload.get("tcgdex_id") or "").strip()
        if (
            set_row is None
            or card_row is None
            or not collector_number
            or not external_id
            or not set_external_id
        ):
            return {}

        print_row = self._find_print(
            session,
            set_row.id,
            card_row.id,
            collector_number,
            external_id,
            language="en",
            is_foil=False,
            variant="default",
        )
        if print_row is None:
            return {}

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
            external_id=external_id,
            stats=stats,
        )
        self._upsert_language_identifier(
            session,
            print_row=print_row,
            language="en",
            external_id=external_id,
            stats=stats,
        )
        self._upsert_localization(
            session,
            print_row=print_row,
            language="en",
            external_id=external_id,
            localization=payload.get("localization") or {},
            stats=stats,
        )
        return {"card_id": card_row.id, "set_id": set_row.id, "print_id": print_row.id}

    def upsert(self, session, payload: dict, stats: IngestStats, **kwargs) -> dict:
        language = self._assert_certified_language(
            payload.get("language") or kwargs.get("lang") or "en"
        )
        if language == "en":
            return self._upsert_english(session, payload, stats, **kwargs)
        if language == "es":
            return self._upsert_overlay(session, payload, stats, language="es")
        return self._upsert_japanese(session, payload, stats)
