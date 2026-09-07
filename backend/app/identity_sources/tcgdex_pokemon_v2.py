from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Iterable

from app.physical_identity_v2 import (
    IdentityValue,
    MarketIdentityEvidence,
    MarketRelationship,
    PhysicalIdentityDescriptor,
    normalize_token,
)


@dataclass(frozen=True, slots=True)
class PokemonVariantClaim:
    descriptor: PhysicalIdentityDescriptor
    variant_id: str
    cardmarket_id: str | None
    tcgplayer_id: str | None


def _known_or_unknown(value: object) -> IdentityValue:
    return IdentityValue.known(value) if normalize_token(value) else IdentityValue.unknown()


def _edition(*, subtype: str, stamps: tuple[str, ...]) -> IdentityValue:
    normalized_stamps = {normalize_token(x) for x in stamps}
    normalized_subtype = normalize_token(subtype)
    if "1st-edition" in normalized_stamps or normalized_subtype == "1st edition":
        return IdentityValue.known("first edition")
    if normalized_subtype == "unlimited":
        return IdentityValue.known("unlimited")
    # Absence of a 1st-edition stamp is not proof of Unlimited.
    return IdentityValue.unknown()


def _stamps_without_edition(stamps: tuple[str, ...]) -> IdentityValue:
    remaining = [x for x in stamps if normalize_token(x) != "1st-edition"]
    return IdentityValue.known(*remaining) if remaining else IdentityValue.not_applicable()


def _treatment(*, subtype: str, foil: str) -> IdentityValue:
    values = []
    if normalize_token(subtype) and normalize_token(subtype) != "unlimited":
        values.append(subtype)
    if normalize_token(foil):
        values.append(foil)
    return IdentityValue.known(*values) if values else IdentityValue.not_applicable()


def claims_from_card(card: dict, *, language: str = "en", region: str = "international") -> list[PokemonVariantClaim]:
    """Convert one full TCGdex Card response into physical variant claims.

    This function deliberately requires full-card `variants_detailed`. A CardBrief
    is insufficient for physical identity and returns no claims rather than
    fabricating `default/nonfoil` variants.
    """
    card_id = str(card.get("id") or "").strip()
    name = str(card.get("name") or "").strip()
    local_id = str(card.get("localId") or "").strip()
    rarity = str(card.get("rarity") or "").strip()
    set_row = card.get("set") or {}
    release_id = str(set_row.get("id") or "").strip()
    if not card_id or not name or not release_id or not local_id:
        return []

    variants = card.get("variants_detailed")
    if not isinstance(variants, list) or not variants:
        return []

    claims: list[PokemonVariantClaim] = []
    for variant in variants:
        if not isinstance(variant, dict):
            continue
        variant_id = str(variant.get("variantId") or "").strip()
        variant_type = str(variant.get("type") or "").strip()
        if not variant_id or not variant_type:
            continue
        subtype = str(variant.get("subtype") or "").strip()
        foil = str(variant.get("foil") or "").strip()
        size = str(variant.get("size") or "").strip()
        stamps = tuple(str(x).strip() for x in (variant.get("stamp") or []) if str(x).strip())
        third_party = variant.get("thirdParty") or {}
        cm_id = str(third_party.get("cardmarket") or "").strip() if isinstance(third_party, dict) else ""
        tcgplayer_id = str(third_party.get("tcgplayer") or "").strip() if isinstance(third_party, dict) else ""

        descriptor = PhysicalIdentityDescriptor(
            game="pokemon",
            source="tcgdex",
            source_print_id=f"{card_id}:{variant_id}",
            card_concept=name,
            release=release_id,
            collector_number=IdentityValue.known(local_id),
            language=IdentityValue.known(language),
            region=IdentityValue.known(region),
            rarity=_known_or_unknown(rarity),
            # TCGdex variant type is physical finish family: normal/reverse/holo.
            finish=IdentityValue.known(variant_type),
            edition=_edition(subtype=subtype, stamps=stamps),
            version=IdentityValue.known(variant_id),
            stamp=_stamps_without_edition(stamps),
            treatment=_treatment(subtype=subtype, foil=foil),
            artwork=IdentityValue.unknown(),
            reprint_family=IdentityValue.unknown(),
            errata_revision=IdentityValue.unknown(),
            promo_type=IdentityValue.unknown(),
            size=_known_or_unknown(size),
            source_facts={
                "tcgdex_card_id": card_id,
                "tcgdex_variant_id": variant_id,
                "variant_type": variant_type,
                "subtype": subtype or None,
                "foil": foil or None,
                "stamps": list(stamps),
                "cardmarket_id": cm_id or None,
                "tcgplayer_id": tcgplayer_id or None,
            },
        )
        claims.append(
            PokemonVariantClaim(
                descriptor=descriptor,
                variant_id=variant_id,
                cardmarket_id=cm_id or None,
                tcgplayer_id=tcgplayer_id or None,
            )
        )
    return claims


def classify_cardmarket_claims(claims: Iterable[PokemonVariantClaim]) -> list[MarketIdentityEvidence]:
    """Classify direct TCGdex Cardmarket claims without forcing 1:1 semantics.

    One Cardmarket idProduct seen on one physical fingerprint is direct exact
    evidence. If TCGdex assigns that same idProduct to multiple physical
    fingerprints, Cardmarket is treated as grouping those variants and the
    relationship becomes GROUPED_PHYSICAL.
    """
    by_cardmarket: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    for claim in claims:
        if not claim.cardmarket_id:
            continue
        fp = claim.descriptor.fingerprint()
        by_cardmarket[claim.cardmarket_id][fp].add(claim.variant_id)

    evidence: list[MarketIdentityEvidence] = []
    for cm_id, fingerprints in sorted(by_cardmarket.items(), key=lambda item: int(item[0]) if item[0].isdigit() else item[0]):
        fps = tuple(sorted(fingerprints))
        relationship = (
            MarketRelationship.EXACT_ONE_PHYSICAL
            if len(fps) == 1
            else MarketRelationship.GROUPED_PHYSICAL
        )
        reason = (
            "TCGdex variants_detailed supplies direct thirdParty.cardmarket for one physical variant"
            if relationship == MarketRelationship.EXACT_ONE_PHYSICAL
            else "TCGdex variants_detailed assigns one Cardmarket idProduct to multiple distinct physical variantIds"
        )
        evidence.append(
            MarketIdentityEvidence(
                market="cardmarket",
                external_product_id=cm_id,
                relationship=relationship,
                physical_fingerprints=fps,
                evidence_sources=("tcgdex:variants_detailed",),
                reason=reason,
            )
        )
    return evidence
