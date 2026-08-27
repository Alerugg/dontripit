from __future__ import annotations

from sqlalchemy import select

from app.ingest.connectors.tcgdex_pokemon_certified_refresh import (
    CertifiedRefreshPokemonTCGDexConnector,
)
from app.models import Card, Print


class DuplicateSafeCertifiedRefreshPokemonTCGDexConnector(
    CertifiedRefreshPokemonTCGDexConnector
):
    """Certified Pokémon writer tolerant of legitimate duplicate card_key rows.

    Historical EN rows are keyed primarily by their exact TCGdex identity. After
    canonical gameplay keys were introduced, multiple legacy Card rows can share
    one ``card_key`` while still owning distinct non-null ``tcgdex_id`` values.
    Treating ``card_key`` as scalar therefore raises ``MultipleResultsFound``.

    Exact source identity remains authoritative. The canonical-key fallback is
    only used when it is unambiguous, or when exactly one matching legacy Card is
    still unclaimed and can be safely backfilled. Otherwise a new external
    identity must not overwrite an existing Card's TCGdex identity.
    """

    def _find_card(self, session, game_id: int, card_payload: dict) -> Card | None:
        tcgdex_card_id = str(card_payload.get("id") or "").strip()
        card_key = str(card_payload.get("card_key") or "").strip()

        if tcgdex_card_id:
            exact = session.execute(
                select(Card).where(
                    Card.game_id == game_id,
                    Card.tcgdex_id == tcgdex_card_id,
                )
            ).scalar_one_or_none()
            if exact is not None:
                return exact

            # A legacy Print can retain the exact source identity even when the
            # parent Card still needs its tcgdex_id backfilled.
            print_row = session.execute(
                select(Print).where(Print.tcgdex_id == tcgdex_card_id)
            ).scalar_one_or_none()
            if print_row is not None:
                print_card = session.get(Card, print_row.card_id)
                if print_card is not None and print_card.game_id == game_id:
                    return print_card

        if card_key:
            matches = session.execute(
                select(Card)
                .where(Card.game_id == game_id, Card.card_key == card_key)
                .order_by(Card.id)
            ).scalars().all()
            if len(matches) == 1:
                return matches[0]
            if len(matches) > 1:
                unclaimed = [
                    row for row in matches if not str(row.tcgdex_id or "").strip()
                ]
                if len(unclaimed) == 1:
                    self.logger.warning(
                        "ingest tcgdex duplicate_card_key reuse_unclaimed "
                        "card_key=%s card_id=%s candidates=%s external_id=%s",
                        card_key,
                        unclaimed[0].id,
                        len(matches),
                        tcgdex_card_id or "<missing>",
                    )
                    return unclaimed[0]
                if len(unclaimed) > 1:
                    raise RuntimeError(
                        "Ambiguous legacy Pokemon card_key: "
                        f"card_key={card_key} unclaimed_cards="
                        f"{[row.id for row in unclaimed]}"
                    )

                # Every matching canonical row already owns another exact source
                # identity. Returning one would overwrite that identity. Let the
                # normal writer materialize the new exact TCGdex Card instead.
                self.logger.info(
                    "ingest tcgdex duplicate_card_key new_external_identity "
                    "card_key=%s candidates=%s external_id=%s",
                    card_key,
                    len(matches),
                    tcgdex_card_id or "<missing>",
                )
                return None

        card_name = str(card_payload.get("name") or "").strip()
        if card_name and not card_key:
            matches = session.execute(
                select(Card).where(Card.game_id == game_id, Card.name == card_name)
            ).scalars().all()
            if len(matches) == 1:
                return matches[0]
        return None
