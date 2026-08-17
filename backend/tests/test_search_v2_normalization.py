from app.search_v2.facets import facets_for_game
from app.search_v2.normalization import (
    build_search_text,
    compact_search_text,
    normalize_language,
    normalize_onepiece_collector_number,
    normalize_onepiece_set_code,
    normalize_search_text,
    variant_family,
)


def test_human_name_normalization_handles_punctuation_accents_and_spacing():
    assert normalize_search_text("Monkey.D.Luffy") == "monkey d luffy"
    assert normalize_search_text("  Pokémon—TCG  ") == "pokemon tcg"
    assert compact_search_text("OP05-119") == "op05119"


def test_onepiece_codes_accept_common_human_formats():
    assert normalize_onepiece_set_code("OP05") == "op-05"
    assert normalize_onepiece_set_code("op-5") == "op-05"
    assert normalize_onepiece_collector_number("OP05-119") == "op05-119"
    assert normalize_onepiece_collector_number("op 05 119") == "op05-119"
    assert normalize_onepiece_collector_number("P-001") == "p-001"


def test_languages_work_with_codes_and_human_names():
    assert normalize_language("English") == "en"
    assert normalize_language("inglés") == "en"
    assert normalize_language("Japanese") == "ja"
    assert normalize_language("JP") == "ja"


def test_exact_variant_and_family_are_separate_dimensions():
    assert variant_family("p1") == "parallel"
    assert variant_family("P9") == "parallel"
    assert variant_family("r1") == "reprint"
    assert variant_family("default") == "default"


def test_search_document_keeps_useful_tokens_without_duplicates():
    text = build_search_text(
        "Monkey.D.Luffy",
        "OP05-119",
        ["Awakening of the New Era", "English", "Manga"],
        {"traits": ["Straw Hat Crew"]},
    )
    assert "monkey d luffy" in text
    assert "op05 119" in text
    assert "awakening of the new era" in text
    assert "straw hat crew" in text


def test_onepiece_advanced_facets_cover_identity_gameplay_and_collecting():
    facets = facets_for_game("onepiece")
    keys = {row["key"] for row in facets}
    required = {
        "set",
        "collector_number",
        "release",
        "language",
        "color",
        "card_type",
        "cost",
        "life",
        "power",
        "counter",
        "attribute",
        "traits",
        "block",
        "rarity",
        "illustration_type",
        "variant_family",
        "exact_variant",
        "promo",
        "manga",
        "sp",
        "treasure_rare",
    }
    assert required.issubset(keys)
    assert any(row["key"] == "color" and row["quick_filter"] for row in facets)
    assert any(row["key"] == "traits" and row["searchable"] for row in facets)
