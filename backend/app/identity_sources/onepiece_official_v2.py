from __future__ import annotations

from dataclasses import dataclass

from app.physical_identity_v2 import IdentityValue, PhysicalIdentityDescriptor, normalize_token


@dataclass(frozen=True, slots=True)
class OnePiecePrintClaim:
    descriptor: PhysicalIdentityDescriptor
    variant: str
    variant_family: str


def claims_from_payload(payload: dict) -> list[OnePiecePrintClaim]:
    language = str(payload.get("language") or "").strip()
    region = str(payload.get("region") or "").strip()
    claims: list[OnePiecePrintClaim] = []

    for card in payload.get("cards") or []:
        if not isinstance(card, dict):
            continue
        card_id = str(card.get("id") or "").strip()
        card_name = str(card.get("name") or "").strip()
        if not card_id or not card_name:
            continue
        for row in card.get("prints") or []:
            if not isinstance(row, dict):
                continue
            set_code = str(row.get("set_code") or "").strip()
            collector = str(row.get("collector_number") or "").strip()
            variant = str(row.get("variant") or "default").strip().lower() or "default"
            variant_family = str(row.get("variant_family") or "").strip().lower()
            rarity = str(row.get("rarity") or "").strip()
            source_print_id = str(row.get("id") or "").strip() or f"{collector}:{variant}:{language}:{region}"
            if not set_code or not collector:
                continue

            if variant == "default":
                version = IdentityValue.known("base")
                reprint_family = IdentityValue.not_applicable()
            else:
                version = IdentityValue.known(variant)
                reprint_family = (
                    IdentityValue.known(variant_family)
                    if normalize_token(variant_family) and variant_family != "default"
                    else IdentityValue.unknown()
                )

            descriptor = PhysicalIdentityDescriptor(
                game="onepiece",
                source="onepiece_official",
                source_print_id=source_print_id,
                card_concept=card_id,
                release=set_code,
                collector_number=IdentityValue.known(collector),
                language=IdentityValue.known(language) if language else IdentityValue.unknown(),
                region=IdentityValue.known(region) if region else IdentityValue.unknown(),
                rarity=IdentityValue.known(rarity) if rarity else IdentityValue.unknown(),
                finish=IdentityValue.unknown(),
                edition=IdentityValue.not_applicable(),
                version=version,
                stamp=IdentityValue.unknown(),
                treatment=IdentityValue.unknown(),
                # Image is retained as evidence, but not fingerprinted: source URLs can change.
                artwork=IdentityValue.unknown(),
                reprint_family=reprint_family,
                errata_revision=IdentityValue.unknown(),
                promo_type=IdentityValue.known("promo") if set_code.upper() == "P" else IdentityValue.not_applicable(),
                size=IdentityValue.not_applicable(),
                source_facts={
                    "official_card_id": card_id,
                    "official_source_print_id": source_print_id,
                    "variant": variant,
                    "variant_family": variant_family or None,
                    "image_url": row.get("image_url"),
                    "release_appearances": row.get("release_appearances") or [],
                    "alternate_source_images": row.get("alternate_source_images") or [],
                    "details": row.get("details") or {},
                },
            )
            claims.append(
                OnePiecePrintClaim(
                    descriptor=descriptor,
                    variant=variant,
                    variant_family=variant_family,
                )
            )
    return claims
