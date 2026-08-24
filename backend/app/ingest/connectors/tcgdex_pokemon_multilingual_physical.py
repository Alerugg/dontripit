from __future__ import annotations

import time

import requests
from sqlalchemy import select

from app.ingest.connectors.tcgdex_pokemon_multilingual import (
    MultilingualTcgdexPokemonConnector,
)
from app.multilingual_models import CardIdentifier


class PhysicalMultilingualTcgdexPokemonConnector(MultilingualTcgdexPokemonConnector):
    """Multilingual Pokémon connector restricted to the physical TCG catalog.

    TCGdex exposes Pokémon TCG Pocket in language-level cards/sets endpoints for
    languages where the ``tcgp`` series is published. Pocket is a digital game
    and must never be materialized as a physical Don’tRipIt Print.

    The guard first discovers the language's available series. If ``tcgp`` is
    present, its set IDs are resolved and excluded before any set detail is
    fetched. If ``tcgp`` is not published for that language (currently true for
    JA), there is nothing to exclude and physical ingest can continue normally.

    Some language catalogs can also list a physical set whose detail endpoint is
    temporarily/unhistorically unavailable (observed live for JA ``SM1+``). If
    the language-global ``/cards`` catalog still exposes cards for that set, the
    writer recovers them. If the global catalog proves that the listed set has
    no cards at all, a full-catalog ingest treats it as stale empty index
    metadata and skips it with a warning rather than fabricating a physical Set.
    Explicit set requests remain fail-closed.
    """

    def _request_json(self, url: str, params: dict | None = None):
        """Retry only transient transport failures for the exact TCGdex request.

        The parent connector already retries bounded 429/5xx responses. Requests
        can also fail before an HTTP response exists (for example ReadTimeout or
        ConnectionError during TLS/read). Repeating the whole EN/ES/JA ingest for
        one dropped request is unnecessarily expensive, so retry that URL here.

        HTTP/data/identity errors are deliberately not caught and still fail
        closed through the existing parent/physical guards.
        """
        wait_seconds = 0.5
        for attempt in range(1, 4):
            try:
                return super()._request_json(url, params=params)
            except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as exc:
                if attempt == 3:
                    raise
                self.logger.warning(
                    "ingest tcgdex transport_retry url=%s error_type=%s attempt=%s wait_seconds=%s",
                    url,
                    type(exc).__name__,
                    attempt,
                    wait_seconds,
                )
                time.sleep(wait_seconds)
                wait_seconds *= 2
        raise RuntimeError(f"TCGdex transport retry loop exhausted unexpectedly: {url}")

    def _upsert_card_identifier(
        self,
        session,
        *,
        card_row,
        language: str,
        external_id: str,
        stats,
    ) -> None:
        """Store physical source aliases as many-to-one Card identifiers.

        A single canonical gameplay Card may be represented by several TCGdex
        IDs when the same card is physically reprinted. The collision boundary
        remains strict on ``(source, external_id)``: one external identity can
        never point at two different Cards.
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

    def _tcg_pocket_set_ids(self, *, lang: str) -> set[str]:
        language = self._assert_certified_language(lang)
        base_url = self.base_url_template.format(lang=language)

        series = self._request_json(f"{base_url}/series")
        if not isinstance(series, list):
            raise RuntimeError(
                f"TCG Pocket exclusion guard failed for {language}: "
                f"unexpected series list payload {type(series).__name__}"
            )

        available_series_ids = {
            str(item.get("id") or "").strip()
            for item in series
            if isinstance(item, dict) and str(item.get("id") or "").strip()
        }
        if "tcgp" not in available_series_ids:
            self.logger.info(
                "ingest tcgdex physical scope lang=%s tcgp_series_published=false",
                language,
            )
            return set()

        payload = self._request_json(f"{base_url}/series/tcgp")
        if not isinstance(payload, dict):
            raise RuntimeError(
                f"TCG Pocket exclusion guard failed for {language}: unexpected tcgp payload"
            )
        set_ids = {
            str(item.get("id") or "").strip()
            for item in (payload.get("sets") or [])
            if isinstance(item, dict) and str(item.get("id") or "").strip()
        }
        if not set_ids:
            raise RuntimeError(
                f"TCG Pocket exclusion guard failed for {language}: tcgp contains no sets"
            )
        return set_ids

    @staticmethod
    def _is_not_found(exc: requests.HTTPError) -> bool:
        response = getattr(exc, "response", None)
        return response is not None and response.status_code == 404

    def _fallback_cards_for_set(
        self,
        *,
        base_url: str,
        remote_set_id: str,
        set_brief: dict,
        language: str,
        global_cards_cache: list[dict] | None,
        allow_empty: bool = False,
    ) -> tuple[dict, list[dict], list[dict]]:
        cards = global_cards_cache
        if cards is None:
            payload = self._request_json(f"{base_url}/cards")
            if not isinstance(payload, list):
                raise RuntimeError(
                    f"Unexpected TCGdex cards payload for {language}: {type(payload).__name__}"
                )
            cards = [item for item in payload if isinstance(item, dict)]

        prefix = f"{remote_set_id}-"
        matching_cards = [
            item
            for item in cards
            if str(item.get("id") or "").strip().startswith(prefix)
        ]
        if not matching_cards:
            if not allow_empty:
                raise RuntimeError(
                    "TCGdex set is listed but detail endpoint is unavailable and no cards "
                    f"can be recovered from /cards: lang={language} set={remote_set_id}"
                )
            self.logger.warning(
                "ingest tcgdex physical skip reason=stale_empty_set_index lang=%s set=%s",
                language,
                remote_set_id,
            )

        recovered_set = {
            "id": remote_set_id,
            "abbreviation": set_brief.get("abbreviation"),
            "name": set_brief.get("name") or remote_set_id,
            "releaseDate": set_brief.get("releaseDate"),
        }
        if matching_cards:
            self.logger.warning(
                "ingest tcgdex physical fallback reason=set_detail_404 lang=%s set=%s recovered_cards=%s",
                language,
                remote_set_id,
                len(matching_cards),
            )
        return recovered_set, matching_cards, cards

    def _load_remote(
        self,
        limit: int | None = None,
        set_id: str | None = None,
        lang: str = "en",
    ) -> list[dict]:
        language = self._assert_certified_language(lang)
        pocket_set_ids = self._tcg_pocket_set_ids(lang=language)

        if set_id:
            normalized_set_id = str(set_id).strip()
            if normalized_set_id in pocket_set_ids:
                self.logger.warning(
                    "ingest tcgdex physical skip reason=tcg_pocket_set lang=%s set=%s",
                    language,
                    normalized_set_id,
                )
                return []
            try:
                return super()._load_remote(
                    limit=limit,
                    set_id=normalized_set_id,
                    lang=language,
                )
            except requests.HTTPError as exc:
                if not self._is_not_found(exc):
                    raise
                base_url = self.base_url_template.format(lang=language)
                sets = self._request_json(f"{base_url}/sets")
                if not isinstance(sets, list):
                    raise RuntimeError(
                        f"Unexpected TCGdex sets payload for {language}: {type(sets).__name__}"
                    ) from exc
                set_brief = next(
                    (
                        item
                        for item in sets
                        if isinstance(item, dict)
                        and str(item.get("id") or "").strip() == normalized_set_id
                    ),
                    None,
                )
                if set_brief is None:
                    raise
                recovered_set, cards, _cache = self._fallback_cards_for_set(
                    base_url=base_url,
                    remote_set_id=normalized_set_id,
                    set_brief=set_brief,
                    language=language,
                    global_cards_cache=None,
                    allow_empty=False,
                )
                if limit:
                    cards = cards[:limit]
                return [
                    self._build_card_payload(recovered_set, card, lang=language)
                    for card in cards
                ]

        if limit is not None and limit <= 0:
            return []

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
            "ingest tcgdex physical scope lang=%s total_sets=%s pocket_sets_excluded=%s physical_sets=%s",
            language,
            len(sets),
            omitted_sets,
            len(physical_sets),
        )

        out: list[dict] = []
        seen_card_ids: set[str] = set()
        visited_sets = 0
        global_cards_cache: list[dict] | None = None

        def _log_progress() -> None:
            # Keep the legacy telemetry contract used by operations/tests while
            # adding the physical-scope guard around the remote catalog.
            self.logger.info(
                "ingest tcgdex load_progress phase=remote sets_visited=%s cards_accumulated=%s limit=%s set_filter=%s",
                visited_sets,
                len(out),
                limit,
                set_id,
            )

        for item in physical_sets:
            remote_set_id = str(item.get("id") or "").strip()
            if not remote_set_id:
                continue
            try:
                set_payload = self._request_json(f"{base_url}/sets/{remote_set_id}")
                if not isinstance(set_payload, dict):
                    raise RuntimeError(
                        f"Unexpected TCGdex set payload for {language}/{remote_set_id}: "
                        f"{type(set_payload).__name__}"
                    )
                cards = [card for card in (set_payload.get("cards") or []) if isinstance(card, dict)]
            except requests.HTTPError as exc:
                if not self._is_not_found(exc):
                    raise
                set_payload, cards, global_cards_cache = self._fallback_cards_for_set(
                    base_url=base_url,
                    remote_set_id=remote_set_id,
                    set_brief=item,
                    language=language,
                    global_cards_cache=global_cards_cache,
                    allow_empty=True,
                )

            visited_sets += 1
            for card in cards:
                card_id = str(card.get("id") or "").strip()
                if not card_id or card_id in seen_card_ids:
                    continue
                seen_card_ids.add(card_id)
                out.append(self._build_card_payload(set_payload, card, lang=language))
                if len(out) == 1 or len(out) % 25 == 0:
                    _log_progress()
                if limit and len(out) >= limit:
                    _log_progress()
                    self.logger.info(
                        "ingest tcgdex physical load_done lang=%s sets_visited=%s cards=%s limit=%s",
                        language,
                        visited_sets,
                        len(out),
                        limit,
                    )
                    return out

        _log_progress()
        self.logger.info(
            "ingest tcgdex physical load_done lang=%s sets_visited=%s cards=%s limit=%s",
            language,
            visited_sets,
            len(out),
            limit,
        )
        return out