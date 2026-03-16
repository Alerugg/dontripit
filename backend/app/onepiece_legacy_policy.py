from __future__ import annotations

import re

_ONEPIECE_OFFICIAL_HOST = "en.onepiece-cardgame.com"
_ONEPIECE_LEGACY_FAKE_HOST = "example.cdn.onepiece"
_PLACEHOLDER_HOST = "placehold.co"

_LEGACY_EXTERNAL_ID_RE = re.compile(r".+-(?:default|parallel)-[a-z]{2,5}$", re.IGNORECASE)
_CANONICAL_EXTERNAL_ID_RE = re.compile(
    r"^(?:"
    r"(?:OP|ST|EB)\d{2}-\d{3}(?:_[pr]\d+)?"
    r"|P-\d{3}(?:_[pr]\d+)?"
    r")$",
    re.IGNORECASE,
)


def is_onepiece_official_image(url: str | None) -> bool:
    return _ONEPIECE_OFFICIAL_HOST in str(url or "").strip().lower()


def is_onepiece_placeholder_or_fake_image(url: str | None) -> bool:
    lowered = str(url or "").strip().lower()
    return _PLACEHOLDER_HOST in lowered or _ONEPIECE_LEGACY_FAKE_HOST in lowered


def is_onepiece_legacy_external_id(external_id: str | None) -> bool:
    value = str(external_id or "").strip()
    if not value:
        return False
    return _LEGACY_EXTERNAL_ID_RE.fullmatch(value) is not None


def is_onepiece_canonical_external_id(external_id: str | None) -> bool:
    value = str(external_id or "").strip()
    if not value:
        return False
    return _CANONICAL_EXTERNAL_ID_RE.fullmatch(value) is not None


def is_legacy_onepiece_print(
    *,
    game_slug: str | None,
    primary_image_url: str | None,
    external_id: str | None,
) -> bool:
    if str(game_slug or "").strip().lower() != "onepiece":
        return False

    has_placeholder_image = is_onepiece_placeholder_or_fake_image(primary_image_url)
    has_legacy_identifier = is_onepiece_legacy_external_id(external_id) and not is_onepiece_canonical_external_id(external_id)
    return has_placeholder_image or has_legacy_identifier
