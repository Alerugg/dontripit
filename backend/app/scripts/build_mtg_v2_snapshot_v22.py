from __future__ import annotations

import argparse
from pathlib import Path

from app.mtg_identity_v2 import clean
from app.scripts import build_mtg_v2_snapshot as base


SNAPSHOT_SCHEMA_VERSION = "mtg-canonical-v2.2"
_BASE_PRINT_ATTRIBUTES = base._print_attributes


def _norm_semantic_set_list(value: object) -> list:
    """Canonicalize Scryfall arrays whose order has no semantic meaning."""

    normalized = {clean(item) for item in base._norm_list(value) if clean(item)}
    return sorted(normalized)


def _face_payload(face: dict) -> dict:
    payload = base._face_payload(face)
    payload["colors"] = _norm_semantic_set_list(face.get("colors"))
    return payload


def card_attributes(card: dict) -> dict:
    """Immutable logical Card attributes only.

    Scryfall's paper printing objects can legitimately disagree on `legalities`
    and `reserved` for one Oracle identity (for example a tournament-legal
    original versus a commemorative/non-tournament printing). Those two fields
    therefore must not participate in logical Card fingerprinting.

    Scryfall also does not guarantee a stable ordering for set-like arrays such
    as `keywords`, `colors`, `color_identity`, and `produced_mana`. Their order
    is not rules-significant, so canonicalize them before fingerprinting while
    preserving the order of `card_faces`, which is significant.
    """

    return {
        "layout": clean(card.get("layout")) or None,
        "mana_cost": clean(card.get("mana_cost")) or None,
        "mana_value": card.get("cmc"),
        "type_line": clean(card.get("type_line")) or None,
        "oracle_text": clean(card.get("oracle_text")) or None,
        "colors": _norm_semantic_set_list(card.get("colors")),
        "color_identity": _norm_semantic_set_list(card.get("color_identity")),
        "keywords": _norm_semantic_set_list(card.get("keywords")),
        "power": clean(card.get("power")) or None,
        "toughness": clean(card.get("toughness")) or None,
        "loyalty": clean(card.get("loyalty")) or None,
        "defense": clean(card.get("defense")) or None,
        "produced_mana": _norm_semantic_set_list(card.get("produced_mana")),
        "faces": [
            _face_payload(face)
            for face in base._norm_list(card.get("card_faces"))
            if isinstance(face, dict)
        ],
    }


def print_attributes(card: dict, finish: str) -> dict:
    attrs = dict(_BASE_PRINT_ATTRIBUTES(card, finish))
    attrs["legalities"] = base._norm_dict(card.get("legalities"))
    attrs["reserved"] = bool(card.get("reserved"))
    return attrs


def run(*, output_dir: Path) -> dict:
    # Reuse the heavily gated V2.1 writer and manifest machinery while
    # overriding only the proven Card-vs-Print classification. The monkeypatch
    # is process-local to this one-shot builder and keeps the original snapshot
    # implementation available as historical evidence.
    base.SNAPSHOT_SCHEMA_VERSION = SNAPSHOT_SCHEMA_VERSION
    base._card_attributes = card_attributes
    base._print_attributes = print_attributes
    return base.run(output_dir=output_dir)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build MTG canonical V2.2 snapshot with printing-context legality/reserved fields"
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    run(output_dir=args.output_dir)


if __name__ == "__main__":
    main()
