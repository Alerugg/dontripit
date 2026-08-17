from __future__ import annotations

from collections.abc import Iterator
from datetime import date, datetime

from sqlalchemy import text

from app.search_v2.normalization import build_search_text, normalize_search_text


MTG_CARD_TYPES = (
    "Artifact",
    "Battle",
    "Conspiracy",
    "Creature",
    "Dungeon",
    "Enchantment",
    "Instant",
    "Kindred",
    "Land",
    "Phenomenon",
    "Plane",
    "Planeswalker",
    "Scheme",
    "Sorcery",
    "Vanguard",
)


def _clean_list(value) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    seen: set[str] = set()
    for item in value:
        clean = str(item or "").strip()
        if clean and clean.casefold() not in seen:
            seen.add(clean.casefold())
            result.append(clean)
    return result


def _card_types(type_line: object) -> list[str]:
    left = str(type_line or "").split("—", 1)[0].split("-", 1)[0]
    words = {word.strip(" ,/") for word in left.split()}
    return [value for value in MTG_CARD_TYPES if value in words]


def _number(value):
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number.is_integer():
        return int(number)
    return number


def _year(value) -> int | None:
    if isinstance(value, (date, datetime)):
        return int(value.year)
    text_value = str(value or "").strip()
    if len(text_value) >= 4 and text_value[:4].isdigit():
        return int(text_value[:4])
    return None


def compact_card_attributes(raw: dict | None) -> dict:
    raw = dict(raw or {})
    attrs = {
        "layout": str(raw.get("layout") or "").strip() or None,
        "mana_value": _number(raw.get("mana_value")),
        "type_line": str(raw.get("type_line") or "").strip() or None,
        "card_types": _card_types(raw.get("type_line")),
        "colors": _clean_list(raw.get("colors")),
        "color_identity": _clean_list(raw.get("color_identity")),
        "keywords": _clean_list(raw.get("keywords")),
    }
    return {key: value for key, value in attrs.items() if value not in (None, "", [], {})}


def compact_print_attributes(raw: dict | None) -> dict:
    raw = dict(raw or {})
    attrs = {
        "release_year": _year(raw.get("released_at")),
        "set_type": str(raw.get("set_type") or "").strip() or None,
        "artist": str(raw.get("artist") or "").strip() or None,
        "frame": str(raw.get("frame") or "").strip() or None,
        "frame_effects": _clean_list(raw.get("frame_effects")),
        "border_color": str(raw.get("border_color") or "").strip() or None,
        "security_stamp": str(raw.get("security_stamp") or "").strip() or None,
        "promo": bool(raw.get("promo")),
        "promo_types": _clean_list(raw.get("promo_types")),
        "full_art": bool(raw.get("full_art")),
        "textless": bool(raw.get("textless")),
        "booster": bool(raw.get("booster")),
        "reprint": bool(raw.get("reprint")),
        "oversized": bool(raw.get("oversized")),
        "story_spotlight": bool(raw.get("story_spotlight")),
        "reserved": bool(raw.get("reserved")),
    }
    return {key: value for key, value in attrs.items() if value not in (None, "", [], {}) or isinstance(value, bool)}


def iter_mtg_card_profiles(session) -> Iterator[dict]:
    rows = session.execute(
        text(
            """
            SELECT c.id AS card_id, c.name, c.oracle_id, c.card_key, ca.attributes_json
            FROM cards c
            JOIN games g ON g.id=c.game_id
            JOIN card_attributes ca ON ca.card_id=c.id
            WHERE g.slug='mtg'
            ORDER BY c.id
            """
        )
    ).mappings()
    for row in rows:
        raw = dict(row["attributes_json"] or {})
        attrs = compact_card_attributes(raw)
        aliases = []
        for face in raw.get("faces") or []:
            if isinstance(face, dict):
                face_name = str(face.get("name") or "").strip()
                if face_name and normalize_search_text(face_name) != normalize_search_text(row["name"]):
                    aliases.append(face_name)
        aliases = _clean_list(aliases)
        yield {
            "card_id": int(row["card_id"]),
            "normalized_name": normalize_search_text(row["name"]),
            "aliases_json": aliases,
            "keywords_json": [],
            "attributes_json": attrs,
            "search_text": build_search_text(
                row["name"],
                row["oracle_id"],
                row["card_key"],
                aliases,
                attrs.get("type_line"),
                attrs.get("card_types"),
                attrs.get("color_identity"),
                attrs.get("keywords"),
            ),
        }


def iter_mtg_print_profiles(session) -> Iterator[dict]:
    rows = session.execute(
        text(
            """
            SELECT
              p.id AS print_id,
              p.card_id,
              c.name,
              c.oracle_id,
              p.scryfall_id,
              s.code AS set_code,
              s.name AS set_name,
              p.collector_number,
              p.language,
              p.rarity,
              p.variant,
              pa.attributes_json
            FROM prints p
            JOIN cards c ON c.id=p.card_id
            JOIN games g ON g.id=c.game_id
            JOIN sets s ON s.id=p.set_id
            JOIN print_attributes pa ON pa.print_id=p.id
            WHERE g.slug='mtg'
            ORDER BY p.id
            """
        )
    ).mappings()
    for row in rows:
        raw = dict(row["attributes_json"] or {})
        attrs = compact_print_attributes(raw)
        printed_name = str(raw.get("printed_name") or "").strip()
        flavor_name = str(raw.get("flavor_name") or "").strip()
        aliases = _clean_list(
            [
                value
                for value in (printed_name, flavor_name)
                if value and normalize_search_text(value) != normalize_search_text(row["name"])
            ]
        )
        exact_variant = str(row["variant"] or "").strip().lower() or "nonfoil"
        yield {
            "print_id": int(row["print_id"]),
            "card_id": int(row["card_id"]),
            "normalized_name": normalize_search_text(row["name"]),
            "normalized_set_code": normalize_search_text(row["set_code"]).replace(" ", "-"),
            "normalized_collector_number": normalize_search_text(row["collector_number"]).replace(" ", "-"),
            "language": str(row["language"] or "").strip().lower() or None,
            "rarity": str(row["rarity"] or "").strip().lower() or None,
            "exact_variant": exact_variant,
            "variant_family": "finish",
            "release_names_json": [],
            "aliases_json": aliases,
            "keywords_json": [],
            "attributes_json": attrs,
            "search_text": build_search_text(
                row["name"],
                aliases,
                row["collector_number"],
                row["set_code"],
                row["set_name"],
                row["rarity"],
                exact_variant,
                row["scryfall_id"],
                row["oracle_id"],
                attrs.get("artist"),
                attrs.get("set_type"),
                attrs.get("frame_effects"),
                attrs.get("promo_types"),
            ),
        }
