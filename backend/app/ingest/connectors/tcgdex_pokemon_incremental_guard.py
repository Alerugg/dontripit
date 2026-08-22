from __future__ import annotations

from app.ingest.connectors.tcgdex_pokemon_multilingual_physical import (
    PhysicalMultilingualTcgdexPokemonConnector,
)


class LegacyAwarePhysicalMultilingualTcgdexPokemonConnector(
    PhysicalMultilingualTcgdexPokemonConnector
):
    """Physical TCGdex writer with the certified EN legacy identity contract.

    English is Don’tRipIt’s canonical international Pokémon catalog and predates
    the language-qualified identifier/localization tables.  Existing EN rows are
    therefore complete when their canonical ``tcgdex_id`` identities still match
    the current TCGdex Set/Card/Print.  Requiring duplicate ``tcgdex:en`` aliases
    and an EN PrintLocalization would force a one-time replay of the entire
    ~21k-card catalog without improving physical identity or searchability.

    ES and JA remain strict multilingual overlays and continue to require the
    language-qualified identifiers/localization enforced by the parent class.
    New EN rows still flow through the normal writer and may receive the newer
    aliases; this guard only prevents legacy-complete rows from being replayed.
    """

    def _localized_state_complete(self, session, normalized: dict) -> bool:
        language = self._assert_certified_language(normalized.get("language") or "en")
        if language != "en":
            return super()._localized_state_complete(session, normalized)

        game = self._find_pokemon_game(session)
        if game is None:
            return False

        set_payload = normalized.get("set") or {}
        card_payload = normalized.get("card") or {}
        set_external_id = (set_payload.get("tcgdex_id") or "").strip()
        external_id = (card_payload.get("id") or "").strip()
        collector_number = (card_payload.get("collector_number") or "").strip()
        if not set_external_id or not external_id or not collector_number:
            return False

        set_row, card_row = self._find_entities_for_language(session, game.id, normalized)
        if set_row is None or card_row is None:
            return False
        if set_row.tcgdex_id != set_external_id or card_row.tcgdex_id != external_id:
            return False

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
        return print_row is not None and print_row.tcgdex_id == external_id
