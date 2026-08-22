from __future__ import annotations

from types import SimpleNamespace

from app.search_v2.pokemon_query import normal_pokemon_search


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


def test_pokemon_fuzzy_search_caps_family_reranking_close_to_page_size():
    session = _Session()

    result = normal_pokemon_search(session, query="Pikchu", limit=24)

    assert result == []
    assert session.params["candidate_limit"] == 96
    assert session.params["limit"] == 24
    assert session.params["candidate_limit"] <= session.params["limit"] * 4


def test_pokemon_fuzzy_keeps_trigram_recovery_and_prefix_family_tiebreaker():
    session = _Session()

    normal_pokemon_search(session, query="Pikchu", limit=24)

    sql = session.sql
    assert "similarity(csp.normalized_name, :q_norm) >= 0.20" in sql
    assert "WHEN normalized_name LIKE :prefix THEN" in sql
    assert "related.normalized_name = pre_candidates.normalized_name" in sql
    assert "related.normalized_name LIKE pre_candidates.normalized_name || ' %'" in sql
    assert "COUNT(*) OVER (\n              PARTITION BY csp.game_id, csp.normalized_name" not in sql
    assert "LIMIT :candidate_limit" in sql


def test_pokemon_typo_family_count_is_guarded_by_prefix_case():
    session = _Session()

    normal_pokemon_search(session, query="Pikchu", limit=24)

    sql = session.sql
    family_count = sql.index("SELECT COUNT(*)\n                      FROM card_search_profiles related")
    prefix_guard = sql.index("WHEN normalized_name LIKE :prefix THEN")
    else_guard = sql.index("ELSE 0.0", prefix_guard)
    assert prefix_guard < family_count < else_guard
