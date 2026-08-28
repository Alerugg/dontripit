from __future__ import annotations

from sqlalchemy import select

from app.ingest.connectors.tcgdex_pokemon_duplicate_safe import (
    DuplicateSafeCertifiedRefreshPokemonTCGDexConnector,
)
from app.models import Card, Print
from app.multilingual_models import CardIdentifier


class ExactIdentityRehomeCertifiedPokemonTCGDexConnector(
    DuplicateSafeCertifiedRefreshPokemonTCGDexConnector
):
    """Repair stale Card/Print ownership only with exact TCGdex evidence.

    Historical production rows can retain an exact language-qualified
    CardIdentifier on a legacy Card while the current canonical writer has
    already materialized the distinct Card that owns that exact TCGdex id. The
    exact Print can be either still on the same legacy Card or already rehomed
    to the exact target Card.

    This layer permits only that narrow repair for certified EN/ES/JA TCGdex
    namespaces. Any duplicate Print identity, third-party Print owner, language
    mismatch, or non-exact target identity still fails closed.
    """

    _EXACT_REHOME_SOURCES = frozenset({"tcgdex:en", "tcgdex:es", "tcgdex:ja"})

    @classmethod
    def _is_approved_exact_card_identifier_rehome(
        cls,
        *,
        source: str,
        external_id: str,
        existing_card: Card | None,
        target_card: Card,
    ) -> bool:
        if source not in cls._EXACT_REHOME_SOURCES or existing_card is None:
            return False
        incoming = str(external_id or "").strip()
        target_exact = str(target_card.tcgdex_id or "").strip()
        existing_exact = str(existing_card.tcgdex_id or "").strip()
        return bool(
            incoming
            and target_exact == incoming
            and existing_card.game_id == target_card.game_id
            and existing_card.id != target_card.id
            and existing_exact
            and existing_exact != incoming
        )

    @classmethod
    def _exact_print_owner_allows_rehome(
        cls,
        *,
        source: str,
        external_id: str,
        existing_card: Card | None,
        target_card: Card,
        exact_print: Print | None,
    ) -> bool:
        if exact_print is None:
            return False
        if not cls._is_approved_exact_card_identifier_rehome(
            source=source,
            external_id=external_id,
            existing_card=existing_card,
            target_card=target_card,
        ):
            return False
        incoming = str(external_id or "").strip()
        return bool(
            str(exact_print.tcgdex_id or "").strip() == incoming
            and existing_card is not None
            and exact_print.card_id in {existing_card.id, target_card.id}
        )

    def _upsert_card_identifier(
        self,
        session,
        *,
        card_row,
        language: str,
        external_id: str,
        stats,
    ) -> None:
        try:
            return super()._upsert_card_identifier(
                session,
                card_row=card_row,
                language=language,
                external_id=external_id,
                stats=stats,
            )
        except RuntimeError as exc:
            source = self._source_namespace(language)
            existing = session.execute(
                select(CardIdentifier).where(
                    CardIdentifier.source == source,
                    CardIdentifier.external_id == external_id,
                )
            ).scalar_one_or_none()
            existing_card = (
                session.get(Card, existing.card_id) if existing is not None else None
            )
            if existing is None or existing.card_id == card_row.id:
                raise

            exact_prints = session.execute(
                select(Print)
                .where(Print.tcgdex_id == external_id)
                .order_by(Print.id)
                .limit(2)
            ).scalars().all()
            if len(exact_prints) != 1:
                raise RuntimeError(
                    "TCGdex stale exact identity rehome requires one exact Print: "
                    f"source={source} external_id={external_id} "
                    f"exact_print_count={len(exact_prints)}"
                ) from exc

            exact_print = exact_prints[0]
            if not self._exact_print_owner_allows_rehome(
                source=source,
                external_id=external_id,
                existing_card=existing_card,
                target_card=card_row,
                exact_print=exact_print,
            ):
                raise RuntimeError(
                    "TCGdex stale exact identity rehome has inconsistent Print owner: "
                    f"source={source} external_id={external_id} "
                    f"existing_card_id={existing.card_id} "
                    f"target_card_id={card_row.id} print_id={exact_print.id} "
                    f"print_card_id={exact_print.card_id}"
                ) from exc

            old_card_id = existing.card_id
            print_rehomed = exact_print.card_id != card_row.id
            if print_rehomed:
                exact_print.card_id = card_row.id
                stats.records_updated += 1

            existing.card_id = card_row.id
            stats.records_updated += 1
            self.logger.warning(
                "ingest tcgdex rehome_stale_exact_identity "
                "source=%s external_id=%s old_card_id=%s target_card_id=%s "
                "print_id=%s print_rehomed=%s",
                source,
                external_id,
                old_card_id,
                card_row.id,
                exact_print.id,
                print_rehomed,
            )
            return None
