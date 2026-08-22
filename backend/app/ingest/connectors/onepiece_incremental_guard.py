from __future__ import annotations

from sqlalchemy import select

from app.ingest.connectors.onepiece_canonical import OnePieceCanonicalConnector
from app.ingest.normalization import normalize_collector_number, normalize_variant
from app.models import Card, Game, Print, Set


class SelfHealingOnePieceCanonicalConnector(OnePieceCanonicalConnector):
    """Canonical One Piece writer with checksum-safe drift repair.

    Incremental ingestion normally skips a source payload whose checksum was
    already persisted. That is safe only while every canonical entity guaranteed
    by that payload still exists. A later accidental deletion or partial repair
    can otherwise leave production permanently behind an unchanged official
    Bandai source.

    Before accepting a checksum skip, prove that the stored source payload still
    has complete Card, Set and exact physical Print coverage in the database. If
    any expected identity is missing, return ``False`` so the normal writer
    replays the current payload and repairs the drift transactionally.
    """

    name = "onepiece"

    @staticmethod
    def _expected_physical_inventory(payload: dict) -> tuple[set[str], set[str], set[tuple[str, str, str, str, str]]]:
        language = OnePieceCanonicalConnector._normalize_language(str(payload.get("language") or "en"))
        cards: set[str] = set()
        sets: set[str] = set()
        prints: set[tuple[str, str, str, str, str]] = set()

        for set_row in payload.get("sets") or []:
            code = str(set_row.get("code") or "").strip().lower()
            if code:
                sets.add(code)

        for card in payload.get("cards") or []:
            card_key = str(card.get("id") or "").strip().lower()
            if card_key:
                cards.add(card_key)
            for print_row in card.get("prints") or []:
                set_code = str(print_row.get("set_code") or "").strip().lower()
                collector = normalize_collector_number(print_row.get("collector_number"))
                variant = normalize_variant(print_row.get("variant"))
                if card_key and set_code and collector:
                    prints.add((card_key, set_code, collector, language, variant))

        return cards, sets, prints

    def should_skip_existing_record(self, existing_record, **kwargs) -> bool:
        session = kwargs.get("session")
        if session is None:
            return False

        payload = getattr(existing_record, "raw_json", None)
        if not isinstance(payload, dict) or payload.get("_payload_omitted"):
            self.logger.warning(
                "ingest onepiece checksum_guard replay reason=missing_source_payload"
            )
            return False

        if str(payload.get("source") or "").strip() != "onepiece_official_v2":
            return super().should_skip_existing_record(existing_record, **kwargs)

        expected_cards, expected_sets, expected_prints = self._expected_physical_inventory(payload)
        if not expected_cards and not expected_sets and not expected_prints:
            self.logger.warning(
                "ingest onepiece checksum_guard replay reason=empty_expected_inventory region=%s",
                payload.get("region"),
            )
            return False

        game = session.execute(select(Game).where(Game.slug == "onepiece")).scalar_one_or_none()
        if game is None:
            self.logger.warning(
                "ingest onepiece checksum_guard replay reason=missing_game region=%s",
                payload.get("region"),
            )
            return False

        actual_cards = {
            str(value).strip().lower()
            for value in session.execute(
                select(Card.card_key).where(Card.game_id == game.id, Card.card_key.is_not(None))
            ).scalars()
        }
        actual_sets = {
            str(value).strip().lower()
            for value in session.execute(
                select(Set.code).where(Set.game_id == game.id)
            ).scalars()
        }
        actual_prints = {
            (
                str(card_key or "").strip().lower(),
                str(set_code or "").strip().lower(),
                normalize_collector_number(collector),
                str(language or "").strip().lower(),
                normalize_variant(variant),
            )
            for card_key, set_code, collector, language, variant in session.execute(
                select(Card.card_key, Set.code, Print.collector_number, Print.language, Print.variant)
                .join(Print, Print.card_id == Card.id)
                .join(Set, Set.id == Print.set_id)
                .where(Card.game_id == game.id)
            ).all()
        }

        missing_cards = expected_cards - actual_cards
        missing_sets = expected_sets - actual_sets
        missing_prints = expected_prints - actual_prints
        if missing_cards or missing_sets or missing_prints:
            self.logger.warning(
                "ingest onepiece checksum_guard replay region=%s missing_cards=%s missing_sets=%s missing_prints=%s print_examples=%s",
                payload.get("region"),
                len(missing_cards),
                len(missing_sets),
                len(missing_prints),
                sorted(missing_prints)[:10],
            )
            return False

        self.logger.info(
            "ingest onepiece checksum_guard skip_ok region=%s cards=%s sets=%s prints=%s",
            payload.get("region"),
            len(expected_cards),
            len(expected_sets),
            len(expected_prints),
        )
        return True
