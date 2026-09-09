from __future__ import annotations

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
class MtgFinishClaim:
    descriptor: PhysicalIdentityDescriptor
    finish: str
    cardmarket_id: str | None


def _values_or_na(values: object) -> IdentityValue:
    if isinstance(values, (list, tuple, set)):
        cleaned = [v for v in values if normalize_token(v)]
        return IdentityValue.known(*cleaned) if cleaned else IdentityValue.not_applicable()
    return IdentityValue.known(values) if normalize_token(values) else IdentityValue.not_applicable()


def _finishes(card: dict) -> tuple[str, ...]:
    raw = card.get("finishes")
    if isinstance(raw, list):
        values = tuple(dict.fromkeys(normalize_token(x) for x in raw if normalize_token(x)))
        if values:
            return values
    inferred = []
    if card.get("nonfoil") is True:
        inferred.append("nonfoil")
    if card.get("foil") is True:
        inferred.append("foil")
    return tuple(inferred) or ("unknown",)


def claims_from_card(card: dict) -> list[MtgFinishClaim]:
    scryfall_id = str(card.get("id") or "").strip()
    name = str(card.get("name") or "").strip()
    set_code = str(card.get("set") or "").strip()
    collector = str(card.get("collector_number") or "").strip()
    if not scryfall_id or not name or not set_code or not collector:
        return []

    language = str(card.get("lang") or "en").strip()
    rarity = str(card.get("rarity") or "").strip()
    cm_id = str(card.get("cardmarket_id") or "").strip()
    illustration = str(card.get("illustration_id") or "").strip()
    security_stamp = str(card.get("security_stamp") or "").strip()
    promo_types = card.get("promo_types") or []
    frame_effects = card.get("frame_effects") or []
    border_color = str(card.get("border_color") or "").strip()
    variation_of = str(card.get("variation_of") or "").strip()

    treatments = list(frame_effects if isinstance(frame_effects, list) else [])
    if border_color and border_color not in {"black", "white"}:
        treatments.append(f"border:{border_color}")

    claims = []
    for finish in _finishes(card):
        descriptor = PhysicalIdentityDescriptor(
            game="mtg",
            source="scryfall",
            source_print_id=f"{scryfall_id}:{finish}",
            card_concept=str(card.get("oracle_id") or name),
            release=set_code,
            collector_number=IdentityValue.known(collector),
            language=IdentityValue.known(language),
            region=IdentityValue.not_applicable(),
            rarity=IdentityValue.known(rarity) if rarity else IdentityValue.unknown(),
            finish=IdentityValue.known(finish) if finish != "unknown" else IdentityValue.unknown(),
            edition=IdentityValue.not_applicable(),
            version=IdentityValue.known(variation_of) if variation_of else IdentityValue.not_applicable(),
            stamp=IdentityValue.known(security_stamp) if security_stamp else IdentityValue.not_applicable(),
            treatment=_values_or_na(treatments),
            artwork=IdentityValue.known(illustration) if illustration else IdentityValue.unknown(),
            reprint_family=IdentityValue.not_applicable(),
            errata_revision=IdentityValue.not_applicable(),
            promo_type=_values_or_na(promo_types),
            size=IdentityValue.not_applicable(),
            source_facts={
                "scryfall_id": scryfall_id,
                "oracle_id": card.get("oracle_id"),
                "cardmarket_id": cm_id or None,
                "finishes": list(_finishes(card)),
                "foil": card.get("foil"),
                "nonfoil": card.get("nonfoil"),
                "promo": card.get("promo"),
                "promo_types": promo_types,
                "security_stamp": security_stamp or None,
                "frame_effects": frame_effects,
                "border_color": border_color or None,
                "illustration_id": illustration or None,
                "variation": card.get("variation"),
                "variation_of": variation_of or None,
            },
        )
        claims.append(MtgFinishClaim(descriptor=descriptor, finish=finish, cardmarket_id=cm_id or None))
    return claims


def classify_cardmarket_claims(claims: Iterable[MtgFinishClaim]) -> list[MarketIdentityEvidence]:
    by_cm: dict[str, set[str]] = {}
    for claim in claims:
        if not claim.cardmarket_id:
            continue
        by_cm.setdefault(claim.cardmarket_id, set()).add(claim.descriptor.fingerprint())

    output = []
    for cm_id, fingerprints in sorted(by_cm.items(), key=lambda item: int(item[0]) if item[0].isdigit() else item[0]):
        fps = tuple(sorted(fingerprints))
        relationship = MarketRelationship.EXACT_ONE_PHYSICAL if len(fps) == 1 else MarketRelationship.GROUPED_PHYSICAL
        output.append(
            MarketIdentityEvidence(
                market="cardmarket",
                external_product_id=cm_id,
                relationship=relationship,
                physical_fingerprints=fps,
                evidence_sources=("scryfall:cardmarket_id",),
                reason=(
                    "Scryfall cardmarket_id identifies one physical finish"
                    if len(fps) == 1
                    else "Scryfall assigns one cardmarket_id to a printing available in multiple physical finishes"
                ),
            )
        )
    return output
