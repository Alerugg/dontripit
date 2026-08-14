from __future__ import annotations

from sqlalchemy import select

from app.ingest.base import IngestStats
from app.ingest.connectors.tcgdex_pokemon import TcgdexPokemonConnector
from app.ingest.normalization import normalize_language
from app.ingest.provenance import upsert_field_provenance
from app.models import Game, Print, PrintIdentifier, PrintImage
from app.multilingual_models import PrintLocalization


class MultilingualTcgdexPokemonConnector(TcgdexPokemonConnector):
    """Language-safe TCGdex writer.

    TCGdex card IDs are stable across localizations, so non-English records must
    never reuse the globally unique ``Print.tcgdex_id``. Canonical Card/Set rows
    remain the existing English identity; localized names/content live on the
    language-specific physical Print.
    """

    @staticmethod
    def _source_namespace(language: str) -> str:
        return f"tcgdex:{normalize_language(language)}"

    def _build_card_payload(self, set_payload: dict, card_payload: dict, *, lang: str = "en") -> dict:
        payload = super()._build_card_payload(set_payload, card_payload, lang=lang)
        payload["_language"] = normalize_language(lang)
        return payload

    def load(self, path=None, **kwargs):
        language = normalize_language(kwargs.get("lang", "en"))
        rows = super().load(path, **kwargs)
        enriched = []
        for file_path, payload, _checksum in rows:
            localized_payload = dict(payload)
            localized_payload["_language"] = normalize_language(
                localized_payload.get("_language") or language
            )
            enriched.append((file_path, localized_payload, self.checksum(localized_payload)))
        return enriched

    def normalize(self, payload: dict, **kwargs) -> dict:
        normalized = super().normalize(payload, **kwargs)
        language = normalize_language(payload.get("_language") or kwargs.get("lang") or "en")
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
        language = normalize_language(language)
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

            # Legacy compatibility exists only for English. Non-English must not
            # resolve the globally unique TCGdex ID to the English physical print.
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
                "TCGdex localized identifier collision: "
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
            identifier.external_id = external_id
            stats.records_updated += 1

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

    def _localized_state_complete(self, session, normalized: dict) -> bool:
        language = normalize_language(normalized.get("language") or "en")
        game = self._find_pokemon_game(session)
        if game is None:
            return False

        set_payload = normalized.get("set") or {}
        card_payload = normalized.get("card") or {}
        set_row = self._find_set(session, game.id, set_payload)
        card_row = self._find_card(session, game.id, card_payload)
        collector_number = (card_payload.get("collector_number") or "").strip()
        external_id = (card_payload.get("id") or "").strip()
        if set_row is None or card_row is None or not collector_number or not external_id:
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

        source = self._source_namespace(language)
        identifier = session.execute(
            select(PrintIdentifier).where(
                PrintIdentifier.print_id == print_row.id,
                PrintIdentifier.source == source,
                PrintIdentifier.external_id == external_id,
            )
        ).scalar_one_or_none()
        localization = session.execute(
            select(PrintLocalization).where(
                PrintLocalization.print_id == print_row.id,
                PrintLocalization.language == language,
                PrintLocalization.source == "tcgdex",
            )
        ).scalar_one_or_none()
        return identifier is not None and localization is not None

    def should_skip_existing_record(self, existing_record, **kwargs) -> bool:
        session = kwargs.get("session")
        if session is None:
            return False
        normalized = self.normalize(existing_record.raw_json or {}, **kwargs)
        return self._localized_state_complete(session, normalized)

    def _upsert_non_english(self, session, payload: dict, stats: IngestStats) -> dict:
        language = normalize_language(payload.get("language") or "en")
        game = self._find_pokemon_game(session)
        if game is None:
            self.logger.warning(
                "ingest tcgdex localized skip reason=missing_canonical_game language=%s",
                language,
            )
            return {}

        set_payload = payload.get("set") or {}
        card_payload = payload.get("card") or {}
        set_row = self._find_set(session, game.id, set_payload)
        card_row = self._find_card(session, game.id, card_payload)
        if set_row is None or card_row is None:
            self.logger.warning(
                "ingest tcgdex localized skip reason=missing_canonical_identity language=%s set_id=%s card_id=%s",
                language,
                set_payload.get("tcgdex_id"),
                card_payload.get("id"),
            )
            return {}

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
        else:
            changed = False
            if print_row.card_id != card_row.id:
                print_row.card_id = card_row.id
                changed = True
            if print_row.set_id != set_row.id:
                print_row.set_id = set_row.id
                changed = True
            # Explicit invariant: localized TCGdex IDs never occupy the globally
            # unique legacy column shared with the English physical print.
            if print_row.tcgdex_id is not None:
                raise RuntimeError(
                    "Non-English TCGdex print unexpectedly owns global tcgdex_id: "
                    f"print_id={print_row.id} language={language} tcgdex_id={print_row.tcgdex_id}"
                )
            if changed:
                stats.records_updated += 1

        self._upsert_language_identifier(
            session,
            print_row=print_row,
            language=language,
            external_id=external_id,
            stats=stats,
        )

        image_url = self._primary_image_url_from_base(card_payload.get("image"))
        if image_url:
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
        return {
            "card_id": card_row.id,
            "set_id": set_row.id,
            "print_id": print_row.id,
        }

    def upsert(self, session, payload: dict, stats: IngestStats, **kwargs) -> dict:
        language = normalize_language(payload.get("language") or kwargs.get("lang") or "en")
        if language != "en":
            return self._upsert_non_english(session, payload, stats)

        # English retains the certified legacy behavior and IDs, then receives
        # the new namespaced identifier/localization additively.
        super().upsert(session, payload, stats, **kwargs)

        game = session.execute(select(Game).where(Game.slug == "pokemon")).scalar_one_or_none()
        if game is None:
            return {}
        set_payload = payload.get("set") or {}
        card_payload = payload.get("card") or {}
        set_row = self._find_set(session, game.id, set_payload)
        card_row = self._find_card(session, game.id, card_payload)
        collector_number = (card_payload.get("collector_number") or "").strip()
        external_id = (card_payload.get("id") or "").strip()
        if set_row is None or card_row is None or not collector_number or not external_id:
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
        return {
            "card_id": card_row.id,
            "set_id": set_row.id,
            "print_id": print_row.id,
        }
