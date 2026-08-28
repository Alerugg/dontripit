from types import SimpleNamespace

from app.search_v2.exact_identifier import (
    _set_collector_parts,
    _structured_code,
    exact_structured_identifier_search,
)


def test_structured_identifier_normalization_is_strict():
    assert _structured_code("base1-4") == "base1-4"
    assert _structured_code("BASE1 4") == "base1-4"
    assert _structured_code("lea_1") == "lea-1"
    assert _structured_code("Pikachu") is None
    assert _structured_code("Porygon-Z") is None


def test_set_collector_parts_preserve_indexable_components():
    assert _set_collector_parts("lea-1") == ("lea", "1")
    assert _set_collector_parts("svp-202") == ("svp", "202")
    assert _set_collector_parts("bad") is None


def test_exact_identifier_only_claims_supported_games():
    session = SimpleNamespace(bind=SimpleNamespace(dialect=SimpleNamespace(name="postgresql")))
    assert exact_structured_identifier_search(session, query="LOB-001", game="yugioh") is None
    assert exact_structured_identifier_search(session, query="P-135", game="onepiece") is None
    assert exact_structured_identifier_search(session, query="Pikachu", game="pokemon") is None


class _Mappings:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return _Mappings(self._rows)


class _FakeSession:
    bind = SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))

    def __init__(self):
        self.params = None
        self.sql = None

    def execute(self, sql, params):
        self.sql = str(sql)
        self.params = params
        return _Result([])


def test_pokemon_identifier_uses_separate_indexed_print_columns_and_fails_closed():
    session = _FakeSession()
    result = exact_structured_identifier_search(
        session,
        query="SVP 202",
        game="pokemon",
        limit=24,
    )
    assert result == []
    assert session.params["game"] == "pokemon"
    assert session.params["set_code"] == "svp"
    assert session.params["collector"] == "202"
    assert "psp.normalized_set_code=:set_code" in session.sql
    assert "psp.normalized_collector_number=:collector" in session.sql
    assert "card_key" not in session.sql.split("WITH ranked_cards AS MATERIALIZED", 1)[1].split(")", 1)[0]
    assert "|| '-' ||" not in session.sql


def test_mtg_set_collector_identifier_uses_separate_indexed_columns():
    session = _FakeSession()
    result = exact_structured_identifier_search(
        session,
        query="LEA 1",
        game="mtg",
        limit=24,
    )
    assert result == []
    assert session.params["game"] == "mtg"
    assert session.params["set_code"] == "lea"
    assert session.params["collector"] == "1"
    assert "psp.normalized_set_code=:set_code" in session.sql
    assert "psp.normalized_collector_number=:collector" in session.sql
    assert "|| '-' ||" not in session.sql
