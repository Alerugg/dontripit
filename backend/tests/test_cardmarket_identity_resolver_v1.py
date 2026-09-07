from __future__ import annotations

from app.jobs.cardmarket_catalog_audit import ProductListRow
from app.jobs.cardmarket_identity_resolver import (
    ResolverIdentity,
    build_catalog_products,
    cardmarket_redirect_url,
    resolve_identity,
    split_pokemon_descriptor,
    split_terminal_collector,
)


def product(product_id: str, name: str, expansion_id: str) -> ProductListRow:
    return ProductListRow(
        product_id=product_id,
        name=name,
        category_id="",
        category="",
        expansion_id=expansion_id,
        metacard_id=None,
    )


def identity(game: str, *, name: str, collector: str = "", known=(), source=()) -> ResolverIdentity:
    return ResolverIdentity(
        identity_key=f"{game}:demo",
        game=game,
        card_id=1,
        set_id=10,
        set_code="SET",
        card_name=name,
        collector_number=collector,
        variant="default",
        print_ids=(1, 2),
        pending_print_ids=(2,),
        languages=("en", "ja"),
        finishes=("nonfoil", "foil"),
        rarities=("rare",),
        known_product_ids=tuple(known),
        mtg_source_product_ids=tuple(source),
    )


def indexes(game: str, rows: list[ProductListRow]):
    products = build_catalog_products(game, rows)
    by_id = {item.product_id: item for item in products}
    by_expansion = {}
    for item in products:
        by_expansion.setdefault(item.expansion_id, []).append(item)
    return by_id, by_expansion


def test_official_redirect_paths_are_idproduct_based():
    assert cardmarket_redirect_url("mtg", 2) == "https://www.cardmarket.com/en/Magic/Products?idProduct=2"
    assert cardmarket_redirect_url("pokemon", 123).endswith("/Pokemon/Products?idProduct=123")
    assert cardmarket_redirect_url("yugioh", 456).endswith("/YuGiOh/Products?idProduct=456")
    assert cardmarket_redirect_url("onepiece", 789).endswith("/OnePiece/Products?idProduct=789")


def test_onepiece_collector_is_preserved_and_duplicate_products_fail_closed():
    base, collector = split_terminal_collector("Roronoa Zoro (OP01-001)")
    assert base == "Roronoa Zoro"
    assert collector == "OP01-001"

    by_id, by_expansion = indexes(
        "onepiece",
        [
            product("690368", "Roronoa Zoro (OP01-001)", "5229"),
            product("690369", "Roronoa Zoro (OP01-001)", "5229"),
        ],
    )
    result = resolve_identity(
        identity("onepiece", name="Roronoa Zoro", collector="OP01-001"),
        products_by_id=by_id,
        products_by_expansion=by_expansion,
        trusted_expansion_ids=("5229",),
    )
    assert result.category == "AMBIGUOUS"
    assert result.method == "C_multiple_exact_products"
    assert result.candidate_ids == ("690368", "690369")


def test_onepiece_unique_collector_match_is_exact():
    by_id, by_expansion = indexes(
        "onepiece",
        [product("690370", "Usopp (OP01-004)", "5229")],
    )
    result = resolve_identity(
        identity("onepiece", name="Usopp", collector="OP01-004"),
        products_by_id=by_id,
        products_by_expansion=by_expansion,
        trusted_expansion_ids=("5229",),
    )
    assert result.category == "EXACT"
    assert result.product_id == "690370"


def test_pokemon_descriptor_is_not_destroyed_and_base_only_requires_review():
    base, descriptor = split_pokemon_descriptor("Weedle [Multiply]")
    assert base == "Weedle"
    assert descriptor == "Multiply"

    by_id, by_expansion = indexes(
        "pokemon",
        [product("273532", "Weedle [Multiply]", "100")],
    )
    result = resolve_identity(
        identity("pokemon", name="Weedle"),
        products_by_id=by_id,
        products_by_expansion=by_expansion,
        trusted_expansion_ids=("100",),
    )
    assert result.category == "UNIQUE_HIGH_CONFIDENCE"
    assert result.method == "D_pokemon_unique_base_name_in_expansion"
    assert result.product_id == "273532"


def test_pokemon_multiple_descriptors_are_ambiguous():
    by_id, by_expansion = indexes(
        "pokemon",
        [
            product("1", "Weedle [Multiply]", "100"),
            product("2", "Weedle [Poison Sting]", "100"),
        ],
    )
    result = resolve_identity(
        identity("pokemon", name="Weedle"),
        products_by_id=by_id,
        products_by_expansion=by_expansion,
        trusted_expansion_ids=("100",),
    )
    assert result.category == "AMBIGUOUS"
    assert result.method == "D_pokemon_multiple_descriptor_products"


def test_existing_exact_sibling_wins_without_reconstructing_slug():
    by_id, by_expansion = indexes("yugioh", [product("101788", '"A" Cell Breeding Device', "200")])
    result = resolve_identity(
        identity("yugioh", name='"A" Cell Breeding Device', known=("101788",)),
        products_by_id=by_id,
        products_by_expansion=by_expansion,
        trusted_expansion_ids=(),
    )
    assert result.category == "EXACT"
    assert result.method == "A_existing_exact_or_sibling"
    assert result.product_id == "101788"


def test_mtg_scryfall_cardmarket_id_is_exact_when_current_catalog_contains_it():
    by_id, by_expansion = indexes("mtg", [product("2", "Arrest", "45")])
    result = resolve_identity(
        identity("mtg", name="Arrest", source=("2",)),
        products_by_id=by_id,
        products_by_expansion=by_expansion,
        trusted_expansion_ids=(),
    )
    assert result.category == "EXACT"
    assert result.method == "B_scryfall_cardmarket_id"
    assert result.product_id == "2"


def test_no_crosswalk_fails_closed_instead_of_global_fuzzy_match():
    by_id, by_expansion = indexes("yugioh", [product("1", "Blue-Eyes White Dragon", "200")])
    result = resolve_identity(
        identity("yugioh", name="Blue Eyes White Dragon"),
        products_by_id=by_id,
        products_by_expansion=by_expansion,
        trusted_expansion_ids=(),
    )
    assert result.category == "UNMATCHED"
    assert result.method == "C_no_trusted_expansion_crosswalk"
