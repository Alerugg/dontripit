from __future__ import annotations

from datetime import date

import pytest

from app.routes.search_v2 import _yugioh_display_language
from app.search_v2.yugioh_advanced import _display_language as advanced_display_language
from app.search_v2.yugioh_advanced import advanced_yugioh_search
from app.search_v2.yugioh_profiles import iter_yugioh_print_profiles
from app.search_v2.yugioh_query import _display_language as normal_display_language
from app.search_v2.yugioh_query import normal_yugioh_search


class _Dialect:
    name = "postgresql"


class _Bind:
    dialect = _Dialect()


class _Mappings:
    def __init__(self, rows=None):
        self.rows = list(rows or [])

    def all(self):
        return self.rows

    def __iter__(self):
        return iter(self.rows)


class _Result:
    def __init__(self, rows=None):
        self.rows = rows or []

    def mappings(self):
        return _Mappings(self.rows)


class _Session:
    bind = _Bind()

    def __init__(self, rows=None):
        self.statement = None
        self.params = None
        self.rows = rows or []

    def execute(self, statement, params=None):
        self.statement = statement
        self.params = params
        return _Result(self.rows)


def test_route_normalizes_single_all_and_multi_language_values():
    assert _yugioh_display_language(None) is None
    assert _yugioh_display_language("") is None
    assert _yugioh_display_language("all") is None
    assert _yugioh_display_language("English") == "en"
    assert _yugioh_display_language("en,ja") == "en,ja"
    assert _yugioh_display_language(["Español", "jp"]) == "es,ja"
    assert _yugioh_display_language("ja,en,ja") == "ja,en"


def test_route_rejects_unsupported_language_values():
    with pytest.raises(ValueError):
        _yugioh_display_language("en,fr")


def test_search_helpers_preserve_valid_multi_language_csv():
    assert normal_display_language("EN, Japanese") == "en,ja"
    assert advanced_display_language("Spanish,ja") == "es,ja"


def test_normal_search_filters_real_physical_print_language_not_localization_presence():
    session = _Session()
    result = normal_yugioh_search(session, query="Dark Magician", language="en,ja", limit=12)

    assert result == []
    assert session.params["display_language"] == "en,ja"
    sql = str(session.statement)
    assert "lower(coalesce(p.language,''))=ANY(string_to_array(:display_language, ','))" in sql
    assert "lower(coalesce(pp.language,''))=ANY(string_to_array(:display_language, ','))" in sql
    assert "lower(pl.language)=lower(coalesce(p.language,''))" in sql
    assert "lower(plf.language)=ANY" not in sql


def test_japanese_query_keeps_raw_unicode_signal_even_when_ascii_normalization_is_empty():
    session = _Session()
    result = normal_yugioh_search(session, query="ブラック・マジシャン", language="ja", limit=8)

    assert result == []
    assert session.params["q_norm"] == ""
    assert session.params["q_raw"] == "ブラック・マジシャン"
    assert session.params["display_language"] == "ja"
    assert "localized_signal" in str(session.statement)


def test_advanced_search_uses_same_physical_language_contract():
    session = _Session()
    result = advanced_yugioh_search(
        session,
        filters={"language": ["es", "ja"]},
        query="Mago Oscuro",
        language="es,ja",
        limit=24,
        offset=0,
    )

    assert result["items"] == []
    assert result["language"] == "es,ja"
    assert session.params["display_language"] == "es,ja"
    assert session.params["f_language"] == ["es", "ja"]
    sql = str(session.statement)
    assert "lower(coalesce(p.language,''))=ANY(:f_language)" in sql
    assert "lower(coalesce(p.language,''))=ANY(string_to_array(:display_language, ','))" in sql
    assert "cm.cardmarket_price" in sql
    assert "EXISTS (SELECT 1 FROM print_localizations pld" not in sql


def test_print_profile_is_one_physical_print_with_localized_name_and_aggregated_releases():
    row = {
        "print_id": 900001,
        "card_id": 72296,
        "canonical_name": "Dark Magician",
        "yugoprodeck_id": "46986414",
        "set_code": "LB",
        "canonical_set_name": "Legend of Blue Eyes",
        "collector_number": "LB-005",
        "language": "ja",
        "rarity": "Ultra Rare",
        "variant": "ultra-rare",
        "localized_name": "ブラック・マジシャン",
        "localized_set_name": "青眼の白龍伝説",
        "release_names": ["Release A", "Release B"],
        "release_codes": ["A", "B"],
        "first_release_date": date(2000, 5, 18),
    }
    session = _Session([row])

    profiles = list(iter_yugioh_print_profiles(session))

    assert len(profiles) == 1
    profile = profiles[0]
    assert profile["print_id"] == 900001
    assert profile["language"] == "ja"
    # Global normalization is intentionally ASCII-oriented. CJK exact matching
    # is handled by the raw-Unicode localized_signal over print_localizations.
    assert profile["normalized_name"] == ""
    assert profile["release_names_json"] == ["Release A", "Release B"]
    assert profile["aliases_json"] == ["Dark Magician"]
    assert profile["attributes_json"]["release_year"] == 2000
    assert "dark magician" in profile["search_text"]
    assert "release a" in profile["search_text"]

    sql = str(session.statement)
    assert "array_agg(DISTINCT cr.name" in sql
    assert "WHERE pr.print_id=p.id" in sql
    assert "lower(pl.language)=lower(coalesce(p.language,''))" in sql
