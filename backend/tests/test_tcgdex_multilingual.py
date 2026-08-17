import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app import db
from app.ingest.base import IngestStats
from app.ingest.connectors.tcgdex_pokemon_multilingual import MultilingualTcgdexPokemonConnector
from app.models import Card, Print, PrintIdentifier, Set
from app.multilingual_models import (
    CardIdentifier,
    PrintLocalization,
    SetIdentifier,
)


def _raw_card(
    *,
    language: str,
    set_id: str,
    set_name: str,
    card_id: str,
    card_name: str,
    local_id: str = "1",
) -> dict:
    return {
        "_language": language,
        "set": {
            "id": set_id,
            "abbreviation": set_id,
            "name": set_name,
            "releaseDate": "2020-02-07",
        },
        "id": card_id,
        "localId": local_id,
        "name": card_name,
        "image": f"https://assets.tcgdex.net/{language}/{set_id}/{local_id}",
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


def test_en_es_share_exact_canonical_identity_but_have_separate_prints(client):
    connector = MultilingualTcgdexPokemonConnector()
    en = _raw_card(
        language="en",
        set_id="swsh1",
        set_name="Sword & Shield",
        card_id="swsh1-1",
        card_name="Celebi",
    )
    es = _raw_card(
        language="es",
        set_id="swsh1",
        set_name="Espada y Escudo",
        card_id="swsh1-1",
        card_name="Celebi",
    )

    with db.SessionLocal() as session:
        _upsert(connector, session, en)
        _upsert(connector, session, es)
        session.commit()

        card = session.execute(select(Card).where(Card.tcgdex_id == "swsh1-1")).scalar_one()
        set_row = session.execute(select(Set).where(Set.tcgdex_id == "swsh1")).scalar_one()
        assert card.name == "Celebi"
        assert set_row.name == "Sword & Shield"

        prints = session.execute(
            select(Print).where(Print.card_id == card.id).order_by(Print.language)
        ).scalars().all()
        assert [row.language for row in prints] == ["en", "es"]
        by_language = {row.language: row for row in prints}
        assert by_language["en"].tcgdex_id == "swsh1-1"
        assert by_language["es"].tcgdex_id is None

        card_ids = session.execute(
            select(CardIdentifier).where(CardIdentifier.external_id == "swsh1-1")
        ).scalars().all()
        assert {row.source for row in card_ids} == {"tcgdex:en", "tcgdex:es"}
        assert {row.card_id for row in card_ids} == {card.id}

        set_ids = session.execute(
            select(SetIdentifier).where(SetIdentifier.external_id == "swsh1")
        ).scalars().all()
        assert {row.source for row in set_ids} == {"tcgdex:en", "tcgdex:es"}
        assert {row.set_id for row in set_ids} == {set_row.id}

        localizations = session.execute(
            select(PrintLocalization).where(PrintLocalization.external_id == "swsh1-1")
        ).scalars().all()
        assert {row.language for row in localizations} == {"en", "es"}
        localized_sets = {row.language: row.set_name for row in localizations}
        assert localized_sets == {
            "en": "Sword & Shield",
            "es": "Espada y Escudo",
        }


def test_ja_raw_id_collision_never_merges_with_unrelated_english_card(client):
    connector = MultilingualTcgdexPokemonConnector()
    # This mirrors the live TCGdex collision observed on 2026-08-14: the same
    # raw neo4-100 ID describes different physical cards in EN and JA.
    en = _raw_card(
        language="en",
        set_id="neo4",
        set_name="Neo Destiny",
        card_id="neo4-100",
        card_name="Lucky Stadium",
        local_id="100",
    )
    ja = _raw_card(
        language="ja",
        set_id="neo4",
        set_name="闇、そして光へ...",
        card_id="neo4-100",
        card_name="ビルからのメール",
        local_id="100",
    )

    with db.SessionLocal() as session:
        _upsert(connector, session, en)
        _upsert(connector, session, ja)
        session.commit()

        english_card = session.execute(
            select(Card).where(Card.tcgdex_id == "neo4-100")
        ).scalar_one()
        japanese_identifier = session.execute(
            select(CardIdentifier).where(
                CardIdentifier.source == "tcgdex:ja",
                CardIdentifier.external_id == "neo4-100",
            )
        ).scalar_one()
        japanese_card = session.get(Card, japanese_identifier.card_id)
        assert japanese_card is not None
        assert english_card.id != japanese_card.id
        assert english_card.name == "Lucky Stadium"
        assert japanese_card.name == "ビルからのメール"
        assert japanese_card.tcgdex_id is None
        assert japanese_card.card_key == "tcgdex:ja:neo4-100"

        english_set = session.execute(
            select(Set).where(Set.tcgdex_id == "neo4")
        ).scalar_one()
        japanese_set_identifier = session.execute(
            select(SetIdentifier).where(
                SetIdentifier.source == "tcgdex:ja",
                SetIdentifier.external_id == "neo4",
            )
        ).scalar_one()
        japanese_set = session.get(Set, japanese_set_identifier.set_id)
        assert japanese_set is not None
        assert english_set.id != japanese_set.id
        assert japanese_set.tcgdex_id is None
        assert japanese_set.code == "ja-neo4"

        english_print = session.execute(
            select(Print).where(Print.card_id == english_card.id)
        ).scalar_one()
        japanese_print = session.execute(
            select(Print).where(Print.card_id == japanese_card.id)
        ).scalar_one()
        assert english_print.language == "en"
        assert japanese_print.language == "ja"
        assert english_print.tcgdex_id == "neo4-100"
        assert japanese_print.tcgdex_id is None
        assert english_print.id != japanese_print.id

        scoped_print_ids = session.execute(
            select(PrintIdentifier).where(PrintIdentifier.external_id == "neo4-100")
        ).scalars().all()
        scoped = {row.source: row.print_id for row in scoped_print_ids}
        assert scoped["tcgdex:en"] == english_print.id
        assert scoped["tcgdex:ja"] == japanese_print.id
        assert scoped["tcgdex"] == english_print.id


def test_japanese_rerun_is_idempotent(client):
    connector = MultilingualTcgdexPokemonConnector()
    ja = _raw_card(
        language="ja",
        set_id="SV4a",
        set_name="シャイニートレジャーex",
        card_id="SV4a-001",
        card_name="ナゾノクサ",
        local_id="001",
    )

    with db.SessionLocal() as session:
        _upsert(connector, session, ja)
        session.commit()
        counts_before = {
            "cards": len(session.execute(select(Card)).scalars().all()),
            "sets": len(session.execute(select(Set)).scalars().all()),
            "prints": len(session.execute(select(Print)).scalars().all()),
            "print_identifiers": len(session.execute(select(PrintIdentifier)).scalars().all()),
            "card_identifiers": len(session.execute(select(CardIdentifier)).scalars().all()),
            "set_identifiers": len(session.execute(select(SetIdentifier)).scalars().all()),
            "localizations": len(session.execute(select(PrintLocalization)).scalars().all()),
        }

        rerun_stats = _upsert(connector, session, ja)
        session.commit()
        counts_after = {
            "cards": len(session.execute(select(Card)).scalars().all()),
            "sets": len(session.execute(select(Set)).scalars().all()),
            "prints": len(session.execute(select(Print)).scalars().all()),
            "print_identifiers": len(session.execute(select(PrintIdentifier)).scalars().all()),
            "card_identifiers": len(session.execute(select(CardIdentifier)).scalars().all()),
            "set_identifiers": len(session.execute(select(SetIdentifier)).scalars().all()),
            "localizations": len(session.execute(select(PrintLocalization)).scalars().all()),
        }
        assert counts_after == counts_before
        assert rerun_stats.records_inserted == 0
        assert rerun_stats.records_updated == 0


def test_spanish_without_exact_english_identity_is_rejected(client):
    connector = MultilingualTcgdexPokemonConnector()
    es = _raw_card(
        language="es",
        set_id="swsh1",
        set_name="Espada y Escudo",
        card_id="swsh1-1",
        card_name="Celebi",
    )

    with db.SessionLocal() as session:
        stats = _upsert(connector, session, es)
        session.commit()
        assert session.execute(select(Card)).scalars().all() == []
        assert session.execute(select(Set)).scalars().all() == []
        assert session.execute(select(Print)).scalars().all() == []
        assert stats.records_inserted == 0
        assert stats.records_updated == 0


def test_uncertified_tcgdex_language_fails_closed(client):
    connector = MultilingualTcgdexPokemonConnector()
    fr = _raw_card(
        language="fr",
        set_id="swsh1",
        set_name="Épée et Bouclier",
        card_id="swsh1-1",
        card_name="Celebi",
    )
    with pytest.raises(RuntimeError, match="Uncertified TCGdex language semantics"):
        connector.normalize(fr, lang="fr")


def test_print_localization_rejects_second_source_for_same_print_language(client):
    connector = MultilingualTcgdexPokemonConnector()
    en = _raw_card(
        language="en",
        set_id="swsh1",
        set_name="Sword & Shield",
        card_id="swsh1-1",
        card_name="Celebi",
    )

    with db.SessionLocal() as session:
        _upsert(connector, session, en)
        session.commit()

        print_row = session.execute(
            select(Print).where(Print.language == "en")
        ).scalar_one()
        session.add(
            PrintLocalization(
                print_id=print_row.id,
                language="en",
                source="other-source",
                external_id="other-id",
                card_name="Competing truth",
                set_name="Competing set",
                details_json={},
            )
        )
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()