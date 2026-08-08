from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from typing import Iterable


KNOWN_FINISHES = frozenset({"nonfoil", "foil", "etched"})


def clean(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def normalize_identity_text(value: object) -> str:
    text = unicodedata.normalize("NFKD", clean(value))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", text.casefold()).strip()


def _face_signature(face: dict) -> dict:
    return {
        "name": clean(face.get("name")),
        "mana_cost": clean(face.get("mana_cost")),
        "type_line": clean(face.get("type_line")),
        "oracle_text": clean(face.get("oracle_text")),
        "colors": list(face.get("colors") or []),
        "power": clean(face.get("power")),
        "toughness": clean(face.get("toughness")),
        "loyalty": clean(face.get("loyalty")),
        "defense": clean(face.get("defense")),
    }


def rules_signature(card: dict) -> str:
    """Return a deterministic rules signature for non-Oracle fallback identity.

    The signature intentionally excludes printing dimensions such as set,
    collector number, language, artist, finish and Scryfall object id. Those
    belong to Print identity, not logical Card identity.
    """

    payload = {
        "name": clean(card.get("name")),
        "layout": normalize_identity_text(card.get("layout")),
        "mana_cost": clean(card.get("mana_cost")),
        "type_line": clean(card.get("type_line")),
        "oracle_text": clean(card.get("oracle_text")),
        "colors": list(card.get("colors") or []),
        "color_identity": list(card.get("color_identity") or []),
        "power": clean(card.get("power")),
        "toughness": clean(card.get("toughness")),
        "loyalty": clean(card.get("loyalty")),
        "defense": clean(card.get("defense")),
        "faces": [
            _face_signature(face)
            for face in (card.get("card_faces") or [])
            if isinstance(face, dict)
        ],
    }
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def card_identity_key(card: dict) -> str:
    """Canonical logical MTG Card key.

    Oracle identity is authoritative whenever Scryfall provides it. The rare
    objects without ``oracle_id`` use a source-backed semantic fingerprint:
    normalized name + layout + complete rules signature. This keeps multiple
    printings of the same exceptional logical card together without ever
    fuzzy-merging two different rules objects by name alone.
    """

    oracle_id = clean(card.get("oracle_id")).lower()
    if oracle_id:
        return f"mtg:oracle:{oracle_id}"

    name = normalize_identity_text(card.get("name"))
    layout = normalize_identity_text(card.get("layout"))
    if not name or not layout:
        raise ValueError("MTG card without oracle_id requires non-empty name and layout")

    material = f"{name}|{layout}|{rules_signature(card)}"
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()
    return f"mtg:fallback:{digest}"


def finish_values(card: dict) -> tuple[str, ...]:
    values = {
        clean(value).lower()
        for value in (card.get("finishes") or [])
        if clean(value)
    }
    if not values:
        if bool(card.get("nonfoil")):
            values.add("nonfoil")
        if bool(card.get("foil")):
            values.add("foil")

    unknown = values - KNOWN_FINISHES
    if unknown:
        raise ValueError(f"Unknown Scryfall finish values: {sorted(unknown)}")
    if not values:
        raise ValueError(f"Paper Scryfall object has no finish evidence: {clean(card.get('id')) or '<missing-id>'}")
    return tuple(sorted(values))


def physical_print_key(card: dict, finish: str) -> str:
    scryfall_id = clean(card.get("id")).lower()
    if not scryfall_id:
        raise ValueError("MTG physical Print requires a Scryfall object id")
    finish = clean(finish).lower()
    if finish not in KNOWN_FINISHES:
        raise ValueError(f"Unknown Scryfall finish value: {finish or '<blank>'}")
    return f"mtg:scryfall:{scryfall_id}:{finish}"


def exact_print_keys(card: dict) -> tuple[str, ...]:
    return tuple(physical_print_key(card, finish) for finish in finish_values(card))
