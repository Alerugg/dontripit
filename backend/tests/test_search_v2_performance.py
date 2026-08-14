from types import SimpleNamespace

from app.search_v2.advanced import advanced_onepiece_search


class _Result:
    def scalar_one(self):
        return 0

    def mappings(self):
        return self

    def all(self):
        return []


class _Session:
    def __init__(self):
        self.bind = SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))
        self.statements = []

    def execute(self, statement, params=None):
        self.statements.append(str(statement))
        return _Result()


def test_unpriced_advanced_count_does_not_join_cardmarket():
    session = _Session()

    result = advanced_onepiece_search(
        session,
        query="Luffy",
        filters={},
        sort="relevance",
        has_price=False,
        limit=24,
        offset=0,
    )

    assert result["total"] == 0
    assert len(session.statements) == 2
    count_sql, rows_sql = session.statements
    assert "SELECT COUNT(*)" in count_sql
    assert "external_catalog_print_links" not in count_sql
    assert "price_snapshots" not in count_sql
    assert "external_catalog_print_links" in rows_sql
    assert "price_snapshots" in rows_sql


def test_priced_advanced_count_keeps_cardmarket_eligibility_join():
    session = _Session()

    advanced_onepiece_search(
        session,
        query="Luffy",
        filters={},
        sort="relevance",
        has_price=True,
        limit=24,
        offset=0,
    )

    count_sql = session.statements[0]
    assert "SELECT COUNT(*)" in count_sql
    assert "external_catalog_print_links" in count_sql
    assert "price_snapshots" in count_sql
    assert "cm.cardmarket_price IS NOT NULL" in count_sql
