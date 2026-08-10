from __future__ import annotations

from app.search_v2.advanced import ONEPIECE_ALLOWED_FILTERS
from app.search_v2.facets import facets_for_game
from app.search_v2.mtg_advanced import ALLOWED_FILTERS as MTG_ALLOWED_FILTERS
from app.search_v2.mtg_facets import mtg_facets
from app.search_v2.pokemon_advanced import POKEMON_ALLOWED_FILTERS
from app.search_v2.yugioh_advanced import ALLOWED_FILTERS as YUGIOH_ALLOWED_FILTERS
from app.search_v2.yugioh_facets import yugioh_facets


def _active_keys(rows: list[dict]) -> set[str]:
    return {row["key"] for row in rows if row.get("active", True) and row.get("filterable", True)}


def test_every_advertised_onepiece_filter_is_executable():
    assert _active_keys(facets_for_game("onepiece")) <= ONEPIECE_ALLOWED_FILTERS
    assert "manga" not in ONEPIECE_ALLOWED_FILTERS
    assert "illustration_type" not in ONEPIECE_ALLOWED_FILTERS


def test_every_advertised_pokemon_filter_is_executable():
    assert _active_keys(facets_for_game("pokemon")) <= POKEMON_ALLOWED_FILTERS


def test_every_advertised_yugioh_filter_is_executable():
    assert _active_keys(yugioh_facets()) <= YUGIOH_ALLOWED_FILTERS
    assert "banlist" not in YUGIOH_ALLOWED_FILTERS


def test_every_advertised_mtg_filter_is_executable():
    assert _active_keys(mtg_facets()) == MTG_ALLOWED_FILTERS


def test_filter_surfaces_remain_game_specific():
    onepiece = _active_keys(facets_for_game("onepiece"))
    pokemon = _active_keys(facets_for_game("pokemon"))
    yugioh = _active_keys(yugioh_facets())
    mtg = _active_keys(mtg_facets())

    assert {"power", "counter", "traits"} <= onepiece
    assert {"hp", "dex_id", "regulation_mark"} <= pokemon
    assert {"atk", "def", "link_marker"} <= yugioh
    assert {"mana_value", "color_identity", "reserved"} <= mtg
