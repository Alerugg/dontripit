import pytest
import requests

from app.ingest.connectors.tcgdex_pokemon_multilingual_physical import (
    PhysicalMultilingualTcgdexPokemonConnector,
)
from app.ingest.registry import get_connector


class FakePhysicalConnector(PhysicalMultilingualTcgdexPokemonConnector):
    def __init__(self, payloads):
        super().__init__()
        self.payloads = payloads
        self.calls = []

    def _request_json(self, url: str, params=None):
        self.calls.append(url)
        if url not in self.payloads:
            raise AssertionError(f"Unexpected TCGdex request: {url}")
        value = self.payloads[url]
        if isinstance(value, Exception):
            raise value
        return value


def _not_found(url: str) -> requests.HTTPError:
    response = requests.Response()
    response.status_code = 404
    response.url = url
    return requests.HTTPError(f"404 Client Error: Not Found for url: {url}", response=response)


def _payloads():
    base = "https://api.tcgdex.net/v2/en"
    return {
        f"{base}/series": [
            {"id": "base", "name": "Base"},
            {"id": "tcgp", "name": "Pokémon TCG Pocket"},
            {"id": "swsh", "name": "Sword & Shield"},
        ],
        f"{base}/series/tcgp": {
            "id": "tcgp",
            "name": "Pokémon TCG Pocket",
            "sets": [
                {"id": "A1", "name": "Genetic Apex"},
                {"id": "P-A", "name": "Promo-A"},
            ],
        },
        f"{base}/sets": [
            {"id": "A1", "name": "Genetic Apex"},
            {"id": "P-A", "name": "Promo-A"},
            {"id": "swsh1", "name": "Sword & Shield"},
        ],
        f"{base}/sets/swsh1": {
            "id": "swsh1",
            "name": "Sword & Shield",
            "abbreviation": "SSH",
            "releaseDate": "2020-02-07",
            "cards": [
                {
                    "id": "swsh1-1",
                    "localId": "1",
                    "name": "Celebi",
                    "image": "https://assets.tcgdex.net/en/swsh/swsh1/1",
                }
            ],
        },
    }


def test_full_remote_load_excludes_all_tcgp_sets_before_fetching_details():
    connector = FakePhysicalConnector(_payloads())

    rows = connector._load_remote(lang="en")

    assert len(rows) == 1
    assert rows[0]["id"] == "swsh1-1"
    assert rows[0]["set"]["id"] == "swsh1"
    assert all("/sets/A1" not in url for url in connector.calls)
    assert all("/sets/P-A" not in url for url in connector.calls)
    assert "https://api.tcgdex.net/v2/en/sets/swsh1" in connector.calls


def test_explicit_tcgp_set_is_rejected_without_fetching_set_detail():
    connector = FakePhysicalConnector(_payloads())

    rows = connector._load_remote(set_id="A1", lang="en")

    assert rows == []
    assert connector.calls == [
        "https://api.tcgdex.net/v2/en/series",
        "https://api.tcgdex.net/v2/en/series/tcgp",
    ]


def test_tcgp_exclusion_guard_fails_closed_when_published_series_has_no_sets():
    base = "https://api.tcgdex.net/v2/en"
    connector = FakePhysicalConnector(
        {
            f"{base}/series": [{"id": "tcgp", "name": "Pokémon TCG Pocket"}],
            f"{base}/series/tcgp": {"id": "tcgp", "sets": []},
        }
    )

    with pytest.raises(RuntimeError, match="TCG Pocket exclusion guard failed"):
        connector._load_remote(lang="en")


def test_language_without_tcgp_series_continues_without_requesting_missing_endpoint():
    base = "https://api.tcgdex.net/v2/ja"
    connector = FakePhysicalConnector(
        {
            f"{base}/series": [{"id": "neo", "name": "Neo"}],
            f"{base}/sets": [{"id": "neo1", "name": "Gold, Silver, to a New World..."}],
            f"{base}/sets/neo1": {
                "id": "neo1",
                "name": "Gold, Silver, to a New World...",
                "releaseDate": "2000-02-04",
                "cards": [
                    {
                        "id": "neo1-1",
                        "localId": "1",
                        "name": "Japanese card",
                        "image": "https://assets.tcgdex.net/ja/neo/neo1/1",
                    }
                ],
            },
        }
    )

    rows = connector._load_remote(lang="ja")

    assert len(rows) == 1
    assert rows[0]["_language"] == "ja"
    assert f"{base}/series/tcgp" not in connector.calls
    assert connector.calls == [f"{base}/series", f"{base}/sets", f"{base}/sets/neo1"]


