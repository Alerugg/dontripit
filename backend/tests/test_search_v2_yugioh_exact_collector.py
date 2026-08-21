from types import SimpleNamespace

from app.search_v2.yugioh_exact_collector import _collector_code, exact_yugioh_collector_search


def test_yugioh_collector_normalization_is_strict():
    assert _collector_code("LOB-001") == "lob-001"
    assert _collector_code("LOB 001") == "lob-001"
    assert _collector_code("DUEA_EN045") == "duea-en045"
    assert _collector_code("Blue-Eyes White Dragon") is None
    assert _collector_code("Porygon-Z") is None


def test_yugioh_exact_path_only_claims_yugioh():
    session = SimpleNamespace(bind=SimpleNamespace(dialect=SimpleNamespace(name="postgresql")))
    assert exact_yugioh_collector_search(session, query="LOB-001", game="pokemon") is None
    assert exact_yugioh_collector_search(session, query="Blue-Eyes White Dragon", game="yugioh") is None


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

    def execute(self, _sql, params):
        self.params = params
        return _Result([])


def test_yugioh_exact_collector_fails_closed_and_preserves_language_filter():
    session = _FakeSession()
    result = exact_yugioh_collector_search(
        session,
        query="LOB 001",
        game="yugioh",
        language="es,ja",
        limit=24,
    )
    assert result == []
    assert session.params["q_code"] == "lob-001"
    assert session.params["display_language"] == "es,ja"
