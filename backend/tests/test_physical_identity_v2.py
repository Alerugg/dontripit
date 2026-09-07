from __future__ import annotations

import pytest

from app.physical_identity_v2 import (
    IdentityValue,
    KnowledgeState,
    MarketIdentityEvidence,
    MarketRelationship,
    PhysicalIdentityDescriptor,
    rarity_family,
)


def descriptor(**overrides):
    values = {
        "game": "pokemon",
        "source": "tcgdex",
        "source_print_id": "base1-4:variant-a",
        "card_concept": "Charizard",
        "release": "Base Set",
        "collector_number": IdentityValue.known("4/102"),
        "language": IdentityValue.known("en"),
        "rarity": IdentityValue.known("Rare"),
        "finish": IdentityValue.known("holo"),
        "edition": IdentityValue.unknown(),
    }
    values.update(overrides)
    return PhysicalIdentityDescriptor(**values)


def test_unknown_does_not_collapse_to_known_default():
    unknown = descriptor(edition=IdentityValue.unknown())
    unlimited = descriptor(edition=IdentityValue.known("unlimited"))
    assert unknown.fingerprint() != unlimited.fingerprint()
    assert unknown.missing_dimensions(["edition"]) == ("edition",)
    assert unlimited.missing_dimensions(["edition"]) == ()


def test_not_applicable_is_distinct_from_unknown():
    unknown = descriptor(region=IdentityValue.unknown())
    na = descriptor(region=IdentityValue.not_applicable())
    assert unknown.fingerprint() != na.fingerprint()
    assert unknown.region.state is KnowledgeState.UNKNOWN
    assert na.region.state is KnowledgeState.NOT_APPLICABLE


def test_stamp_and_treatment_change_physical_fingerprint():
    plain = descriptor(stamp=IdentityValue.known("none"))
    prerelease = descriptor(stamp=IdentityValue.known("pre-release"))
    assert plain.fingerprint() != prerelease.fingerprint()


def test_source_provenance_does_not_change_canonical_fingerprint():
    a = descriptor(source="tcgdex", source_print_id="a", source_facts={"raw": 1})
    b = descriptor(source="cardtrader", source_print_id="b", source_facts={"raw": 2})
    assert a.fingerprint() == b.fingerprint()


def test_grouped_market_product_requires_multiple_physical_fingerprints():
    fp = descriptor().fingerprint()
    with pytest.raises(ValueError):
        MarketIdentityEvidence(
            market="cardmarket",
            external_product_id="123",
            relationship=MarketRelationship.GROUPED_PHYSICAL,
            physical_fingerprints=(fp,),
            evidence_sources=("tcgdex",),
            reason="Cardmarket groups first edition and unlimited",
        )


def test_exact_market_product_requires_one_physical_fingerprint():
    fp = descriptor().fingerprint()
    evidence = MarketIdentityEvidence(
        market="cardmarket",
        external_product_id="123",
        relationship=MarketRelationship.EXACT_ONE_PHYSICAL,
        physical_fingerprints=(fp,),
        evidence_sources=("tcgdex",),
        reason="direct variant thirdParty.cardmarket id",
    )
    assert evidence.relationship is MarketRelationship.EXACT_ONE_PHYSICAL


def test_rarity_normalization_only_collapses_certified_spelling_aliases():
    assert rarity_family("Ultra") == "ultra rare"
    assert rarity_family("UR") == "ultra rare"
    assert rarity_family("Secret Rare") == "secret rare"
    assert rarity_family("Quarter Century Secret Rare") == "quarter century secret rare"
