from __future__ import annotations

from typing import Any


# Source reconciliation note:
# TCGdex PR #1375 introduced two `holo` rows for sv10-096 with no finish/stamp
# distinction but different Cardmarket products. The standard Destined Rivals
# product (Cardmarket 825969) belongs to the main-set product; Cardmarket 828209
# is the additional/promo product for the same collector number. Keep this
# resolution explicit and auditable instead of guessing from price or row order.
#
# Primary/source evidence:
# - tcgdex/cards-database PR #1375, file 096.ts
# - Cardmarket standard DRI 096 product vs Destined Rivals: Additionals catalog
KNOWN_RELEASE_CONTEXT_BY_CARDMARKET: dict[tuple[str, int], str] = {
    ("sv10-096", 825969): "main_set",
    ("sv10-096", 828209): "additional_promo",
}


def third_party_ids(raw: dict[str, Any] | None) -> dict[str, Any]:
    value = (raw or {}).get("thirdParty")
    if not isinstance(value, dict):
        return {}
    return {str(key): child for key, child in sorted(value.items()) if child not in (None, "")}


def resolved_release_context(source_id: str, raw_variant: dict[str, Any] | None) -> str | None:
    ids = third_party_ids(raw_variant)
    cardmarket = ids.get("cardmarket")
    try:
        cardmarket_id = int(cardmarket)
    except (TypeError, ValueError):
        return None
    return KNOWN_RELEASE_CONTEXT_BY_CARDMARKET.get((source_id, cardmarket_id))


def variant_dimensions(source_id: str, raw_variant: dict[str, Any]) -> dict[str, Any]:
    """Return physical identity dimensions without using marketplace IDs as identity.

    Marketplace IDs are evidence/provenance, not normally a physical dimension.
    The only current exception is a source-proven release-context distinction for
    sv10-096, encoded above and deliberately scoped to exact Cardmarket IDs.
    """
    payload = {
        "type": str(raw_variant.get("type") or "unknown").strip().lower(),
        "subtype": str(raw_variant.get("subtype") or "").strip().lower() or None,
        "stamps": sorted(
            {str(value).strip().lower() for value in (raw_variant.get("stamp") or []) if str(value).strip()}
        ),
        "foil": str(raw_variant.get("foil") or "").strip().lower() or None,
        "size": str(raw_variant.get("size") or "").strip().lower() or None,
        "language": "en",
        "release_context": resolved_release_context(source_id, raw_variant),
    }
    return payload
