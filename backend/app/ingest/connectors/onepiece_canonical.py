from __future__ import annotations

from app.ingest.connectors.onepiece_v2 import OnePieceV2Connector


class OnePieceCanonicalConnector(OnePieceV2Connector):
    """Production One Piece catalog connector.

    Canonical Card/Print identity must come from the official Bandai card list
    parsed by the V2 identity model. Secondary structured sources may enrich
    Search V2 attributes, but they must never replace canonical identity.
    """

    name = "onepiece"

    def _load_remote(self, *, limit: int | None = None) -> dict:
        return self._load_official_cardlist_remote(limit=limit)
