from __future__ import annotations

from collections.abc import Iterable
from typing import Any


_PLACEHOLDER_VALUES = {
    "",
    "-",
    "?",
    "n/a",
    "na",
    "none",
    "null",
    "unknown",
    "undefined",
}

_OPTIONAL_PHYSICAL_FIELDS = {
    "set_code",
    "set_name",
    "collector_number",
    "language",
    "display_language",
    "rarity",
    "exact_variant",
    "variant_family",
    "finish",
}


def clean_optional_metadata(value: Any) -> Any:
    """Return None for source placeholders while preserving real values."""
    if value is None:
        return None
    if not isinstance(value, str):
        return value
    text = value.strip()
    if text.lower() in _PLACEHOLDER_VALUES:
        return None
    return text


def sanitize_search_item(item: dict[str, Any]) -> dict[str, Any]:
    """Sanitize physical metadata without changing identity or ranking."""
    sanitized = dict(item)
    for key in _OPTIONAL_PHYSICAL_FIELDS:
        if key in sanitized:
            sanitized[key] = clean_optional_metadata(sanitized[key])

    matched = sanitized.get("matched_print")
    if isinstance(matched, dict):
        matched_clean = dict(matched)
        for key in _OPTIONAL_PHYSICAL_FIELDS:
            if key in matched_clean:
                matched_clean[key] = clean_optional_metadata(matched_clean[key])
        sanitized["matched_print"] = matched_clean
    return sanitized


def sanitize_search_items(items: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return [sanitize_search_item(item) for item in items]


def sanitize_search_result(result: dict[str, Any]) -> dict[str, Any]:
    """Sanitize an advanced-search result envelope when it contains items."""
    sanitized = dict(result)
    items = sanitized.get("items")
    if isinstance(items, list):
        sanitized["items"] = sanitize_search_items(items)
    results = sanitized.get("results")
    if isinstance(results, list):
        sanitized["results"] = sanitize_search_items(results)
    return sanitized
