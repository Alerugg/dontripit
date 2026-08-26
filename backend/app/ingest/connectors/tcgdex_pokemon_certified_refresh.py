from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import requests
from sqlalchemy import select

from app.ingest.connectors.tcgdex_pokemon_incremental_guard import (
    LegacyAwarePhysicalMultilingualTcgdexPokemonConnector,
)
from app.models import Card, Game, Print, PrintIdentifier, Set as CardSet
from app.multilingual_models import CardIdentifier, PrintLocalization, SetIdentifier


class CertifiedRefreshPokemonTCGDexConnector(
    LegacyAwarePhysicalMultilingualTcgdexPokemonConnector
):
    """Production Pokémon writer with bounded network and DB catalog scans.

    The certified writer must inspect the complete EN/ES/JA physical catalog so
    incremental checksum/identity guards can prove that nothing disappeared.
    Historically that inspection fetched every ``/sets/{id}`` endpoint strictly
    sequentially and then re-ran several identity queries for every unchanged
    card. Both costs made a no-op certification unnecessarily slow and exposed
    the long-running job to late transport/database failures.

    Full, unfiltered catalog scans fetch set details with a deliberately small
    worker pool while consuming results in source-set order. Incremental
    completeness is also materialized once per DB session/language as an exact
    set of identities, preserving the EN legacy contract and the stricter ES/JA
    identifier + localization contract. Missing or inconsistent rows are never
    considered complete and therefore continue through the normal self-healing
    writer. Explicit set and limited probes retain the established serial path.
    """

    FULL_CATALOG_WORKERS = 4

    def _certified_complete_identity_keys(self, session, language: str) -> set[tuple[str, str, str]]:
        """Return exact identities that satisfy the existing skip contract.

        This is deliberately a read-only acceleration. It converts the historical
        per-card Set/Card/Print/identifier/localization lookups into one joined
        query per language. Any identity absent from the materialized set is
        treated as incomplete and follows the normal fail-closed upsert path.
        """
        language = self._assert_certified_language(language)
        cache_session = getattr(self, "_complete_identity_cache_session", None)
        if cache_session is not session:
            self._complete_identity_cache_session = session
            self._complete_identity_cache: dict[str, set[tuple[str, str, str]]] = {}

        cached = self._complete_identity_cache.get(language)
        if cached is not None:
            return cached

        game_id = session.execute(
            select(Game.id).where(Game.slug == "pokemon")
        ).scalar_one_or_none()
        if game_id is None:
            complete: set[tuple[str, str, str]] = set()
            self._complete_identity_cache[language] = complete
            return complete

        if language == "en":
            # Exact equivalent of LegacyAwarePhysicalMultilingualTcgdexPokemonConnector:
            # canonical Set/Card TCGdex IDs plus an EN default non-foil Print whose
            # TCGdex ID is that exact card identity. Language-qualified aliases and
            # localizations are intentionally not required for legacy-complete EN.
            rows = session.execute(
                select(
                    CardSet.tcgdex_id,
                    Card.tcgdex_id,
                    Print.collector_number,
                )
                .select_from(Print)
                .join(CardSet, CardSet.id == Print.set_id)
                .join(Card, Card.id == Print.card_id)
                .where(
                    CardSet.game_id == game_id,
                    Card.game_id == game_id,
                    CardSet.tcgdex_id.is_not(None),
                    Card.tcgdex_id.is_not(None),
                    Print.tcgdex_id.is_not(None),
                    Print.tcgdex_id == Card.tcgdex_id,
                    Print.language == "en",
                    Print.is_foil.is_(False),
                    Print.variant == "default",
                )
            ).all()
        else:
            source = self._source_namespace(language)
            statement = (
                select(
                    SetIdentifier.external_id,
                    CardIdentifier.external_id,
                    Print.collector_number,
                )
                .select_from(Print)
                .join(CardSet, CardSet.id == Print.set_id)
                .join(Card, Card.id == Print.card_id)
                .join(SetIdentifier, SetIdentifier.set_id == CardSet.id)
                .join(CardIdentifier, CardIdentifier.card_id == Card.id)
                .join(PrintIdentifier, PrintIdentifier.print_id == Print.id)
                .join(PrintLocalization, PrintLocalization.print_id == Print.id)
                .where(
                    CardSet.game_id == game_id,
                    Card.game_id == game_id,
                    SetIdentifier.source == source,
                    CardIdentifier.source == source,
                    PrintIdentifier.source == source,
                    PrintIdentifier.external_id == CardIdentifier.external_id,
                    PrintLocalization.language == language,
                    PrintLocalization.source == "tcgdex",
                    Print.language == language,
                    Print.is_foil.is_(False),
                    Print.variant == "default",
                    Print.tcgdex_id.is_(None),
                )
            )
            if language == "es":
                # ES overlays the exact shared international EN Set/Card identity.
                statement = statement.where(
                    CardSet.tcgdex_id == SetIdentifier.external_id,
                    Card.tcgdex_id == CardIdentifier.external_id,
                )
            rows = session.execute(statement).all()

        complete = {
            (str(set_external_id), str(card_external_id), str(collector_number))
            for set_external_id, card_external_id, collector_number in rows
            if set_external_id and card_external_id and collector_number
        }
        self._complete_identity_cache[language] = complete
        self.logger.info(
            "ingest tcgdex certified_complete_cache lang=%s identities=%s",
            language,
            len(complete),
        )
        return complete

    def _localized_state_complete(self, session, normalized: dict) -> bool:
        language = self._assert_certified_language(normalized.get("language") or "en")
        set_payload = normalized.get("set") or {}
        card_payload = normalized.get("card") or {}
        set_external_id = str(set_payload.get("tcgdex_id") or "").strip()
        card_external_id = str(card_payload.get("id") or "").strip()
        collector_number = str(card_payload.get("collector_number") or "").strip()
        if not set_external_id or not card_external_id or not collector_number:
            return False
        return (
            set_external_id,
            card_external_id,
            collector_number,
        ) in self._certified_complete_identity_keys(session, language)

    def upsert(self, session, payload: dict, stats, **kwargs) -> dict:
        """Avoid rewriting physical rows that already satisfy the certified state.

        A source checksum can legitimately be new after connector-normalization
        changes even when the physical Set/Card/Print identity in production is
        already complete. ``SourceConnector.run`` has already staged the new
        SourceRecord before calling this method. Re-running the multilingual
        writer in that case is unnecessary and, historically, caused tens of
        thousands of alias/localization writes during recertification.

        This guard uses the exact same fail-closed completeness contract as the
        incremental skip path. Anything incomplete still falls through to the
        normal self-healing writer.
        """
        if self._localized_state_complete(session, payload):
            return {}
        return super().upsert(session, payload, stats, **kwargs)

    def _upsert_set_identifier(
        self,
        session,
        *,
        set_row: CardSet,
        language: str,
        external_id: str,
        stats,
    ) -> None:
        """Create a language-qualified set alias once per session.

        Production sessions run with autoflush disabled. During a backfill, the
        first card in a set can therefore stage a SetIdentifier that subsequent
        cards cannot see through SQL until a later flush. The parent helper then
        stages the same unique ``(source, external_id)`` row repeatedly and the
        eventual flush fails with ``uq_set_identifier_source_external``.

        Keep a session-local identity map so pending aliases participate in the
        same collision checks as persisted aliases without adding per-card
        flushes or weakening uniqueness.
        """
        source = self._source_namespace(language)
        cache_session = getattr(self, "_set_identifier_cache_session", None)
        if cache_session is not session:
            self._set_identifier_cache_session = session
            self._set_identifier_by_external: dict[tuple[str, str], SetIdentifier] = {}
            self._set_identifier_by_entity: dict[tuple[int, str], SetIdentifier] = {}

        external_key = (source, external_id)
        entity_key = (int(set_row.id), source)
        cached_external = self._set_identifier_by_external.get(external_key)
        if cached_external is not None:
            if cached_external.set_id != set_row.id:
                raise RuntimeError(
                    "TCGdex set identifier collision: "
                    f"source={source} external_id={external_id} "
                    f"existing_set_id={cached_external.set_id} target_set_id={set_row.id}"
                )
            self._set_identifier_by_entity[entity_key] = cached_external
            return

        cached_entity = self._set_identifier_by_entity.get(entity_key)
        if cached_entity is not None:
            if cached_entity.external_id != external_id:
                raise RuntimeError(
                    "TCGdex set source identity changed unexpectedly: "
                    f"set_id={set_row.id} source={source} "
                    f"old={cached_entity.external_id} new={external_id}"
                )
            self._set_identifier_by_external[external_key] = cached_entity
            return

        by_external = session.execute(
            select(SetIdentifier).where(
                SetIdentifier.source == source,
                SetIdentifier.external_id == external_id,
            )
        ).scalar_one_or_none()
        if by_external is not None:
            if by_external.set_id != set_row.id:
                raise RuntimeError(
                    "TCGdex set identifier collision: "
                    f"source={source} external_id={external_id} "
                    f"existing_set_id={by_external.set_id} target_set_id={set_row.id}"
                )
            self._set_identifier_by_external[external_key] = by_external
            self._set_identifier_by_entity[entity_key] = by_external
            return

        by_entity = session.execute(
            select(SetIdentifier).where(
                SetIdentifier.set_id == set_row.id,
                SetIdentifier.source == source,
            )
        ).scalar_one_or_none()
        if by_entity is not None:
            if by_entity.external_id != external_id:
                raise RuntimeError(
                    "TCGdex set source identity changed unexpectedly: "
                    f"set_id={set_row.id} source={source} "
                    f"old={by_entity.external_id} new={external_id}"
                )
            identifier = by_entity
        else:
            identifier = SetIdentifier(
                set_id=set_row.id,
                source=source,
                external_id=external_id,
            )
            session.add(identifier)
            stats.records_inserted += 1

        self._set_identifier_by_external[external_key] = identifier
        self._set_identifier_by_entity[entity_key] = identifier

    def _load_remote(
        self,
        limit: int | None = None,
        set_id: str | None = None,
        lang: str = "en",
    ) -> list[dict]:
        # Keep targeted/limited operations byte-for-byte on the established path.
        if set_id is not None or limit is not None:
            return super()._load_remote(limit=limit, set_id=set_id, lang=lang)

        language = self._assert_certified_language(lang)
        pocket_set_ids = self._tcg_pocket_set_ids(lang=language)
        base_url = self.base_url_template.format(lang=language)

        sets = self._request_json(f"{base_url}/sets")
        if not isinstance(sets, list):
            raise RuntimeError(
                f"Unexpected TCGdex sets payload for {language}: {type(sets).__name__}"
            )

        physical_sets = [
            item
            for item in sets
            if isinstance(item, dict)
            and str(item.get("id") or "").strip()
            and str(item.get("id") or "").strip() not in pocket_set_ids
        ]
        omitted_sets = len(sets) - len(physical_sets)
        self.logger.info(
            "ingest tcgdex certified_refresh lang=%s total_sets=%s "
            "pocket_sets_excluded=%s physical_sets=%s workers=%s",
            language,
            len(sets),
            omitted_sets,
            len(physical_sets),
            self.FULL_CATALOG_WORKERS,
        )

        def _fetch_set(item: dict):
            remote_set_id = str(item.get("id") or "").strip()
            try:
                set_payload = self._request_json(f"{base_url}/sets/{remote_set_id}")
                if not isinstance(set_payload, dict):
                    raise RuntimeError(
                        f"Unexpected TCGdex set payload for {language}/{remote_set_id}: "
                        f"{type(set_payload).__name__}"
                    )
                cards = [
                    card
                    for card in (set_payload.get("cards") or [])
                    if isinstance(card, dict)
                ]
                return set_payload, cards, False
            except requests.HTTPError as exc:
                if not self._is_not_found(exc):
                    raise
                # Recovery remains sequential below so one shared /cards cache is
                # enough even if several historical set detail endpoints are 404.
                return None, None, True

        futures = []
        with ThreadPoolExecutor(
            max_workers=self.FULL_CATALOG_WORKERS,
            thread_name_prefix="tcgdex-physical",
        ) as executor:
            for item in physical_sets:
                futures.append((item, executor.submit(_fetch_set, item)))

            out: list[dict] = []
            seen_card_ids: set[str] = set()
            visited_sets = 0
            global_cards_cache: list[dict] | None = None

            def _log_progress() -> None:
                self.logger.info(
                    "ingest tcgdex load_progress phase=remote sets_visited=%s "
                    "cards_accumulated=%s limit=%s set_filter=%s",
                    visited_sets,
                    len(out),
                    None,
                    None,
                )

            for item, future in futures:
                remote_set_id = str(item.get("id") or "").strip()
                set_payload, cards, needs_fallback = future.result()
                if needs_fallback:
                    set_payload, cards, global_cards_cache = self._fallback_cards_for_set(
                        base_url=base_url,
                        remote_set_id=remote_set_id,
                        set_brief=item,
                        language=language,
                        global_cards_cache=global_cards_cache,
                        allow_empty=True,
                    )

                visited_sets += 1
                for card in cards or []:
                    card_id = str(card.get("id") or "").strip()
                    if not card_id or card_id in seen_card_ids:
                        continue
                    seen_card_ids.add(card_id)
                    out.append(self._build_card_payload(set_payload, card, lang=language))
                    if len(out) == 1 or len(out) % 25 == 0:
                        _log_progress()

        _log_progress()
        self.logger.info(
            "ingest tcgdex physical load_done lang=%s sets_visited=%s cards=%s limit=%s",
            language,
            visited_sets,
            len(out),
            None,
        )
        return out
