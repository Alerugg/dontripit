from sqlalchemy import select

from app import db
from app.ingest.base import IngestStats
from app.ingest.connectors.tcgdex_pokemon_multilingual import MultilingualTcgdexPokemonConnector
from app.models import Card, Print, PrintIdentifier, Set
from app.multilingual_models import PrintLocalization


def _raw_card(*, language: str, set_name: str, card_name: str) -> dict:
    return {
        "_language": language,
        "set": {
            "id": "swsh1",
            "abbreviation": "SSH",
            "name": set_name,
            "releaseDate": "2020-02-07",
        },
        "id": "swsh1-1",
        "localId": "1",
        "name": card_name,
        "image": "https://assets.tcgdex.net/en/swsh/swsh1/1",
        "hp": 70,
        "stage": "Basic",
        "types": ["Grass"],
        "abilities": [],
        "attacks": [],
        "rules": [],
    }


def _upsert(connector, session, raw: dict) -> IngestStats:
    stats = IngestStats()
    normalized = connector.normalize(raw, lang=raw["_language"])
    connector.upsert(
        session,
        normalized,
        stats,
        lang=raw["_language"],
        source_name="tcgdex_pokemon",
    )
    session.flush()
    return stats


def test_tcgdex_multilingual_prints_are_isolated_and_canonical_names_stay_english(client):
    connector = MultilingualTcgdexPokemonConnector()

    en = _raw_card(language="en", set_name="Sword & Shield", card_name="Celebi")
    es = _raw_card(language="es", set_name="Espada y Escudo", card_name="Celebi")
    ja = _raw_card(language="ja", set_name="ソード＆シールド", card_name="セレビィ")

    with db.SessionLocal() as session:
        _upsert(connector, session, en)
        _upsert(connector, session, es)
        _upsert(connector, session, ja)
        session.commit()

        card = session.execute(select(Card).where(Card.tcgdex_id == "swsh1-1")).scalar_one()
        set_row = session.execute(select(Set).where(Set.tcgdex_id == "swsh1")).scalar_one()
        assert card.name == "Celebi"
        assert set_row.name == "Sword & Shield"

        prints = session.execute(
            select(Print).where(Print.card_id == card.id).order_by(Print.language)
        ).scalars().all()
        assert [row.language for row in prints] == ["en", "es", "ja"]

        by_language = {row.language: row for row in prints}
        assert by_language["en"].tcgdex_id == "swsh1-1"
        assert by_language["es"].tcgdex_id is None
        assert by_language["ja"].tcgdex_id is None
        assert len({row.id for row in prints}) == 3

        identifiers = session.execute(
            select(PrintIdentifier).where(PrintIdentifier.external_id == "swsh1-1")
        ).scalars().all()
        assert {row.source for row in identifiers} == {
            "tcgdex",
            "tcgdex:en",
            "tcgdex:es",
            "tcgdex:ja",
        }
        source_to_print = {row.source: row.print_id for row in identifiers}
        assert source_to_print["tcgdex"] == by_language["en"].id
        assert source_to_print["tcgdex:en"] == by_language["en"].id
        assert source_to_print["tcgdex:es"] == by_language["es"].id
        assert source_to_print["tcgdex:ja"] == by_language["ja"].id

        localizations = session.execute(
            select(PrintLocalization).where(
                PrintLocalization.external_id == "swsh1-1"
            )
        ).scalars().all()
        assert {row.language for row in localizations} == {"en", "es", "ja"}
        localized_names = {row.language: row.card_name for row in localizations}
        localized_sets = {row.language: row.set_name for row in localizations}
        assert localized_names == {
            "en": "Celebi",
            "es": "Celebi",
            "ja": "セレビィ",
        }
        assert localized_sets == {
            "en": "Sword & Shield",
            "es": "Espada y Escudo",
            "ja": "ソード＆シールド",
        }


def test_tcgdex_multilingual_rerun_is_idempotent(client):
    connector = MultilingualTcgdexPokemonConnector()
    en = _raw_card(language="en", set_name="Sword & Shield", card_name="Celebi")
    es = _raw_card(language="es", set_name="Espada y Escudo", card_name="Celebi")

    with db.SessionLocal() as session:
        _upsert(connector, session, en)
        _upsert(connector, session, es)
        session.commit()

        counts_before = {
            "prints": len(session.execute(select(Print)).scalars().all()),
            "identifiers": len(session.execute(select(PrintIdentifier)).scalars().all()),
            "localizations": len(session.execute(select(PrintLocalization)).scalars().all()),
        }

        rerun_stats = _upsert(connector, session, es)
        session.commit()

        counts_after = {
            "prints": len(session.execute(select(Print)).scalars().all()),
            "identifiers": len(session.execute(select(PrintIdentifier)).scalars().all()),
            "localizations": len(session.execute(select(PrintLocalization)).scalars().all()),
        }
        assert counts_after == counts_before
        assert rerun_stats.records_inserted == 0
        assert rerun_stats.records_updated == 0


def test_non_english_ingest_never_creates_canonical_identity_without_english(client):
    connector = MultilingualTcgdexPokemonConnector()
    es = _raw_card(language="es", set_name="Espada y Escudo", card_name="Celebi")

    with db.SessionLocal() as session:
        stats = _upsert(connector, session, es)
        session.commit()

        assert session.execute(select(Card)).scalars().all() == []
        assert session.execute(select(Set)).scalars().all() == []
        assert session.execute(select(Print)).scalars().all() == []
        assert session.execute(select(PrintLocalization)).scalars().all() == []
        assert stats.records_inserted == 0
        assert stats.records_updated == 0
