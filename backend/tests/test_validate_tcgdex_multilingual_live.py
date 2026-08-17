from app.scripts.validate_tcgdex_multilingual_live import select_physical_samples


def _card(card_id: str, name: str) -> dict:
    return {"id": card_id, "name": name}


def test_live_sample_selector_respects_international_and_japanese_identity_spaces():
    maps = {
        "en": {
            "sv1-001": _card("sv1-001", "Sprigatito"),
            "sv1-002": _card("sv1-002", "Fuecoco"),
            "neo4-100": _card("neo4-100", "Lucky Stadium"),
        },
        "es": {
            "sv1-001": _card("sv1-001", "Sprigatito"),
            "sv1-002": _card("sv1-002", "Fuecoco"),
        },
        "ja": {
            "SV4a-001": _card("SV4a-001", "ナゾノクサ"),
            "SV4a-002": _card("SV4a-002", "クサイハナ"),
            "neo4-100": _card("neo4-100", "ビルからのメール"),
        },
    }

    international, japanese, collision = select_physical_samples(maps, limit=2)

    assert international == ["sv1-001", "sv1-002"]
    assert japanese == ["SV4a-001", "SV4a-002"]
    assert collision == "neo4-100"


def test_live_sample_selector_does_not_require_en_es_ja_triple_overlap():
    maps = {
        "en": {
            "set-001": _card("set-001", "A"),
        },
        "es": {
            "set-001": _card("set-001", "A"),
        },
        "ja": {
            "JP1-001": _card("JP1-001", "エー"),
        },
    }

    international, japanese, collision = select_physical_samples(maps, limit=1)

    assert international == ["set-001"]
    assert japanese == ["JP1-001"]
    assert collision is None
