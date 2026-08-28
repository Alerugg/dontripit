from app.ingest.base import SourceConnector
from app.ingest.connectors.fixture_local import FixtureLocalConnector
from app.ingest.connectors.onepiece_incremental_guard import SelfHealingOnePieceCanonicalConnector
from app.ingest.connectors.riftbound import RiftboundConnector
from app.ingest.connectors.scryfall_mtg_v2 import ScryfallMtgV2Connector
from app.ingest.connectors.tcgdex_pokemon_identity_rehome import (
    ExactIdentityRehomeCertifiedPokemonTCGDexConnector,
)
from app.ingest.connectors.ygoprodeck_yugioh_v2 import YgoProDeckYugiohV2Connector


CONNECTORS = {
    FixtureLocalConnector.name: FixtureLocalConnector,
    ScryfallMtgV2Connector.name: ScryfallMtgV2Connector,
    ExactIdentityRehomeCertifiedPokemonTCGDexConnector.name: ExactIdentityRehomeCertifiedPokemonTCGDexConnector,
    SelfHealingOnePieceCanonicalConnector.name: SelfHealingOnePieceCanonicalConnector,
    YgoProDeckYugiohV2Connector.name: YgoProDeckYugiohV2Connector,
    RiftboundConnector.name: RiftboundConnector,
}

# Canonical identity can be stricter than historical generic upsert contracts.
# Keep source classes importable for read-only audits/snapshot tools, but block
# generic writers until their physical identity and source policy are certified.
WRITE_QUARANTINED_CONNECTORS = {
    "scryfall_mtg": (
        "MTG Canonical V2 uses exact physical Print identity "
        "(Scryfall object id + finish). The legacy generic Scryfall upsert is "
        "quarantined until a finish-aware incremental writer is certified."
    ),
    "riftbound": (
        "Riftbound must use Riot's authorized riftbound-content-v1 source only. "
        "The legacy connector can auto-fallback to unofficial data, merges Cards "
        "by name when source IDs are absent, and does not yet prove exact physical "
        "variant identity. It is read-only/quarantined until the official Riot API "
        "source and canonical V2 snapshot are certified."
    ),
}


def is_connector_write_quarantined(name: str) -> bool:
    return str(name or "").strip() in WRITE_QUARANTINED_CONNECTORS


def get_connector(name: str, *, allow_quarantined: bool = False) -> SourceConnector:
    connector_name = str(name or "").strip()
    connector_cls = CONNECTORS.get(connector_name)
    if not connector_cls:
        raise ValueError(f"Unknown connector: {connector_name}")
    if is_connector_write_quarantined(connector_name) and not allow_quarantined:
        raise RuntimeError(
            f"Write connector quarantined: {connector_name}. "
            f"{WRITE_QUARANTINED_CONNECTORS[connector_name]}"
        )
    return connector_cls()
