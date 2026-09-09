from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
import hashlib
import json
import re
import unicodedata
from typing import Iterable, Mapping


class KnowledgeState(StrEnum):
    """Meaning of a physical-identity dimension.

    UNKNOWN means the source could know the dimension but did not provide it.
    NOT_APPLICABLE means the dimension has no identity meaning for this object.
    KNOWN means a source supplied a concrete value.
    """

    UNKNOWN = "unknown"
    NOT_APPLICABLE = "not_applicable"
    KNOWN = "known"


class MarketRelationship(StrEnum):
    EXACT_ONE_PHYSICAL = "exact_one_physical"
    GROUPED_PHYSICAL = "grouped_physical"
    ALIAS_SAME_PHYSICAL = "alias_same_physical"
    CANDIDATE = "candidate"
    AMBIGUOUS = "ambiguous"
    OUT_OF_SCOPE = "out_of_scope"


@dataclass(frozen=True, slots=True)
class IdentityValue:
    state: KnowledgeState
    values: tuple[str, ...] = ()

    @classmethod
    def unknown(cls) -> "IdentityValue":
        return cls(KnowledgeState.UNKNOWN, ())

    @classmethod
    def not_applicable(cls) -> "IdentityValue":
        return cls(KnowledgeState.NOT_APPLICABLE, ())

    @classmethod
    def known(cls, *values: object) -> "IdentityValue":
        normalized = tuple(
            sorted(
                {
                    normalize_token(value)
                    for value in values
                    if normalize_token(value)
                }
            )
        )
        if not normalized:
            return cls.unknown()
        return cls(KnowledgeState.KNOWN, normalized)

    def as_json(self) -> dict:
        return {"state": self.state.value, "values": list(self.values)}


@dataclass(frozen=True, slots=True)
class PhysicalIdentityDescriptor:
    game: str
    source: str
    source_print_id: str
    card_concept: str
    release: str
    collector_number: IdentityValue = field(default_factory=IdentityValue.unknown)
    language: IdentityValue = field(default_factory=IdentityValue.unknown)
    region: IdentityValue = field(default_factory=IdentityValue.unknown)
    rarity: IdentityValue = field(default_factory=IdentityValue.unknown)
    finish: IdentityValue = field(default_factory=IdentityValue.unknown)
    edition: IdentityValue = field(default_factory=IdentityValue.unknown)
    version: IdentityValue = field(default_factory=IdentityValue.unknown)
    stamp: IdentityValue = field(default_factory=IdentityValue.unknown)
    treatment: IdentityValue = field(default_factory=IdentityValue.unknown)
    artwork: IdentityValue = field(default_factory=IdentityValue.unknown)
    reprint_family: IdentityValue = field(default_factory=IdentityValue.unknown)
    errata_revision: IdentityValue = field(default_factory=IdentityValue.unknown)
    promo_type: IdentityValue = field(default_factory=IdentityValue.unknown)
    size: IdentityValue = field(default_factory=IdentityValue.unknown)
    source_facts: Mapping[str, object] = field(default_factory=dict, compare=False, hash=False)

    _DIMENSIONS = (
        "collector_number",
        "language",
        "region",
        "rarity",
        "finish",
        "edition",
        "version",
        "stamp",
        "treatment",
        "artwork",
        "reprint_family",
        "errata_revision",
        "promo_type",
        "size",
    )

    def canonical_payload(self) -> dict:
        return {
            "game": normalize_token(self.game),
            "card_concept": normalize_token(self.card_concept),
            "release": normalize_token(self.release),
            "dimensions": {
                name: getattr(self, name).as_json()
                for name in self._DIMENSIONS
            },
        }

    def fingerprint(self) -> str:
        """Stable hash of the complete known/unknown physical descriptor.

        Unknown is intentionally encoded. An identity lacking edition evidence must
        never hash like a proven Unlimited or First Edition identity.
        """
        encoded = json.dumps(
            self.canonical_payload(),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def missing_dimensions(self, required: Iterable[str]) -> tuple[str, ...]:
        missing = []
        for dimension in required:
            if dimension not in self._DIMENSIONS:
                raise ValueError(f"unknown physical identity dimension: {dimension}")
            value: IdentityValue = getattr(self, dimension)
            if value.state == KnowledgeState.UNKNOWN:
                missing.append(dimension)
        return tuple(missing)


@dataclass(frozen=True, slots=True)
class MarketIdentityEvidence:
    market: str
    external_product_id: str
    relationship: MarketRelationship
    physical_fingerprints: tuple[str, ...]
    evidence_sources: tuple[str, ...]
    reason: str

    def __post_init__(self) -> None:
        if self.relationship == MarketRelationship.EXACT_ONE_PHYSICAL and len(self.physical_fingerprints) != 1:
            raise ValueError("exact_one_physical requires exactly one physical fingerprint")
        if self.relationship == MarketRelationship.GROUPED_PHYSICAL and len(self.physical_fingerprints) < 2:
            raise ValueError("grouped_physical requires at least two physical fingerprints")
        if self.relationship == MarketRelationship.ALIAS_SAME_PHYSICAL and len(self.physical_fingerprints) != 1:
            raise ValueError("alias_same_physical records one shared physical fingerprint")


def normalize_token(value: object) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).strip().casefold()
    text = text.replace("’", "'")
    text = re.sub(r"\s+", " ", text)
    return text


def rarity_family(value: object) -> str:
    """Normalize spelling/casing, not game semantics.

    It is intentionally conservative. Secret Rare and Quarter Century Secret Rare
    do not collapse into one family here. Game adapters may add aliases only when
    explicitly certified.
    """
    text = normalize_token(value)
    aliases = {
        "common": "common",
        "c": "common",
        "uncommon": "uncommon",
        "u": "uncommon",
        "rare": "rare",
        "r": "rare",
        "super": "super rare",
        "super rare": "super rare",
        "sr": "super rare",
        "ultra": "ultra rare",
        "ultra rare": "ultra rare",
        "ur": "ultra rare",
        "secret": "secret rare",
        "secret rare": "secret rare",
        "scr": "secret rare",
    }
    return aliases.get(text, text)
