from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable


_SPACE_RE = re.compile(r"\s+")
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")
_ONEPIECE_SET_RE = re.compile(r"^(op|st|eb|prb)[\s\-_]?(\d{1,2})$", re.IGNORECASE)
_ONEPIECE_COLLECTOR_RE = re.compile(
    r"^(?:(op|st|eb|prb)[\s\-_]?(\d{1,2})[\s\-_]?(\d{3})|(p)[\s\-_]?(\d{3}))$",
    re.IGNORECASE,
)


LANGUAGE_ALIASES = {
    "en": {"en", "eng", "english", "ingles", "inglés"},
    "ja": {"ja", "jp", "jpn", "japanese", "japones", "japonés"},
    "es": {"es", "spa", "spanish", "espanol", "español"},
    "fr": {"fr", "fra", "french", "frances", "francés"},
    "de": {"de", "ger", "german", "aleman", "alemán"},
    "it": {"it", "ita", "italian", "italiano"},
    "pt": {"pt", "por", "portuguese", "portugues", "portugués"},
    "zh": {"zh", "cn", "chinese", "chino"},
    "ko": {"ko", "kr", "korean", "coreano"},
}


def _ascii_fold(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    return "".join(ch for ch in text if not unicodedata.combining(ch)).lower()


def normalize_search_text(value: object) -> str:
    """Normalize human-entered text without destroying token boundaries.

    Examples:
    - ``Monkey.D.Luffy`` -> ``monkey d luffy``
    - ``Pokémon`` -> ``pokemon``
    - ``OP05-119`` -> ``op05 119``
    """
    folded = _ascii_fold(value)
    folded = _NON_ALNUM_RE.sub(" ", folded)
    return _SPACE_RE.sub(" ", folded).strip()


def compact_search_text(value: object) -> str:
    """Return an alphanumeric compact form useful for exact codes/numbers."""
    return re.sub(r"[^a-z0-9]", "", _ascii_fold(value))


def normalize_onepiece_set_code(value: object) -> str | None:
    raw = normalize_search_text(value).replace(" ", "")
    match = _ONEPIECE_SET_RE.fullmatch(raw)
    if not match:
        return None
    prefix, number = match.groups()
    return f"{prefix.lower()}-{int(number):02d}"


def normalize_onepiece_collector_number(value: object) -> str | None:
    raw = compact_search_text(value)
    match = _ONEPIECE_COLLECTOR_RE.fullmatch(raw)
    if not match:
        return None
    family, set_no, card_no, promo_family, promo_no = match.groups()
    if promo_family:
        return f"p-{promo_no}"
    return f"{family.lower()}{int(set_no):02d}-{card_no}"


def normalize_language(value: object) -> str | None:
    normalized = normalize_search_text(value)
    if not normalized:
        return None
    compact = normalized.replace(" ", "")
    for code, aliases in LANGUAGE_ALIASES.items():
        normalized_aliases = {normalize_search_text(alias).replace(" ", "") for alias in aliases}
        if compact in normalized_aliases:
            return code
    return compact


def variant_family(value: object) -> str:
    normalized = normalize_search_text(value).replace(" ", "") or "default"
    if re.fullmatch(r"p\d+", normalized):
        return "parallel"
    if re.fullmatch(r"r\d+", normalized):
        return "reprint"
    return normalized


def unique_normalized(values: Iterable[object]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = normalize_search_text(value)
        if normalized and normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    return result


def build_search_text(*values: object) -> str:
    """Build a de-duplicated text document for trigram/FTS matching."""
    flattened: list[object] = []
    for value in values:
        if value is None:
            continue
        if isinstance(value, dict):
            for key, nested in value.items():
                flattened.extend([key, nested])
        elif isinstance(value, (list, tuple, set)):
            flattened.extend(value)
        else:
            flattened.append(value)
    return " ".join(unique_normalized(flattened))
