from __future__ import annotations

import pytest
import requests

from app.ingest.connectors.tcgdex_pokemon_multilingual_physical import (
    PhysicalMultilingualTcgdexPokemonConnector,
)


def _json_response(url: str, payload: str = '{"ok": true}', status: int = 200) -> requests.Response:
    response = requests.Response()
    response.status_code = status
    response.url = url
    response._content = payload.encode("utf-8")
    response.encoding = "utf-8"
    return response


@pytest.mark.parametrize(
    "transport_error",
    [
        requests.exceptions.ReadTimeout("read timed out"),
        requests.exceptions.ConnectionError("connection dropped"),
    ],
)
def test_physical_request_retries_transient_transport_failure(monkeypatch, transport_error):
    connector = PhysicalMultilingualTcgdexPokemonConnector()
    url = "https://api.tcgdex.net/v2/en/sets/base1"
    calls = []
    outcomes = [transport_error, _json_response(url)]

    def fake_get(request_url, params=None, timeout=None):
        calls.append((request_url, params, timeout))
        outcome = outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(requests, "get", fake_get)
    monkeypatch.setattr("time.sleep", lambda _seconds: None)

    assert connector._request_json(url) == {"ok": True}
    assert len(calls) == 2
    assert calls == [(url, None, 30), (url, None, 30)]


def test_physical_request_transport_retry_is_bounded(monkeypatch):
    connector = PhysicalMultilingualTcgdexPokemonConnector()
    url = "https://api.tcgdex.net/v2/en/sets/base1"
    calls = []

    def always_timeout(request_url, params=None, timeout=None):
        calls.append((request_url, params, timeout))
        raise requests.exceptions.ReadTimeout("read timed out")

    monkeypatch.setattr(requests, "get", always_timeout)
    monkeypatch.setattr("time.sleep", lambda _seconds: None)

    with pytest.raises(requests.exceptions.ReadTimeout):
        connector._request_json(url)

    assert len(calls) == 3
    assert calls == [(url, None, 30), (url, None, 30), (url, None, 30)]


def test_physical_request_does_not_retry_non_transient_http_error(monkeypatch):
    connector = PhysicalMultilingualTcgdexPokemonConnector()
    url = "https://api.tcgdex.net/v2/en/sets/not-real"
    calls = []

    def not_found(request_url, params=None, timeout=None):
        calls.append((request_url, params, timeout))
        return _json_response(request_url, payload='{"error": "not found"}', status=404)

    monkeypatch.setattr(requests, "get", not_found)
    monkeypatch.setattr("time.sleep", lambda _seconds: None)

    with pytest.raises(requests.HTTPError):
        connector._request_json(url)

    assert calls == [(url, None, 30)]
