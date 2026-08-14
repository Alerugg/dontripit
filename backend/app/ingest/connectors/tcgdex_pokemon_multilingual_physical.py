from __future__ import annotations

from app.ingest.connectors.tcgdex_pokemon_multilingual import (
    MultilingualTcgdexPokemonConnector,
)


class PhysicalMultilingualTcgdexPokemonConnector(MultilingualTcgdexPokemonConnector):
    """Multilingual Pokémon connector restricted to the physical TCG catalog.

    TCGdex exposes Pokémon TCG Pocket in the same language-level cards/sets
    endpoints. TCG Pocket is a digital game and must never be materialized as a
    physical Don’tRipIt Print. TCGdex documents every Pocket set under the
    ``tcgp`` series, so this writer resolves that series first and fails closed
    if the exclusion set cannot be established.
    """

    def _tcg_pocket_set_ids(self, *, lang: str) -> set[str]:
        language = self._assert_certified_language(lang)
        base_url = self.base_url_template.format(lang=language)
        payload = self._request_json(f"{base_url}/series/tcgp")
        if not isinstance(payload, dict):
            raise RuntimeError(
                f"TCG Pocket exclusion guard failed for {language}: unexpected series payload"
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
                if limit and len(out) >= limit:
                    self.logger.info(
                        "ingest tcgdex physical load_done lang=%s sets_visited=%s cards=%s limit=%s",
                        language,
                        visited_sets,
                        len(out),
                        limit,
                    )
                    return out

        self.logger.info(
            "ingest tcgdex physical load_done lang=%s sets_visited=%s cards=%s limit=%s",
            language,
            visited_sets,
            len(out),
            limit,
        )
        return out
