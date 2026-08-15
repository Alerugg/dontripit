from app.routes.set_ui import _normalized_region, _resolve_canonical_set


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


class _Session:
    def __init__(self, rows):
        self.rows = rows
        self.params = None

    def execute(self, _statement, params):
        self.params = params
        rows = [row for row in self.rows if params.get("region") is None or row["region"].lower() == params["region"]]
        return _Result(rows)


def test_region_normalization_accepts_physical_region_codes():
    assert _normalized_region(" JP ") == "jp"
    assert _normalized_region("ocg-jp") == "ocg-jp"
    assert _normalized_region("") is None


def test_legacy_lookup_prefers_single_global_set_without_unioning_regional_set():
    session = _Session([
        {"id": 10, "code": "DL2", "name": "Legacy TCG", "region": "global"},
        {"id": 20, "code": "DL2", "name": "Japanese OCG", "region": "jp"},
    ])

    resolved = _resolve_canonical_set(session, game="yugioh", requested_code="DL2")

    assert resolved == {"id": 10, "code": "DL2", "name": "Legacy TCG", "region": "global"}
    assert session.params == {"game": "yugioh", "set_code": "dl2", "region": None}


def test_explicit_jp_lookup_selects_only_japanese_physical_set():
    session = _Session([
        {"id": 10, "code": "DL2", "name": "Legacy TCG", "region": "global"},
        {"id": 20, "code": "DL2", "name": "Japanese OCG", "region": "jp"},
    ])

    resolved = _resolve_canonical_set(
        session,
        game="yugioh",
        requested_code="DL2",
        requested_region="jp",
    )

    assert resolved == {"id": 20, "code": "DL2", "name": "Japanese OCG", "region": "jp"}


def test_lookup_without_global_rejects_multiple_regional_candidates():
    session = _Session([
        {"id": 20, "code": "DL2", "name": "Japanese OCG", "region": "jp"},
        {"id": 30, "code": "DL2", "name": "Korean OCG", "region": "kr"},
    ])

    resolved = _resolve_canonical_set(session, game="yugioh", requested_code="DL2")

    assert resolved["ambiguous"] is True
    assert {row["region"] for row in resolved["matches"]} == {"jp", "kr"}
