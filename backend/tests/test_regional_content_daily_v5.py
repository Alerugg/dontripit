from types import SimpleNamespace

import pytest

from scripts.sync_regional_content_daily_v5 import _find_current_row, _same_identity


class _Mappings:
    def __init__(self, rows):
        self.rows = rows

    def one_or_none(self):
        assert len(self.rows) <= 1
        return self.rows[0] if self.rows else None

    def all(self):
        return list(self.rows)


class _Result:
    def __init__(self, rows):
        self.rows = rows

    def mappings(self):
        return _Mappings(self.rows)


class _FakeConn:
    dialect = SimpleNamespace(name="postgresql")

    def __init__(self, legacy_rows):
        self.legacy_rows = legacy_rows

    def execute(self, statement, params=None):
        sql = str(statement)
        if "WHERE source_key =" in sql:
            return _Result([])
        if "WHERE source =" in sql:
            return _Result(self.legacy_rows)
        raise AssertionError(sql)


def _record():
    return {
        "game": "pokemon",
        "region": "eu",
        "source_key": "pokemon_eu_pokemon_uk",
        "item_url": "https://www.pokemon.com/uk/news/example",
    }


def _legacy_row(**overrides):
    row = {
        "id": 17,
        "game_id": 1,
        "region": "eu",
        "locale": "en-GB",
        "kind": "news",
        "source_key": "legacy-shape",
        "source_name": "Pokemon",
        "source_url": "https://www.pokemon.com/uk/news",
        "item_url": "legacy-item-shape",
        "title": "Example",
        "published_date": None,
        "release_date": None,
        "raw_json": {},
    }
    row.update(overrides)
    return row


def test_exact_legacy_source_url_collision_is_adopted_only_for_same_game_and_region():
    current, origin = _find_current_row(
        _FakeConn([_legacy_row()]),
        _record(),
        expected_game_id=1,
        has_legacy_identity=True,
    )
    assert origin == "legacy"
    assert current["id"] == 17
    assert _same_identity(current, _record()) is False


def test_legacy_collision_across_region_fails_closed():
    with pytest.raises(RuntimeError, match="Refusing to adopt legacy regional row"):
        _find_current_row(
            _FakeConn([_legacy_row(region="us")]),
            _record(),
            expected_game_id=1,
            has_legacy_identity=True,
        )


def test_legacy_collision_across_game_fails_closed():
    with pytest.raises(RuntimeError, match="Refusing to adopt legacy regional row"):
        _find_current_row(
            _FakeConn([_legacy_row(game_id=99)]),
            _record(),
            expected_game_id=1,
            has_legacy_identity=True,
        )
