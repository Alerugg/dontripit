from __future__ import annotations

import threading
import time

from app.ingest.connectors.tcgdex_pokemon_certified_refresh import (
    CertifiedRefreshPokemonTCGDexConnector,
)


class ConcurrentProbeConnector(CertifiedRefreshPokemonTCGDexConnector):
    def __init__(self):
        super().__init__()
        self._lock = threading.Lock()
        self.active_set_requests = 0
        self.max_active_set_requests = 0

    def _request_json(self, url: str, params=None):
        base = "https://api.tcgdex.net/v2/en"
        if url == f"{base}/series":
            return [{"id": "base", "name": "Base"}]
        if url == f"{base}/sets":
            return [
                {"id": "base1", "name": "Base Set"},
                {"id": "base2", "name": "Jungle"},
            ]
        if url in {f"{base}/sets/base1", f"{base}/sets/base2"}:
            remote_set_id = url.rsplit("/", 1)[-1]
            with self._lock:
                self.active_set_requests += 1
                self.max_active_set_requests = max(
                    self.max_active_set_requests,
                    self.active_set_requests,
                )
            try:
                # Long enough for the second worker to overlap deterministically,
                # short enough to keep the focused CI test fast.
                time.sleep(0.05)
                number = "1" if remote_set_id == "base1" else "2"
                return {
                    "id": remote_set_id,
                    "name": "Base Set" if remote_set_id == "base1" else "Jungle",
                    "cards": [
                        {
                            "id": f"{remote_set_id}-{number}",
                            "localId": number,
                            "name": "Detailed card",
                            "hp": 60 if remote_set_id == "base1" else 70,
                            "attacks": [{"name": "Test attack", "damage": "10"}],
                        }
                    ],
                }
            finally:
                with self._lock:
                    self.active_set_requests -= 1
        raise AssertionError(f"Unexpected TCGdex request: {url}")


def test_full_certified_refresh_fetches_sets_concurrently_without_losing_detail_or_order():
    connector = ConcurrentProbeConnector()

    rows = connector._load_remote(lang="en")

    assert [row["id"] for row in rows] == ["base1-1", "base2-2"]
    assert [row["hp"] for row in rows] == [60, 70]
    assert [row["attacks"][0]["name"] for row in rows] == ["Test attack", "Test attack"]
    assert connector.max_active_set_requests >= 2


def test_targeted_refresh_keeps_the_existing_serial_path():
    connector = ConcurrentProbeConnector()

    rows = connector._load_remote(set_id="base1", lang="en")

    assert [row["id"] for row in rows] == ["base1-1"]
    assert connector.max_active_set_requests == 1
