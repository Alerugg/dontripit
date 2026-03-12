from sqlalchemy import select

from app import db
from app.ingest.normalization import (
    build_card_key,
    build_print_key,
    normalize_collector_number,
    normalize_language,
    normalize_variant,
)
from app.ingest.normalized_schema import parse_normalized_payload
from app.ingest.registry import get_connector
from app.models import Print


def test_canonical_key_generation_is_stable():
    card_key_a = build_card_key(
        game_slug="yugioh",
        canonical_name="Dark Magician",
        external_ids=[{"source": "ygoprodeck", "id_type": "card_id", "value": "46986414"}],
    )
    card_key_b = build_card_key(
        game_slug="yugioh",
        canonical_name="Dark Magician",
        external_ids=[{"source": "ygoprodeck", "id_type": "card_id", "value": "46986414"}],
    )
    assert card_key_a == card_key_b

    print_key_a = build_print_key(
        card_key=card_key_a,
        set_code="SDK-001",
        collector_number="001",
        language="EN-US",
        finish="nonfoil",
        variant="Ultra Rare",
    )
    print_key_b = build_print_key(
        card_key=card_key_a,
        set_code="sdk_001",
        collector_number="1",
        language="english",
        finish="nonfoil",
        variant="ultra-rare",
    )
    assert print_key_a == print_key_b


def test_normalization_helpers_basic_behavior():
    assert normalize_collector_number("001A") == "1a"
    assert normalize_collector_number("  0007 ") == "7"
    assert normalize_language("EN-US") == "en"
    assert normalize_language("Japanese") == "ja"
    assert normalize_variant("Ultra Rare") == "ultra-rare"


def test_ygoprodeck_normalize_emits_valid_contract():
    connector = get_connector("ygoprodeck_yugioh")
    payloads = connector.load("data/fixtures/ygoprodeck_yugioh_sample.json", fixture=True, limit=1)
    _, card_payload, _ = payloads[0]

    normalized = connector.normalize(card_payload)
    parsed = parse_normalized_payload(normalized)

    assert parsed.normalized_game.slug == "yugioh"
    assert parsed.normalized_card.card_key
    assert parsed.normalized_prints
    assert all(item.print_key for item in parsed.normalized_prints)


def test_ygoprodeck_pipeline_avoids_obvious_duplicate_prints(client):
    connector = get_connector("ygoprodeck_yugioh")
    with db.SessionLocal() as session:
        connector.run(
            session,
            "data/fixtures/ygoprodeck_yugioh_sample.json",
            fixture=True,
            incremental=False,
            limit=1,
        )
        session.commit()

    with db.SessionLocal() as session:
        before = session.execute(select(Print.print_key, Print.id).where(Print.print_key.is_not(None))).all()

    with db.SessionLocal() as session:
        connector.run(
            session,
            "data/fixtures/ygoprodeck_yugioh_sample.json",
            fixture=True,
            incremental=False,
            limit=1,
        )
        session.commit()

    with db.SessionLocal() as session:
        after = session.execute(select(Print.print_key, Print.id).where(Print.print_key.is_not(None))).all()

    assert len(before) == len(after)
    assert {row[0] for row in before} == {row[0] for row in after}


def test_ygoprodeck_normalize_prefers_best_available_image_url():
    connector = get_connector("ygoprodeck_yugioh")
    payload = {
        "id": 123,
        "name": "Test Monster",
        "card_sets": [
            {"set_code": "TS-001", "set_name": "Test Set", "set_rarity": "Common"},
        ],
        "card_images": [
            {"image_url_cropped": "https://img.example/crop.jpg"},
            {"image_url": "https://img.example/full.jpg"},
        ],
    }

    normalized = connector.normalize(payload)

    assert normalized["card_image_url"] == "https://img.example/crop.jpg"
    assert normalized["normalized_images"]
    assert normalized["normalized_images"][0]["url"] == "https://img.example/crop.jpg"
