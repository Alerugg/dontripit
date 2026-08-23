from types import SimpleNamespace

from app.routes import onepiece_don_search as don_route
from app.search_v2.onepiece_don_query import onepiece_don_market_page


class _EmptyResult:
    def mappings(self):
        return self

    def all(self):
        return []


class _CapturingPostgresSession:
    def __init__(self):
        self.bind = SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))
        self.sql = ""
        self.params = {}

    def execute(self, statement, params):
        self.sql = str(statement)
        self.params = dict(params)
        return _EmptyResult()


def _don_item(subject="Luffy"):
    return {
        "type": "don_market",
        "identity_scope": "source_owned",
        "game": "onepiece",
        "card_id": None,
        "print_id": None,
        "name": f"Don!! ({subject})",
        "subject": subject,
        "collector_number": None,
        "set_code": None,
        "primary_image_url": "https://example.invalid/don.jpg",
        "cardmarket_id_product": "123",
        "cardmarket_price": 4.5,
        "cardmarket_currency": "EUR",
    }


def _page(items):
    return {
        "items": items,
        "total": len(items),
        "limit": 24,
        "offset": 0,
        "has_more": False,
        "next_offset": None,
        "identity_scope": "source_owned",
    }


def test_don_query_filters_by_certified_subject_not_product_name():
    session = _CapturingPostgresSession()

    result = onepiece_don_market_page(session, query="Donquixote", limit=24, offset=0)

    assert result["items"] == []
    assert session.params["query"] == "donquixote"
    assert session.params["contains"] == "%donquixote%"
    assert "m.subject_normalized LIKE :contains" in session.sql
    assert "m.name LIKE :contains" not in session.sql


def test_generic_don_query_lists_all_current_source_owned_dons():
    session = _CapturingPostgresSession()

    onepiece_don_market_page(session, query="DON", limit=24, offset=0)

    assert session.params["query"] == "don"
    assert "AND (TRUE)" in session.sql


def test_don_filter_endpoint_is_onepiece_only_and_keeps_source_owned_identity(client, monkeypatch):
    calls = []

    def fake_page(session, *, query, limit, offset):
        calls.append({"query": query, "limit": limit, "offset": offset})
        return _page([_don_item("Luffy")])

    monkeypatch.setattr(don_route, "onepiece_don_market_page", fake_page)

    response = client.get("/api/v2/search/don?q=Luffy&game=onepiece&limit=24")
    assert response.status_code == 200
    body = response.get_json()
    assert body["don_only"] is True
    assert body["game"] == "onepiece"
    assert body["identity_scope"] == "source_owned"
    assert body["pagination_mode"] == "onepiece_don_source_owned"
    assert [item["subject"] for item in body["items"]] == ["Luffy"]
    assert all(item["type"] == "don_market" for item in body["items"])
    assert all(item["card_id"] is None and item["print_id"] is None for item in body["items"])
    assert calls == [{"query": "Luffy", "limit": 24, "offset": 0}]

    wrong_game = client.get("/api/v2/search/don?q=Luffy&game=pokemon")
    assert wrong_game.status_code == 422
    assert wrong_game.get_json()["error"] == "don_search_only_supports_onepiece"
    assert len(calls) == 1


def test_don_filter_suggestions_are_source_owned_and_subject_scoped(client, monkeypatch):
    def fake_page(session, *, query, limit, offset):
        assert query == "Luf"
        assert limit == 8
        assert offset == 0
        return _page([_don_item("Luffy")])

    monkeypatch.setattr(don_route, "onepiece_don_market_page", fake_page)

    response = client.get("/api/v2/search/don/suggest?q=Luf&limit=8")
    assert response.status_code == 200
    body = response.get_json()
    assert body["don_only"] is True
    assert body["identity_scope"] == "source_owned"
    assert body["items"] == [
        {
            "type": "don_market",
            "identity_scope": "source_owned",
            "card_id": None,
            "print_id": None,
            "name": "Don!! (Luffy)",
            "subject": "Luffy",
            "game": "onepiece",
            "collector_number": None,
            "set_code": None,
            "image_url": "https://example.invalid/don.jpg",
            "cardmarket_id_product": "123",
            "cardmarket_price": 4.5,
            "cardmarket_currency": "EUR",
        }
    ]
