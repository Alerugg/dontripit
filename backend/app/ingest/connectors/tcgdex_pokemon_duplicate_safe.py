from __future__ import annotations

import os

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

    Full production recertification can optionally be split into deterministic
    set shards through ``POKEMON_SHARD_INDEX`` / ``POKEMON_SHARD_COUNT``. The
    filtering happens on the language ``/sets`` index before any set details are
    fetched, so each job loads and writes only its own disjoint slice. Explicit
    set and limited probes keep the established unsharded behavior.
    """

    @staticmethod
    def _pokemon_shard_config() -> tuple[int, int]:
        try:
            shard_count = int(os.getenv("POKEMON_SHARD_COUNT", "1"))
            shard_index = int(os.getenv("POKEMON_SHARD_INDEX", "0"))
        except ValueError as exc:
            raise RuntimeError("Pokemon shard configuration must be integer-valued") from exc

        if shard_count < 1:
            raise RuntimeError(f"POKEMON_SHARD_COUNT must be >= 1, got {shard_count}")
        if shard_index < 0 or shard_index >= shard_count:
            raise RuntimeError(
                "POKEMON_SHARD_INDEX must satisfy 0 <= index < count: "
                f"index={shard_index} count={shard_count}"
            )
        return shard_index, shard_count

    @staticmethod
    def _select_shard_sets(
        items: list[dict],
        *,
        shard_index: int,
        shard_count: int,
    ) -> list[dict]:
        if shard_count == 1:
            return list(items)

        ordered = sorted(
            (
                item
                for item in items
                if isinstance(item, dict) and str(item.get("id") or "").strip()
            ),
            key=lambda item: str(item.get("id") or "").strip(),
        )
        return [
            item
            for position, item in enumerate(ordered)
            if position % shard_count == shard_index
        ]

    def _load_remote(
        self,
        limit: int | None = None,
        set_id: str | None = None,
        lang: str = "en",
    ) -> list[dict]:
        shard_index, shard_count = self._pokemon_shard_config()
        if set_id is not None or limit is not None or shard_count == 1:
            return super()._load_remote(limit=limit, set_id=set_id, lang=lang)

        self._pokemon_active_shard = (shard_index, shard_count)
        self.logger.info(
            "ingest tcgdex shard_start lang=%s shard_index=%s shard_count=%s",
            lang,
            shard_index,
            shard_count,
        )
        try:
            return super()._load_remote(limit=limit, set_id=set_id, lang=lang)
        finally:
            self._pokemon_active_shard = None

    def _request_json(self, url: str, params: dict | None = None):
        payload = super()._request_json(url, params=params)
        shard = getattr(self, "_pokemon_active_shard", None)
        if (
            shard is None
            or not isinstance(payload, list)
            or url.rstrip("/").rsplit("/", 1)[-1] != "sets"
        ):
            return payload

        shard_index, shard_count = shard
        selected = self._select_shard_sets(
            payload,
            shard_index=shard_index,
            shard_count=shard_count,
        )
        self.logger.info(
            "ingest tcgdex shard_sets total_sets=%s selected_sets=%s "
            "shard_index=%s shard_count=%s",
            len(payload),
            len(selected),
            shard_index,
            shard_count,
        )
        return selected

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
