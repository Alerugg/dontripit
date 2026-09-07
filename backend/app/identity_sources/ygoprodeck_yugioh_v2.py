from __future__ import annotations

from dataclasses import dataclass

from app.physical_identity_v2 import IdentityValue, PhysicalIdentityDescriptor, normalize_token, rarity_family


@dataclass(frozen=True, slots=True)
class YugiohPrintClaim:
    descriptor: PhysicalIdentityDescriptor
    set_name: str
    set_code: str
    rarity_raw: str
    rarity_code: str | None


def claims_from_card(card: dict) -> list[YugiohPrintClaim]:
    card_id = str(card.get("id") or "").strip()
    name = str(card.get("name") or "").strip()
    if not card_id or not name:
        return []

    claims: list[YugiohPrintClaim] = []
    seen: set[tuple[str, str, str, str]] = set()
    for index, row in enumerate(card.get("card_sets") or []):
        if not isinstance(row, dict):
            continue
        set_name = str(row.get("set_name") or "").strip()
        set_code = str(row.get("set_code") or "").strip()
        rarity_raw = str(row.get("set_rarity") or "").strip()
        rarity_code = str(row.get("set_rarity_code") or "").strip() or None
        if not set_name or not set_code:
            continue
        key = (normalize_token(set_name), normalize_token(set_code), rarity_family(rarity_raw), normalize_token(rarity_code))
        if key in seen:
            continue
        seen.add(key)

        source_id = f"{card_id}:{set_code}:{rarity_code or rarity_family(rarity_raw) or index}"
        descriptor = PhysicalIdentityDescriptor(
            game="yugioh",
            source="ygoprodeck",
            source_print_id=source_id,
            card_concept=card_id,
            # A YGOPRODeck set_name is release-level; set_code is the card's print code.
            release=set_name,
            collector_number=IdentityValue.known(set_code),
            language=IdentityValue.known(row.get("set_language")) if normalize_token(row.get("set_language")) else IdentityValue.unknown(),
            region=IdentityValue.unknown(),
            rarity=IdentityValue.known(rarity_family(rarity_raw)) if rarity_raw else IdentityValue.unknown(),
            finish=IdentityValue.unknown(),
            edition=IdentityValue.unknown(),
            # YGOPRODeck does not prove Cardmarket V.1/V.2 from card_sets.
            version=IdentityValue.unknown(),
            stamp=IdentityValue.unknown(),
            treatment=IdentityValue.unknown(),
            artwork=IdentityValue.unknown(),
            reprint_family=IdentityValue.unknown(),
            errata_revision=IdentityValue.unknown(),
            promo_type=IdentityValue.unknown(),
            size=IdentityValue.not_applicable(),
            source_facts={
                "ygoprodeck_card_id": card_id,
                "set_name": set_name,
                "set_code": set_code,
                "set_rarity": rarity_raw or None,
                "set_rarity_code": rarity_code,
                "card_images": [
                    {"id": image.get("id"), "image_url": image.get("image_url")}
                    for image in (card.get("card_images") or [])
                    if isinstance(image, dict)
                ],
            },
        )
        claims.append(
            YugiohPrintClaim(
                descriptor=descriptor,
                set_name=set_name,
                set_code=set_code,
                rarity_raw=rarity_raw,
                rarity_code=rarity_code,
            )
        )
    return claims
