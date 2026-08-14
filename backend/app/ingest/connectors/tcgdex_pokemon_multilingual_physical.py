from __future__ import annotations

from app.ingest.connectors.tcgdex_pokemon_multilingual import (
    MultilingualTcgdexPokemonConnector,
)


class PhysicalMultilingualTcgdexPokemonConnector(MultilingualTcgdexPokemonConnector):
    """Multilingual Pokémon connector restricted to the physical TCG catalog.

    TCGdex exposes Pokémon TCG Pocket in language-level cards/sets endpoints for
    languages where the ``tcgp`` series is published. Pocket is a digital game
    and must never be materialized as a physical Don’tRipIt Print.

    The guard first discovers the language's available series. If ``tcgp`` is
    present, its set IDs are resolved and excluded before any set detail is
    fetched. If ``tcgp`` is not published for that language (currently true for
    JA), there is nothing to exclude and physical ingest can continue normally.
    """

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
            return super()._load_remote(limit=limit, set_id=normalized_set_id, lang=language)

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
            set_payload = self._request_json(f"{base_url}/sets/{remote_set_id}")
            if not isinstance(set_payload, dict):
                raise RuntimeError(
                    f"Unexpected TCGdex set payload for {language}/{remote_set_id}: "
                    f"{type(set_payload).__name__}"
                )
            visited_sets += 1
            for card in set_payload.get("cards") or []:
                if not isinstance(card, dict):
                    continue
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
