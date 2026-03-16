from app.onepiece_legacy_policy import (
    is_legacy_onepiece_print,
    is_onepiece_canonical_external_id,
    is_onepiece_legacy_external_id,
)


def test_onepiece_legacy_external_id_pattern_detects_legacy_suffixes():
    assert is_onepiece_legacy_external_id("op01-001-default-en")
    assert is_onepiece_legacy_external_id("eb01-012-parallel-en")
    assert not is_onepiece_legacy_external_id("OP01-016_p1")


def test_onepiece_canonical_external_id_pattern_accepts_new_family():
    assert is_onepiece_canonical_external_id("OP01-016_p1")
    assert is_onepiece_canonical_external_id("ST01-012")
    assert is_onepiece_canonical_external_id("P-001")
    assert not is_onepiece_canonical_external_id("st10-001-default-en")


def test_is_legacy_onepiece_print_is_scoped_to_onepiece_only():
    assert is_legacy_onepiece_print(
        game_slug="onepiece",
        primary_image_url="https://placehold.co/367x512?text=ONE+PIECE",
        external_id="op01-001-default-en",
    )
    assert not is_legacy_onepiece_print(
        game_slug="yugioh",
        primary_image_url="https://placehold.co/367x512?text=YGO",
        external_id="dark-magician-default-en",
    )
