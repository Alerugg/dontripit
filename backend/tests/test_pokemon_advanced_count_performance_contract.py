from __future__ import annotations

from app.search_v2.pokemon_advanced import advanced_pokemon_search


class _Dialect:
    name = "postgresql"


class _Bind:
    dialect = _Dialect()


class _Mappings:
    def all(self):
        return []


class _Result:
    def __init__(self, scalar=None):
        self._scalar = scalar

    def scalar_one(self):
        return self._scalar

    def mappings(self):
        return _Mappings()


class _CaptureSession:
    bind = _Bind()

    def __init__(self):
        self.statements: list[str] = []

    def execute(self, statement, params=None):
        sql = str(statement)
        self.statements.append(sql)
        if "SELECT id FROM games WHERE slug='pokemon'" in sql:
            return _Result(1)
        if sql.lstrip().startswith("SELECT COUNT(*)"):
            return _Result(0)
        return _Result()


def _run(*, has_price: bool) -> list[str]:
    session = _CaptureSession()
    result = advanced_pokemon_search(
        session,
        filters={"finish": "holo"},
        query="Pikachu",
        sort="relevance",
        has_price=has_price,
        limit=24,
        offset=0,
    )
    assert result["total"] == 0
    assert result["items"] == []
    return session.statements


def test_ordinary_count_does_not_pay_cardmarket_lateral_join():
    statements = _run(has_price=False)
    count_sql = next(sql for sql in statements if sql.lstrip().startswith("SELECT COUNT(*)"))
    result_sql = statements[-1]

    assert "external_catalog_print_links" not in count_sql
    assert "price_snapshots" not in count_sql
    assert "external_catalog_print_links" in result_sql
    assert "price_snapshots" in result_sql


def test_has_price_count_keeps_cardmarket_join_and_price_predicate():
    statements = _run(has_price=True)
    count_sql = next(sql for sql in statements if sql.lstrip().startswith("SELECT COUNT(*)"))

    assert "external_catalog_print_links" in count_sql
    assert "price_snapshots" in count_sql
    assert "cm.cardmarket_price IS NOT NULL" in count_sql
