from __future__ import annotations

import pytest

from app.routes.search_v2 import _yugioh_display_language
from app.search_v2.yugioh_advanced import _display_language as advanced_display_language
from app.search_v2.yugioh_advanced import advanced_yugioh_search
from app.search_v2.yugioh_query import _display_language as normal_display_language
from app.search_v2.yugioh_query import normal_yugioh_search


class _Dialect:
    name = "postgresql"


class _Bind:
    dialect = _Dialect()


class _Mappings:
    def all(self):
        return []


class _Result:
    def mappings(self):
        return _Mappings()


class _Session:
    bind = _Bind()

    def __init__(self):
        self.statement = None
        self.params = None

    def execute(self, statement, params):
        self.statement = statement
        self.params = params
        return _Result()


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


def test_normal_search_filters_localizations_with_any_selected_language():
    session = _Session()
    result = normal_yugioh_search(
        session,
        query="Dark Magician",
        language="en,ja",
        limit=12,
    )

    assert result == []
    assert session.params["display_language"] == "en,ja"
    sql = str(session.statement)
    assert "print_localizations" in sql
    assert "string_to_array(:display_language, ',')" in sql
    assert "lower(plf.language)=ANY" in sql


def test_japanese_query_keeps_raw_unicode_signal_even_when_ascii_normalization_is_empty():
    session = _Session()
    result = normal_yugioh_search(
        session,
        query="ブラック・マジシャン",
        language="ja",
        limit=8,
    )

    assert result == []
    assert session.params["q_norm"] == ""
    assert session.params["q_raw"] == "ブラック・マジシャン"
    assert session.params["display_language"] == "ja"
    assert "localized_signal" in str(session.statement)


def test_advanced_search_uses_same_multi_language_localization_contract():
    session = _Session()
    result = advanced_yugioh_search(
        session,
        filters={},
        query="Mago Oscuro",
        language="es,ja",
        limit=24,
        offset=0,
    )

    assert result["items"] == []
    assert result["language"] == "es,ja"
    assert session.params["display_language"] == "es,ja"
    sql = str(session.statement)
    assert "print_localizations" in sql
    assert "string_to_array(:display_language, ',')" in sql
    assert "cm.cardmarket_price" in sql
