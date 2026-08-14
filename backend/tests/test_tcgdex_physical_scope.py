import pytest

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
        return self.payloads[url]


def _payloads():
    base = "https://api.tcgdex.net/v2/en"
    return {
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
    assert connector.calls == ["https://api.tcgdex.net/v2/en/series/tcgp"]


def test_tcgp_exclusion_guard_fails_closed_when_series_has_no_sets():
    base = "https://api.tcgdex.net/v2/en"
    connector = FakePhysicalConnector({f"{base}/series/tcgp": {"id": "tcgp", "sets": []}})

    with pytest.raises(RuntimeError, match="TCG Pocket exclusion guard failed"):
        connector._load_remote(lang="en")


def test_registry_uses_physical_only_tcgdex_writer():
    connector = get_connector("tcgdex_pokemon")
    assert isinstance(connector, PhysicalMultilingualTcgdexPokemonConnector)
