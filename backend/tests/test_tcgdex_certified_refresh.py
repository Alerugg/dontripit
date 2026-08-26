from __future__ import annotations

import threading
import time

from sqlalchemy import func, select

from app import db
from app.ingest.base import IngestStats
from app.ingest.connectors.tcgdex_pokemon_certified_refresh import (
    CertifiedRefreshPokemonTCGDexConnector,
)
from app.models import Card, Game, Print, Set
from app.multilingual_models import CardIdentifier, PrintLocalization, SetIdentifier


class ConcurrentProbeConnector(CertifiedRefreshPokemonTCGDexConnector):
    def __init__(self):
        super().__init__()
        self._lock = threading.Lock()
        self.active_set_requests = 0
        self.max_active_set_requests = 0

    def _request_json(self, url: str, params=None):
        base = "https://api.tcgdex.net/v2/en"
        if url == f"{base}/series":
            return [{"id": "base", "name": "Base"}]
        if url == f"{base}/sets":
            return [
                {"id": "base1", "name": "Base Set"},
                {"id": "base2", "name": "Jungle"},
            ]
        if url in {f"{base}/sets/base1", f"{base}/sets/base2"}:
            remote_set_id = url.rsplit("/", 1)[-1]
            with self._lock:
                self.active_set_requests += 1
                self.max_active_set_requests = max(
                    self.max_active_set_requests,
                    self.active_set_requests,
                )
            try:
                # Long enough for the second worker to overlap deterministically,
                # short enough to keep the focused CI test fast.
                time.sleep(0.05)
                number = "1" if remote_set_id == "base1" else "2"
                return {
                    "id": remote_set_id,
                    "name": "Base Set" if remote_set_id == "base1" else "Jungle",
                    "cards": [
                        {
                            "id": f"{remote_set_id}-{number}",
                            "localId": number,
                            "name": "Detailed card",
                            "hp": 60 if remote_set_id == "base1" else 70,
                            "attacks": [{"name": "Test attack", "damage": "10"}],
                        }
                    ],
                }
            finally:
                with self._lock:
                    self.active_set_requests -= 1
        raise AssertionError(f"Unexpected TCGdex request: {url}")


def test_full_certified_refresh_fetches_sets_concurrently_without_losing_detail_or_order():
    connector = ConcurrentProbeConnector()

    rows = connector._load_remote(lang="en")

    assert [row["id"] for row in rows] == ["base1-1", "base2-2"]
    assert [row["hp"] for row in rows] == [60, 70]
    assert [row["attacks"][0]["name"] for row in rows] == ["Test attack", "Test attack"]
    assert connector.max_active_set_requests >= 2


def test_targeted_refresh_keeps_the_existing_serial_path():
    connector = ConcurrentProbeConnector()

    rows = connector._load_remote(set_id="base1", lang="en")

    assert [row["id"] for row in rows] == ["base1-1"]
    assert connector.max_active_set_requests == 1


def test_set_identifier_backfill_is_idempotent_before_session_flush(client):
    connector = CertifiedRefreshPokemonTCGDexConnector()
    stats = IngestStats()

    with db.SessionLocal() as session:
        game = Game(slug="pokemon", name="Pokémon")
        session.add(game)
        session.flush()
        set_row = Set(
            game_id=game.id,
            code="base1",
            tcgdex_id="base1",
            name="Base Set",
        )
        session.add(set_row)
        session.flush()

        # Production uses autoflush-disabled sessions. Reproduce the exact failure:
        # several cards from one set ask for the same language-qualified set alias
        # before any unrelated Card/Print creation happens to flush pending rows.
        with session.no_autoflush:
            connector._upsert_set_identifier(
                session,
                set_row=set_row,
                language="en",
                external_id="base1",
                stats=stats,
            )
            connector._upsert_set_identifier(
                session,
                set_row=set_row,
                language="en",
                external_id="base1",
                stats=stats,
            )

        session.flush()
        count = session.execute(
            select(func.count(SetIdentifier.id)).where(
                SetIdentifier.source == "tcgdex:en",
                SetIdentifier.external_id == "base1",
            )
        ).scalar_one()

    assert count == 1
    assert stats.records_inserted == 1


def test_certified_upsert_does_not_rewrite_legacy_complete_en_for_new_provenance(client):
    connector = CertifiedRefreshPokemonTCGDexConnector()

    with db.SessionLocal() as session:
        game = Game(slug="pokemon", name="Pokémon")
        session.add(game)
        session.flush()
        set_row = Set(
            game_id=game.id,
            code="base1",
            tcgdex_id="base1",
            name="Base Set",
        )
        card_row = Card(
            game_id=game.id,
            name="Alakazam",
            tcgdex_id="base1-1",
            card_key="pokemon:base1:1",
        )
        session.add_all([set_row, card_row])
        session.flush()
        session.add(
            Print(
                set_id=set_row.id,
                card_id=card_row.id,
                collector_number="1",
                language="en",
                rarity="unknown",
                is_foil=False,
                variant="default",
                tcgdex_id="base1-1",
            )
        )
        session.commit()

    payload = {
        "language": "en",
        "set": {
            "tcgdex_id": "base1",
            "code": "base1",
            "name": "Base Set",
            "released_at": None,
        },
        "card": {
            "id": "base1-1",
            "collector_number": "1",
            "name": "Alakazam",
            "card_key": "pokemon:base1:1",
            "image": None,
        },
        "localization": {
            "card_name": "Alakazam",
            "set_name": "Base Set",
            "details": {},
        },
    }

    with db.SessionLocal() as session:
        stats = IngestStats()
        result = connector.upsert(session, payload, stats)
        session.flush()

        set_aliases = session.execute(select(func.count(SetIdentifier.id))).scalar_one()
        card_aliases = session.execute(select(func.count(CardIdentifier.id))).scalar_one()
        localizations = session.execute(select(func.count(PrintLocalization.id))).scalar_one()

    assert result == {}
    assert stats.records_inserted == 0
    assert stats.records_updated == 0
    # Legacy-complete EN does not require multilingual aliases/localizations.
    assert set_aliases == 0
    assert card_aliases == 0
    assert localizations == 0
