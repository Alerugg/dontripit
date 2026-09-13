from app.routes import search_v2 as route
from app.search_v2.onepiece_query import _plain_name_query


def test_exact_identifier_wins_before_yugioh_fast_path(monkeypatch):
    exact = [{"print_id": 123}]
    monkeypatch.setattr(route, "_exact_identifier_for_game", lambda *args, **kwargs: exact)
    monkeypatch.setattr(
        route,
        "fast_yugioh_name_search",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("fast path must not run")),
    )

    assert route._normal_search_for_game(
        object(), query="LOB-001", game="yugioh", limit=24, language=None
    ) is exact


def test_yugioh_default_language_uses_fast_path(monkeypatch):
    fast = [{"card_id": 1}]
    monkeypatch.setattr(route, "_exact_identifier_for_game", lambda *args, **kwargs: None)
    monkeypatch.setattr(route, "fast_yugioh_name_search", lambda *args, **kwargs: fast)
    monkeypatch.setattr(
        route,
        "normal_yugioh_search",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("slow path must not run")),
    )

    assert route._normal_search_for_game(
        object(), query="Blu-Eyes Wite Dragon", game="yugioh", limit=24, language=None
    ) is fast


def test_yugioh_explicit_language_preserves_localized_engine(monkeypatch):
    localized = [{"card_id": 2}]
    monkeypatch.setattr(route, "_exact_identifier_for_game", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        route,
        "fast_yugioh_name_search",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("default fast path must not run")),
    )
    monkeypatch.setattr(route, "normal_yugioh_search", lambda *args, **kwargs: localized)

    assert route._normal_search_for_game(
        object(), query="Dragón Blanco", game="yugioh", limit=24, language="es"
    ) is localized


def test_onepiece_plain_name_uses_fast_path(monkeypatch):
    fast = [{"card_id": 3}]
    monkeypatch.setattr(route, "_exact_identifier_for_game", lambda *args, **kwargs: None)
    monkeypatch.setattr(route, "fast_onepiece_name_search", lambda *args, **kwargs: fast)
    monkeypatch.setattr(
        route,
        "normal_search",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("generic engine must not run")),
    )

    assert route._normal_search_for_game(
        object(), query="Lufy", game="onepiece", limit=24
    ) is fast


def test_onepiece_structured_intent_is_not_classified_as_plain_name():
    assert _plain_name_query("Luffy red leader") is None
    assert _plain_name_query("Luffy cost 5") is None
    assert _plain_name_query("OP05 Luffy") is None


def test_onepiece_free_text_keeps_real_name_terms():
    parsed = _plain_name_query("Lufy")
    assert parsed is not None
    normalized, tokens = parsed
    assert normalized == "lufy"
    assert "lufy" in tokens

    gear = _plain_name_query("Gear 5 Luffy")
    assert gear is not None
    assert gear[0] == "gear 5 luffy"
