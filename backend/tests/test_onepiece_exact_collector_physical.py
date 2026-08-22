from __future__ import annotations

from types import SimpleNamespace

from app.search_v2.onepiece_exact_collector import exact_onepiece_collector_search


class _Rows:
    def all(self):
        return []


class _Session:
    def __init__(self):
        self.statement = None

    def execute(self, statement):
        self.statement = statement
        return _Rows()


def test_exact_onepiece_collector_starts_from_physical_print_and_left_joins_profile():
    session = _Session()

    result = exact_onepiece_collector_search(
        session,
        query="P-150",
        game="onepiece",
        limit=24,
    )

    assert result == []
    sql = str(session.statement)
    assert "FROM prints JOIN cards" in sql
    assert "LEFT OUTER JOIN print_search_profiles" in sql
    assert "cards.card_key" in sql
    assert "FROM print_search_profiles JOIN prints" not in sql


def test_exact_onepiece_collector_rejects_non_collector_text_without_querying():
    session = SimpleNamespace(execute=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not query")))
    assert exact_onepiece_collector_search(session, query="Luffy", game="onepiece") is None
