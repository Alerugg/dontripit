from __future__ import annotations

from sqlalchemy import select

from app.ingest.connectors.onepiece_canonical import OnePieceCanonicalConnector
from app.ingest.normalization import normalize_collector_number, normalize_variant
from app.models import Card, Game, Print, PrintImage, Set


class SelfHealingOnePieceCanonicalConnector(OnePieceCanonicalConnector):
    """Canonical One Piece writer with checksum-safe drift repair and delta replay.

    Incremental ingestion normally skips a source payload whose checksum was
    already persisted. That is safe only while every canonical entity guaranteed
    by that payload still exists. A later accidental deletion or partial repair
    can otherwise leave production permanently behind an unchanged official
    Bandai source.

    Before accepting a checksum skip, prove that the stored source payload still
    has complete Card, Set and exact physical Print coverage in the database. If
    any expected identity is missing, return ``False`` so the writer replays the
    current payload transactionally.

    A changed official checksum does not imply that every row changed. Bandai's
    regional payloads contain thousands of physical prints, while most refreshes
    add or alter only a small tail. Before calling the legacy row-oriented writer,
    build a database snapshot in a handful of queries and reduce the payload to
    only new or materially changed Sets, Cards and Prints. This keeps canonical
    freshness while avoiding tens of thousands of per-row SQL round trips.
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

    def _delta_payload(self, session, payload: dict) -> dict:
        """Return only rows whose canonical material state differs from source.

        ``punk_records`` PrintIdentifier ownership is deliberately excluded from
        freshness. The legacy reconciler may preserve a different identifier for
        a perfectly valid canonical physical print; treating that alias as a
        Bandai source contract turns every refresh into a permanent full replay.
        """

        if str(payload.get("source") or "").strip() != "onepiece_official_v2":
            return payload

        game = session.execute(select(Game).where(Game.slug == "onepiece")).scalar_one_or_none()
        if game is None:
            return payload

        language = self._normalize_language(str(payload.get("language") or "en"))
        region = str(payload.get("region") or "global-en").strip().lower()
        is_global = region == "global-en"

        existing_sets = {
            str(code or "").strip().lower(): {"id": set_id, "name": str(name or "").strip()}
            for set_id, code, name in session.execute(
                select(Set.id, Set.code, Set.name).where(Set.game_id == game.id)
            ).all()
        }
        existing_cards = {
            str(card_key or "").strip().lower(): {"id": card_id, "name": str(name or "").strip()}
            for card_id, card_key, name in session.execute(
                select(Card.id, Card.card_key, Card.name).where(
                    Card.game_id == game.id,
                    Card.card_key.is_not(None),
                )
            ).all()
        }

        print_rows = session.execute(
            select(
                Print.id,
                Card.card_key,
                Set.code,
                Print.collector_number,
                Print.language,
                Print.variant,
                Print.rarity,
                Print.print_key,
            )
            .join(Card, Card.id == Print.card_id)
            .join(Set, Set.id == Print.set_id)
            .where(Card.game_id == game.id)
        ).all()

        existing_prints: dict[tuple[str, str, str, str, str], dict] = {}
        existing_en_physical: set[tuple[str, str, str]] = set()
        for print_id, card_key, set_code, collector, print_language, variant, rarity, print_key in print_rows:
            normalized_set = str(set_code or "").strip().lower()
            normalized_collector = normalize_collector_number(collector)
            normalized_language = str(print_language or "").strip().lower()
            normalized_variant = normalize_variant(variant)
            identity = (
                str(card_key or "").strip().lower(),
                normalized_set,
                normalized_collector,
                normalized_language,
                normalized_variant,
            )
            existing_prints[identity] = {
                "id": int(print_id),
                "rarity": str(rarity).strip() if rarity is not None else None,
                "print_key": str(print_key or "").strip(),
            }
            if normalized_language == "en":
                existing_en_physical.add((normalized_set, normalized_collector, normalized_variant))

        primary_image_by_print: dict[int, str] = {}
        for print_id, url in session.execute(
            select(PrintImage.print_id, PrintImage.url)
            .join(Print, Print.id == PrintImage.print_id)
            .join(Card, Card.id == Print.card_id)
            .where(Card.game_id == game.id, PrintImage.is_primary.is_(True))
            .order_by(PrintImage.id.asc())
        ).all():
            primary_image_by_print.setdefault(int(print_id), str(url or "").strip())

        source_sets_by_code = {
            str(row.get("code") or "").strip().lower(): row
            for row in payload.get("sets") or []
            if str(row.get("code") or "").strip()
        }
        required_set_codes: set[str] = set()
        changed_set_codes: set[str] = set()

        for set_code, source_set in source_sets_by_code.items():
            existing = existing_sets.get(set_code)
            if existing is None:
                changed_set_codes.add(set_code)
                continue
            incoming_name = str(source_set.get("name") or set_code).strip()
            effective_name = incoming_name if is_global else existing["name"]
            if effective_name and effective_name != existing["name"]:
                changed_set_codes.add(set_code)

        delta_cards: list[dict] = []
        source_print_count = 0
        delta_print_count = 0
        reason_counts: dict[str, int] = {}

        def record_reason(reason: str) -> None:
            reason_counts[reason] = reason_counts.get(reason, 0) + 1

        for source_card in payload.get("cards") or []:
            card_key = str(source_card.get("id") or "").strip().lower()
            incoming_name = str(source_card.get("name") or "").strip()
            if not card_key or not incoming_name:
                continue

            existing_card = existing_cards.get(card_key)
            card_changed = existing_card is None
            if card_changed:
                record_reason("missing_card")
            if existing_card is not None:
                effective_name = incoming_name if is_global else existing_card["name"]
                card_changed = bool(effective_name and effective_name != existing_card["name"])
                if card_changed:
                    record_reason("card_name")

            changed_prints: list[dict] = []
            for source_print in source_card.get("prints") or []:
                source_print_count += 1
                set_code = str(source_print.get("set_code") or "").strip().lower()
                collector = normalize_collector_number(source_print.get("collector_number"))
                variant = normalize_variant(source_print.get("variant"))
                if not set_code or not collector:
                    continue

                identity = (card_key, set_code, collector, language, variant)
                existing_print = existing_prints.get(identity)
                print_reasons: set[str] = set()

                if existing_print is None:
                    print_reasons.add("missing_print")
                else:
                    expected_print_key = f"onepiece:{set_code}:{collector}:{language}:{variant}"
                    incoming_rarity_raw = source_print.get("rarity")
                    incoming_rarity = str(incoming_rarity_raw).strip() if incoming_rarity_raw not in (None, "") else None
                    if existing_print["rarity"] != incoming_rarity:
                        print_reasons.add("rarity")
                    if existing_print["print_key"] != expected_print_key:
                        print_reasons.add("print_key")

                    incoming_image = str(source_print.get("image_url") or "").strip()
                    physical_identity = (set_code, collector, variant)
                    if region == "asia-en" and physical_identity in existing_en_physical:
                        incoming_image = ""
                    if incoming_image and primary_image_by_print.get(existing_print["id"], "") != incoming_image:
                        print_reasons.add("primary_image")

                if print_reasons:
                    for reason in print_reasons:
                        record_reason(reason)
                    changed_prints.append(dict(source_print))
                    required_set_codes.add(set_code)
                    delta_print_count += 1

            if card_changed or changed_prints:
                card_copy = dict(source_card)
                card_copy["prints"] = changed_prints
                delta_cards.append(card_copy)

        required_set_codes.update(changed_set_codes)
        delta_sets = [
            dict(source_sets_by_code[code])
            for code in source_sets_by_code
            if code in required_set_codes
        ]

        diagnostics = dict(payload.get("diagnostics") or {})
        diagnostics["incremental_delta"] = {
            "source_cards": len(payload.get("cards") or []),
            "source_prints": source_print_count,
            "delta_cards": len(delta_cards),
            "delta_prints": delta_print_count,
            "delta_sets": len(delta_sets),
            "change_reasons": dict(sorted(reason_counts.items())),
            "region": region,
            "language": language,
        }
        delta = dict(payload)
        delta["sets"] = delta_sets
        delta["cards"] = delta_cards
        delta["diagnostics"] = diagnostics
        self.logger.info(
            "ingest onepiece delta region=%s language=%s source_cards=%s source_prints=%s delta_cards=%s delta_prints=%s delta_sets=%s reasons=%s",
            region,
            language,
            len(payload.get("cards") or []),
            source_print_count,
            len(delta_cards),
            delta_print_count,
            len(delta_sets),
            dict(sorted(reason_counts.items())),
        )
        return delta

    def upsert(self, session, payload: dict, stats, **kwargs) -> dict:
        delta = self._delta_payload(session, payload)
        touched = super().upsert(session, delta, stats, **kwargs)
        # The delta writer must still prove the complete source contract, not only
        # the rows selected for mutation.
        self._assert_promo_materialized(session, payload)
        return touched

    def repair_legacy_records(self, session, source, stats, **kwargs) -> dict:
        """Keep legacy image sweeping out of the canonical daily data refresh.

        The inherited repair scans every One Piece print and resolves images row
        by row. It is unrelated to source freshness and can dominate canonical
        refresh runtime. Image cleanup remains owned by the dedicated image repair
        workflows; the canonical writer only mutates source-backed delta rows.
        """
        self.logger.info(
            "ingest onepiece legacy_repair_skipped owner=dedicated_image_repair_workflow"
        )
        return {}