def test_listed_set_with_404_detail_recovers_cards_from_global_card_list():
    base = "https://api.tcgdex.net/v2/ja"
    broken_url = f"{base}/sets/SM1+"
    connector = FakePhysicalConnector(
        {
            f"{base}/series": [{"id": "sm", "name": "Sun & Moon"}],
            f"{base}/sets": [
                {"id": "SM1+", "name": "強化拡張パック サン&ムーン"},
                {"id": "SM2K", "name": "キミを待つ島々"},
            ],
            broken_url: _not_found(broken_url),
            f"{base}/cards": [
                {
                    "id": "SM1+-001",
                    "localId": "001",
                    "name": "Japanese recovered card 1",
                    "image": "https://assets.tcgdex.net/ja/sm/SM1+/001",
                },
                {
                    "id": "SM1+-002",
                    "localId": "002",
                    "name": "Japanese recovered card 2",
                    "image": "https://assets.tcgdex.net/ja/sm/SM1+/002",
                },
                {
                    "id": "SM2K-001",
                    "localId": "001",
                    "name": "Other set card",
                    "image": "https://assets.tcgdex.net/ja/sm/SM2K/001",
                },
            ],
            f"{base}/sets/SM2K": {
                "id": "SM2K",
                "name": "キミを待つ島々",
                "cards": [
                    {
                        "id": "SM2K-001",
                        "localId": "001",
                        "name": "Other set card",
                        "image": "https://assets.tcgdex.net/ja/sm/SM2K/001",
                    }
                ],
            },
        }
    )

    rows = connector._load_remote(lang="ja")

    assert [row["id"] for row in rows] == ["SM1+-001", "SM1+-002", "SM2K-001"]
    recovered = rows[:2]
    assert {row["set"]["id"] for row in recovered} == {"SM1+"}
    assert {row["set"]["name"] for row in recovered} == {"強化拡張パック サン&ムーン"}
    assert connector.calls.count(f"{base}/cards") == 1


def test_full_load_skips_listed_404_set_proven_empty_by_global_cards():
    base = "https://api.tcgdex.net/v2/ja"
    ghost_url = f"{base}/sets/SM1+"
    connector = FakePhysicalConnector(
        {
            f"{base}/series": [{"id": "sm", "name": "Sun & Moon"}],
            f"{base}/sets": [
                {"id": "SM1+", "name": "強化拡張パック サン&ムーン"},
                {"id": "SM2K", "name": "キミを待つ島々"},
            ],
            ghost_url: _not_found(ghost_url),
            f"{base}/cards": [
                {
                    "id": "SM2K-001",
                    "localId": "001",
                    "name": "Other set card",
                    "image": "https://assets.tcgdex.net/ja/sm/SM2K/001",
                }
            ],
            f"{base}/sets/SM2K": {
                "id": "SM2K",
                "name": "キミを待つ島々",
                "cards": [
                    {
                        "id": "SM2K-001",
                        "localId": "001",
                        "name": "Other set card",
                        "image": "https://assets.tcgdex.net/ja/sm/SM2K/001",
                    }
                ],
            },
        }
    )

    rows = connector._load_remote(lang="ja")

    assert [row["id"] for row in rows] == ["SM2K-001"]
    assert all(row["set"]["id"] != "SM1+" for row in rows)
    assert connector.calls.count(f"{base}/cards") == 1


def test_explicit_listed_set_with_404_detail_uses_same_recovery_path():
    base = "https://api.tcgdex.net/v2/ja"
    broken_url = f"{base}/sets/SM1+"
    connector = FakePhysicalConnector(
        {
            f"{base}/series": [{"id": "sm", "name": "Sun & Moon"}],
            broken_url: _not_found(broken_url),
            f"{base}/sets": [{"id": "SM1+", "name": "強化拡張パック サン&ムーン"}],
            f"{base}/cards": [
                {"id": "SM1+-001", "localId": "001", "name": "Recovered 1"},
                {"id": "SM1+-002", "localId": "002", "name": "Recovered 2"},
            ],
        }
    )

    rows = connector._load_remote(set_id="SM1+", limit=1, lang="ja")

    assert len(rows) == 1
    assert rows[0]["id"] == "SM1+-001"
    assert rows[0]["set"]["id"] == "SM1+"


def test_explicit_listed_404_set_with_no_global_cards_stays_fail_closed():
    base = "https://api.tcgdex.net/v2/ja"
    ghost_url = f"{base}/sets/SM1+"
    connector = FakePhysicalConnector(
        {
            f"{base}/series": [{"id": "sm", "name": "Sun & Moon"}],
            ghost_url: _not_found(ghost_url),
            f"{base}/sets": [{"id": "SM1+", "name": "強化拡張パック サン&ムーン"}],
            f"{base}/cards": [],
        }
    )

    with pytest.raises(RuntimeError, match="no cards can be recovered"):
        connector._load_remote(set_id="SM1+", lang="ja")


def test_registry_uses_physical_only_tcgdex_writer():
    connector = get_connector("tcgdex_pokemon")
    assert isinstance(connector, PhysicalMultilingualTcgdexPokemonConnector)
