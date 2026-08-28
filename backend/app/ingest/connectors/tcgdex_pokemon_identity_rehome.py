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
    """Repair stale EN Card/Print ownership only with exact TCGdex evidence.

    Historical production rows can retain an exact EN CardIdentifier and exact
    Print TCGdex id on a legacy Card while the current canonical writer has just
    materialized the distinct Card that owns that exact TCGdex id. This layer
    permits one narrow repair: the exact Print and the exact CardIdentifier may
    move together from that same legacy Card to the exact target Card.

    Any duplicate Print identity, third-party Print owner, language mismatch, or
    non-exact target identity still fails closed.
    """

    @staticmethod
    def _exact_print_owner_allows_rehome(
        *,
        source: str,
        external_id: str,
        existing_card: Card | None,
        target_card: Card,
        exact_print: Print | None,
    ) -> bool:
        if exact_print is None:
            return False
        if not ExactIdentityRehomeCertifiedPokemonTCGDexConnector._is_approved_legacy_en_card_identifier_rehome(
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
                    "TCGdex stale EN identity rehome requires one exact Print: "
                    f"external_id={external_id} exact_print_count={len(exact_prints)}"
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
                    "TCGdex stale EN identity rehome has inconsistent Print owner: "
                    f"external_id={external_id} existing_card_id={existing.card_id} "
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
                "ingest tcgdex rehome_stale_en_exact_identity "
                "external_id=%s old_card_id=%s target_card_id=%s print_id=%s "
                "print_rehomed=%s",
                external_id,
                old_card_id,
                card_row.id,
                exact_print.id,
                print_rehomed,
            )
            return None
