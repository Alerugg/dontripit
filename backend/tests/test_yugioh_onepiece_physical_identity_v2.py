from __future__ import annotations

from app.identity_sources.onepiece_official_v2 import claims_from_payload
from app.identity_sources.ygoprodeck_yugioh_v2 import claims_from_card
from app.physical_identity_v2 import KnowledgeState


def test_yugioh_release_and_card_print_code_are_not_collapsed():
    card = {
        "id": 89631139,
        "name": "Blue-Eyes White Dragon",
        "card_sets": [
            {
                "set_name": "Legend of Blue Eyes White Dragon",
                "set_code": "LOB-001",
                "set_rarity": "Ultra Rare",
                "set_rarity_code": "(UR)",
            }
        ],
        "card_images": [{"id": 89631139, "image_url": "https://example.test/blue-eyes.jpg"}],
    }
    claim = claims_from_card(card)[0]
    assert claim.descriptor.release == "Legend of Blue Eyes White Dragon"
    assert claim.descriptor.collector_number.values == ("lob-001",)
    assert claim.descriptor.rarity.values == ("ultra rare",)
    # YGOPRODeck does not prove Cardmarket V.1/V.2 or edition.
    assert claim.descriptor.version.state is KnowledgeState.UNKNOWN
    assert claim.descriptor.edition.state is KnowledgeState.UNKNOWN


def test_yugioh_rarity_aliases_normalize_without_collapsing_distinct_rarities():
    base = {
        "id": 1,
        "name": "Example",
        "card_sets": [
            {"set_name": "Release A", "set_code": "AAA-001", "set_rarity": "Ultra", "set_rarity_code": "UR"},
            {"set_name": "Release B", "set_code": "BBB-001", "set_rarity": "Secret Rare", "set_rarity_code": "ScR"},
        ],
    }
    claims = claims_from_card(base)
    assert claims[0].descriptor.rarity.values == ("ultra rare",)
    assert claims[1].descriptor.rarity.values == ("secret rare",)
    assert claims[0].descriptor.fingerprint() != claims[1].descriptor.fingerprint()


def test_onepiece_parallel_and_reprint_suffixes_are_physical_versions():
    payload = {
        "language": "en",
        "region": "global-en",
        "cards": [
            {
                "id": "onepiece:op05-119",
                "name": "Monkey.D.Luffy",
                "prints": [
                    {
                        "id": "OP05-119",
                        "set_code": "OP-05",
                        "collector_number": "OP05-119",
                        "rarity": "SEC",
                        "variant": "default",
                        "variant_family": "default",
                    },
                    {
                        "id": "OP05-119_P1",
                        "set_code": "OP-05",
                        "collector_number": "OP05-119",
                        "rarity": "SEC",
                        "variant": "p1",
                        "variant_family": "parallel",
                    },
                    {
                        "id": "OP05-119_R1",
                        "set_code": "OP-05",
                        "collector_number": "OP05-119",
                        "rarity": "SEC",
                        "variant": "r1",
                        "variant_family": "reprint",
                    },
                ],
            }
        ],
    }
    claims = claims_from_payload(payload)
    assert len(claims) == 3
    by_variant = {c.variant: c.descriptor for c in claims}
    assert by_variant["default"].version.values == ("base",)
    assert by_variant["p1"].version.values == ("p1",)
    assert by_variant["p1"].reprint_family.values == ("parallel",)
    assert by_variant["r1"].reprint_family.values == ("reprint",)
    assert len({c.descriptor.fingerprint() for c in claims}) == 3


def test_onepiece_image_url_is_provenance_not_canonical_fingerprint():
    base = {
        "language": "en",
        "region": "global-en",
        "cards": [{
            "id": "onepiece:p-001",
            "name": "Promo",
            "prints": [{
                "id": "P-001",
                "set_code": "P",
                "collector_number": "P-001",
                "rarity": "P",
                "variant": "default",
                "variant_family": "default",
                "image_url": "https://old.example/p-001.png",
            }],
        }],
    }
    changed_url = {
        **base,
        "cards": [{
            **base["cards"][0],
            "prints": [{**base["cards"][0]["prints"][0], "image_url": "https://new.example/p-001.png"}],
        }],
    }
    a = claims_from_payload(base)[0].descriptor
    b = claims_from_payload(changed_url)[0].descriptor
    assert a.fingerprint() == b.fingerprint()
