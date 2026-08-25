from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import requests

from app.ingest.connectors.tcgdex_pokemon_incremental_guard import (
    LegacyAwarePhysicalMultilingualTcgdexPokemonConnector,
)


class CertifiedRefreshPokemonTCGDexConnector(
    LegacyAwarePhysicalMultilingualTcgdexPokemonConnector
):
    """Production Pokémon writer with bounded parallel physical-set fetching.

    The certified writer must inspect the complete EN/ES/JA physical catalog so
    incremental checksum/identity guards can prove that nothing disappeared.
    Historically that inspection fetched every ``/sets/{id}`` endpoint strictly
    sequentially. A single language therefore required hundreds of network
    round-trips before the incremental writer could skip unchanged rows, making
    the production certification both slow and vulnerable to late transport
    failures.

    Full, unfiltered catalog scans now fetch set details with a deliberately
    small worker pool while consuming results in source-set order. Payloads,
    identity rules, TCG Pocket exclusion, 404 recovery, and fail-closed behavior
    are unchanged. Explicit set and limited probes retain the original serial
    path so diagnostics stay deterministic.
    """

    FULL_CATALOG_WORKERS = 4

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
