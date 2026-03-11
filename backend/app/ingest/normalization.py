from __future__ import annotations

import re
from hashlib import sha1


def trim_or_none(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def canonical_text_slug(value: object, default: str = "unknown") -> str:
    text = (trim_or_none(value) or "").lower()
    text = re.sub(r"[\s_/]+", "-", text)
    text = re.sub(r"[^a-z0-9-]", "", text)
    text = re.sub(r"-+", "-", text).strip("-")
    return text or default


def normalize_collector_number(value: object) -> str:
    raw = trim_or_none(value)
    if raw is None:
        return "unknown"

    compact = re.sub(r"\s+", "", raw)
    match = re.match(r"^(\d+)([a-zA-Z]+)?$", compact)
    if match:
        numeric = str(int(match.group(1)))
        suffix = (match.group(2) or "").lower()
        return f"{numeric}{suffix}"

    return compact.lower()


_LANGUAGE_ALIASES = {
    "english": "en",
    "en-us": "en",
    "en-gb": "en",
    "spanish": "es",
    "es-es": "es",
    "japanese": "ja",
    "jp": "ja",
    "deutsch": "de",
}


def normalize_language(value: object, default: str = "en") -> str:
    raw = (trim_or_none(value) or "").lower()
    if not raw:
        return default
    return _LANGUAGE_ALIASES.get(raw, raw)


def normalize_rarity(value: object) -> str:
    rarity = trim_or_none(value)
    return rarity if rarity else "unknown"


def normalize_variant(value: object) -> str:
    return canonical_text_slug(value, default="default")


def normalize_finish(*, is_foil: bool | None = None, variant: object = None) -> str:
    if is_foil:
        return "foil"
    variant_text = (trim_or_none(variant) or "").lower()
    if "foil" in variant_text:
        return "foil"
    return "nonfoil"


def build_card_key(
    *,
    game_slug: str,
    canonical_name: str,
    identity_hints: dict | None = None,
    external_ids: list[dict] | None = None,
) -> str:
    hints = identity_hints or {}
    oracle_id = trim_or_none(hints.get("oracle_id"))
    if oracle_id:
        return f"{canonical_text_slug(game_slug)}:oracle:{oracle_id.lower()}"

    if external_ids:
        for item in external_ids:
            source = canonical_text_slug(item.get("source"), default="source")
            id_type = canonical_text_slug(item.get("id_type"), default="id")
            value = trim_or_none(item.get("value"))
            if value:
                return f"{canonical_text_slug(game_slug)}:{source}:{id_type}:{value.lower()}"

    name_slug = canonical_text_slug(canonical_name)
    hash_suffix = sha1(canonical_name.lower().encode("utf-8")).hexdigest()[:10]
    return f"{canonical_text_slug(game_slug)}:name:{name_slug}:{hash_suffix}"


def build_print_key(
    *,
    card_key: str,
    set_code: str,
    collector_number: object,
    language: object,
    finish: str,
    variant: object,
) -> str:
    collector_norm = normalize_collector_number(collector_number)
    language_norm = normalize_language(language)
    variant_norm = normalize_variant(variant)
    finish_norm = canonical_text_slug(finish, default="nonfoil")
    return (
        f"{card_key}|{canonical_text_slug(set_code)}|{collector_norm}|"
        f"{language_norm}|{finish_norm}|{variant_norm}"
    )
