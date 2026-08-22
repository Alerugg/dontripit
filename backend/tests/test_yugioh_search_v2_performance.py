from __future__ import annotations

from types import SimpleNamespace

from app.search_v2.yugioh_query import normal_yugioh_search


class _Mappings:
    def all(self):
        return []


class _Result:
    def mappings(self):
        return _Mappings()


class _Session:
    bind = SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))

    def __init__(self):
        self.sql = None
        self.params = None

    def execute(self, statement, params=None):
        self.sql = str(statement)
        self.params = params or {}
        return _Result()


def test_yugioh_fuzzy_search_caps_candidate_hydration_close_to_page_size():
    session = _Session()

    result = normal_yugioh_search(
        session,
        query="Blu-Eyes Wite Dragon",
        limit=24,
    )

    assert result == []
    assert session.params["candidate_limit"] == 96
    assert session.params["limit"] == 24
    assert session.params["candidate_limit"] <= session.params["limit"] * 4


def test_yugioh_localized_contains_predicate_matches_trigram_index_expression():
    session = _Session()

    normal_yugioh_search(
        session,
        query="Mago Oscuro",
        language="es",
        limit=12,
    )

    assert session.params["localized_contains"] == "%mago oscuro%"
    sql = session.sql
    assert "AND lower(pl.card_name) LIKE :localized_contains" in sql
    assert "position(:q_raw in lower(pl.card_name)) > 0" in sql  # scoring remains exact after indexed filtering
