from __future__ import annotations

from sqlalchemy import event

from app import db
from app.ingest.connectors.tcgdex_pokemon_certified_refresh import (
    CertifiedRefreshPokemonTCGDexConnector,
)
from app.models import Card, Game, Print, PrintIdentifier, Set
from app.multilingual_models import CardIdentifier, PrintLocalization, SetIdentifier


def _normalized(language: str, set_id: str, card_id: str, collector_number: str) -> dict:
    return {
        "language": language,
        "set": {"tcgdex_id": set_id},
        "card": {
            "id": card_id,
            "collector_number": collector_number,
        },
    }


def test_certified_en_completeness_cache_preserves_legacy_contract_and_reuses_one_snapshot(client):
    with db.SessionLocal() as session:
        game = Game(slug="pokemon", name="Pokémon")
        session.add(game)
        session.flush()
        set_row = Set(game_id=game.id, code="sv1", tcgdex_id="sv1", name="SV1")
        card_row = Card(game_id=game.id, name="Pikachu", tcgdex_id="sv1-001")
        session.add_all([set_row, card_row])
        session.flush()
        session.add(
            Print(
                set_id=set_row.id,
                card_id=card_row.id,
                collector_number="001",
                language="en",
                is_foil=False,
                variant="default",
                tcgdex_id="sv1-001",
            )
        )
        session.commit()

    connector = CertifiedRefreshPokemonTCGDexConnector()
    statements: list[str] = []

    def capture_sql(_conn, _cursor, statement, _parameters, _context, _executemany):
        statements.append(statement)

    event.listen(db.engine, "before_cursor_execute", capture_sql)
    try:
        with db.SessionLocal() as session:
            payload = _normalized("en", "sv1", "sv1-001", "001")
            assert connector._localized_state_complete(session, payload) is True
            after_first_lookup = len(statements)
            assert after_first_lookup > 0

            # The second card check on the same session/language must be a pure
            # in-memory membership test; no per-card identity SQL is allowed.
            assert connector._localized_state_complete(session, payload) is True
            assert len(statements) == after_first_lookup
    finally:
        event.remove(db.engine, "before_cursor_execute", capture_sql)


def test_certified_multilingual_cache_requires_full_es_and_ja_identity_contract(client):
    with db.SessionLocal() as session:
        game = Game(slug="pokemon", name="Pokémon")
        session.add(game)
        session.flush()

        # ES shares the exact international EN Set/Card identity.
        es_set = Set(game_id=game.id, code="sv1", tcgdex_id="sv1", name="SV1")
        es_card_ok = Card(game_id=game.id, name="Pikachu", tcgdex_id="sv1-001")
        es_card_missing_localization = Card(
            game_id=game.id,
            name="Raichu",
            tcgdex_id="sv1-002",
        )
        session.add_all([es_set, es_card_ok, es_card_missing_localization])
        session.flush()

        es_print_ok = Print(
            set_id=es_set.id,
            card_id=es_card_ok.id,
            collector_number="001",
            language="es",
            is_foil=False,
            variant="default",
            tcgdex_id=None,
        )
        es_print_incomplete = Print(
            set_id=es_set.id,
            card_id=es_card_missing_localization.id,
            collector_number="002",
            language="es",
            is_foil=False,
            variant="default",
            tcgdex_id=None,
        )
        session.add_all([es_print_ok, es_print_incomplete])
        session.flush()

        session.add(SetIdentifier(set_id=es_set.id, source="tcgdex:es", external_id="sv1"))
        session.add_all(
            [
                CardIdentifier(
                    card_id=es_card_ok.id,
                    source="tcgdex:es",
                    external_id="sv1-001",
                ),
                CardIdentifier(
                    card_id=es_card_missing_localization.id,
                    source="tcgdex:es",
                    external_id="sv1-002",
                ),
                PrintIdentifier(
                    print_id=es_print_ok.id,
                    source="tcgdex:es",
                    external_id="sv1-001",
                ),
                PrintIdentifier(
                    print_id=es_print_incomplete.id,
                    source="tcgdex:es",
                    external_id="sv1-002",
                ),
                PrintLocalization(
                    print_id=es_print_ok.id,
                    language="es",
                    source="tcgdex",
                    external_id="sv1-001",
                    card_name="Pikachu",
                    set_name="SV1",
                    details_json={},
                ),
            ]
        )

        # JA owns a separate regional identity and therefore resolves through
        # language-qualified Set/Card/Print identifiers rather than legacy IDs.
        ja_set = Set(game_id=game.id, code="ja-sv1", name="JA SV1")
        ja_card = Card(game_id=game.id, name="ピカチュウ", card_key="tcgdex:ja:ja-sv1-001")
        session.add_all([ja_set, ja_card])
        session.flush()
        ja_print = Print(
            set_id=ja_set.id,
            card_id=ja_card.id,
            collector_number="001",
            language="ja",
            is_foil=False,
            variant="default",
            tcgdex_id=None,
        )
        session.add(ja_print)
        session.flush()
        session.add_all(
            [
                SetIdentifier(
                    set_id=ja_set.id,
                    source="tcgdex:ja",
                    external_id="ja-sv1",
                ),
                CardIdentifier(
                    card_id=ja_card.id,
                    source="tcgdex:ja",
                    external_id="ja-sv1-001",
                ),
                PrintIdentifier(
                    print_id=ja_print.id,
                    source="tcgdex:ja",
                    external_id="ja-sv1-001",
                ),
                PrintLocalization(
                    print_id=ja_print.id,
                    language="ja",
                    source="tcgdex",
                    external_id="ja-sv1-001",
                    card_name="ピカチュウ",
                    set_name="JA SV1",
                    details_json={},
                ),
            ]
        )
        session.commit()

    with db.SessionLocal() as session:
        connector = CertifiedRefreshPokemonTCGDexConnector()
        assert connector._localized_state_complete(
            session,
            _normalized("es", "sv1", "sv1-001", "001"),
        ) is True
        assert connector._localized_state_complete(
            session,
            _normalized("es", "sv1", "sv1-002", "002"),
        ) is False

        # Use a fresh connector so JA builds its own exact cache from the same DB.
        connector = CertifiedRefreshPokemonTCGDexConnector()
        assert connector._localized_state_complete(
            session,
            _normalized("ja", "ja-sv1", "ja-sv1-001", "001"),
        ) is True
