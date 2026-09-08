from __future__ import annotations

from app.identity_sources.tcgdex_pokemon_v2 import (
    claims_from_card,
    classify_cardmarket_claims,
)
from app.physical_identity_v2 import KnowledgeState, MarketRelationship


def test_first_edition_and_shadowless_are_preserved_as_physical_dimensions():
    card = {
        "id": "base1-76",
        "localId": "76",
        "name": "Pokémon Breeder",
        "rarity": "Rare",
        "set": {"id": "base1", "name": "Base Set"},
        "variants_detailed": [
            {
                "type": "normal",
                "subtype": "shadowless",
                "size": "standard",
                "stamp": ["1st-edition"],
                "thirdParty": {"cardmarket": 660142},
                "variantId": "first-ed-shadowless",
            },
            {
                "type": "normal",
                "subtype": "shadowless",
                "size": "standard",
                "thirdParty": {"cardmarket": 660142},
                "variantId": "shadowless-no-edition-proof",
            },
        ],
    }
    claims = claims_from_card(card)
    assert len(claims) == 2
    first, second = claims
    assert first.descriptor.edition.values == ("first edition",)
    assert first.descriptor.treatment.values == ("shadowless",)
    assert second.descriptor.edition.state is KnowledgeState.UNKNOWN
    assert first.descriptor.fingerprint() != second.descriptor.fingerprint()

    cm = classify_cardmarket_claims(claims)
    assert len(cm) == 1
    assert cm[0].external_product_id == "660142"
    assert cm[0].relationship is MarketRelationship.GROUPED_PHYSICAL
    assert len(cm[0].physical_fingerprints) == 2


def test_unique_variant_cardmarket_id_is_exact_one_physical():
    card = {
        "id": "2021swsh-7",
        "localId": "7",
        "name": "Rowlet",
        "rarity": "Common",
        "set": {"id": "2021swsh", "name": "McDonald's Collection 2021"},
        "variants_detailed": [
            {
                "type": "normal",
                "size": "standard",
                "stamp": ["25th-celebration"],
                "thirdParty": {"cardmarket": 538838},
                "variantId": "normal-stamped",
            },
            {
                "type": "holo",
                "size": "standard",
                "stamp": ["25th-celebration"],
                "thirdParty": {"cardmarket": 538843},
                "variantId": "holo-stamped",
            },
        ],
    }
    evidence = classify_cardmarket_claims(claims_from_card(card))
    assert {row.external_product_id for row in evidence} == {"538838", "538843"}
    assert all(row.relationship is MarketRelationship.EXACT_ONE_PHYSICAL for row in evidence)


def test_cardbrief_is_refused_instead_of_fabricating_default_variant():
    card_brief = {
        "id": "base1-4",
        "localId": "4",
        "name": "Charizard",
        "set": {"id": "base1", "name": "Base Set"},
    }
    assert claims_from_card(card_brief) == []


def test_foil_treatment_is_not_collapsed_into_variant_type():
    card = {
        "id": "x-1",
        "localId": "1",
        "name": "Example",
        "rarity": "Uncommon",
        "set": {"id": "x", "name": "Example Set"},
        "variants_detailed": [
            {
                "type": "normal",
                "size": "standard",
                "foil": "galaxy",
                "variantId": "galaxy-normal",
            }
        ],
    }
    claim = claims_from_card(card)[0]
    assert claim.descriptor.finish.values == ("normal",)
    assert claim.descriptor.treatment.values == ("galaxy",)


def test_generated_variant_ids_are_disambiguated_by_physical_signature():
    card = {
        "id": "swsh1-34",
        "localId": "34",
        "name": "Example Generated Variant",
        "rarity": "Rare",
        "set": {"id": "swsh1", "name": "Sword & Shield"},
        "variants_detailed": [
            {
                "type": "normal",
                "size": "standard",
                "variantId": "generated",
                "thirdParty": {"cardmarket": 1001},
            },
            {
                "type": "reverse",
                "size": "standard",
                "variantId": "generated",
                "thirdParty": {"cardmarket": 1001},
            },
        ],
    }
    claims = claims_from_card(card)
    assert len(claims) == 2
    first, second = claims
    assert first.descriptor.source_print_id != second.descriptor.source_print_id
    assert "generated|type=normal" in first.descriptor.source_print_id
    assert "generated|type=reverse" in second.descriptor.source_print_id
    assert first.descriptor.version.state is KnowledgeState.UNKNOWN
    assert second.descriptor.version.state is KnowledgeState.UNKNOWN
    assert first.descriptor.fingerprint() != second.descriptor.fingerprint()

    evidence = classify_cardmarket_claims(claims)
    assert len(evidence) == 1
    assert evidence[0].relationship is MarketRelationship.GROUPED_PHYSICAL
    assert len(evidence[0].physical_fingerprints) == 2
