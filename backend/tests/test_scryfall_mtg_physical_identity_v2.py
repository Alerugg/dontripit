from __future__ import annotations

from app.identity_sources.scryfall_mtg_v2 import claims_from_card, classify_cardmarket_claims
from app.physical_identity_v2 import MarketRelationship


def test_one_scryfall_print_can_expand_into_multiple_physical_finishes():
    card = {
        "id": "scryfall-print-1",
        "oracle_id": "oracle-1",
        "name": "Example",
        "set": "abc",
        "collector_number": "123",
        "lang": "en",
        "rarity": "rare",
        "finishes": ["nonfoil", "foil"],
        "foil": True,
        "nonfoil": True,
        "cardmarket_id": 999,
        "illustration_id": "art-1",
    }
    claims = claims_from_card(card)
    assert {c.finish for c in claims} == {"nonfoil", "foil"}
    assert len({c.descriptor.fingerprint() for c in claims}) == 2
    evidence = classify_cardmarket_claims(claims)
    assert len(evidence) == 1
    assert evidence[0].relationship is MarketRelationship.GROUPED_PHYSICAL
    assert len(evidence[0].physical_fingerprints) == 2


def test_etched_is_not_collapsed_into_generic_foil():
    card = {
        "id": "scryfall-print-2",
        "oracle_id": "oracle-2",
        "name": "Etched Example",
        "set": "mh2",
        "collector_number": "500",
        "lang": "en",
        "rarity": "mythic",
        "finishes": ["etched"],
        "cardmarket_id": 1000,
        "illustration_id": "art-2",
    }
    claim = claims_from_card(card)[0]
    assert claim.finish == "etched"
    assert claim.descriptor.finish.values == ("etched",)
    evidence = classify_cardmarket_claims([claim])[0]
    assert evidence.relationship is MarketRelationship.EXACT_ONE_PHYSICAL


def test_security_stamp_and_treatment_are_identity_facts():
    base = {
        "id": "scryfall-print-3",
        "oracle_id": "oracle-3",
        "name": "Treatment Example",
        "set": "setx",
        "collector_number": "1",
        "lang": "en",
        "rarity": "rare",
        "finishes": ["nonfoil"],
        "illustration_id": "art-3",
    }
    plain = claims_from_card(base)[0].descriptor
    special = claims_from_card({**base, "security_stamp": "oval", "frame_effects": ["showcase"]})[0].descriptor
    assert plain.fingerprint() != special.fingerprint()
