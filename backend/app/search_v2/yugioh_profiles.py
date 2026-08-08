from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import text

from app.search_v2.normalization import build_search_text, normalize_search_text


def _card_class(frame_type: object) -> str:
    frame = str(frame_type or "").strip().lower()
    if frame == "spell":
        return "Spell"
    if frame == "trap":
        return "Trap"
    if frame == "skill":
        return "Skill"
    if frame == "token":
        return "Token"
    return "Monster"


def _int_or_none(value):
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _clean_list(value) -> list[str]:
    if not isinstance(value, list):
        return []
    result = []
    seen = set()
    for item in value:
        clean = str(item or "").strip()
        if clean and clean not in seen:
            seen.add(clean)
            result.append(clean)
    return result


def compact_card_attributes(raw: dict | None) -> dict:
    raw = dict(raw or {})
    banlist = raw.get("banlist_info") if isinstance(raw.get("banlist_info"), dict) else {}
    attrs = {
        "card_class": _card_class(raw.get("frame_type")),
        "card_type": raw.get("category") or raw.get("type"),
        "frame_type": raw.get("frame_type"),
        "attribute": raw.get("attribute"),
        "race": raw.get("race"),
        "archetype": raw.get("archetype"),
        "level": _int_or_none(raw.get("level")),
        "rank": _int_or_none(raw.get("rank")),
        "atk": _int_or_none(raw.get("atk")),
        "def": _int_or_none(raw.get("def")),
        "pendulum_scale": _int_or_none(raw.get("scale")),
        "link_value": _int_or_none(raw.get("link_value")),
        "link_markers": _clean_list(raw.get("link_markers")),
        # Keep normalized rule data available for later activation, but the facet
        # definition remains inactive until status semantics are separately certified.
        "banlist_tcg": banlist.get("ban_tcg"),
        "banlist_ocg": banlist.get("ban_ocg"),
        "banlist_goat": banlist.get("ban_goat"),
    }
    return {
        key: value
        for key, value in attrs.items()
        if value not in (None, "", [], {})
    }


def iter_yugioh_card_profiles(session) -> Iterator[dict]:
    rows = session.execute(
        text(
            """
            SELECT
              c.id AS card_id,
              c.name,
              c.yugoprodeck_id,
              ca.attributes_json
            FROM cards c
            JOIN games g ON g.id=c.game_id
            JOIN card_attributes ca ON ca.card_id=c.id
            WHERE g.slug='yugioh'
            ORDER BY c.id
            """
        )
    ).mappings()

    for row in rows:
        raw = row["attributes_json"] or {}
        attrs = compact_card_attributes(raw)
        source_aliases = _clean_list(raw.get("source_alias_ids"))
        aliases = [str(row["yugoprodeck_id"] or "").strip(), *source_aliases]
        aliases = [value for value in aliases if value]
        keywords = [
            attrs.get("card_class"),
            attrs.get("card_type"),
            attrs.get("frame_type"),
            attrs.get("attribute"),
            attrs.get("race"),
            attrs.get("archetype"),
        ]
        yield {
            "card_id": int(row["card_id"]),
            "normalized_name": normalize_search_text(row["name"]),
            "aliases_json": aliases,
            "keywords_json": [value for value in keywords if value],
            "attributes_json": attrs,
            "search_text": build_search_text(row["name"], aliases, keywords),
        }


def iter_yugioh_print_profiles(session) -> Iterator[dict]:
    rows = session.execute(
        text(
            """
            SELECT
              p.id AS print_id,
              p.card_id,
              c.name,
              c.yugoprodeck_id,
              s.code AS set_code,
              p.collector_number,
              p.language,
              p.rarity,
              p.variant,
              cr.name AS release_name,
              cr.code AS release_code,
              cr.release_date
            FROM prints p
            JOIN cards c ON c.id=p.card_id
            JOIN games g ON g.id=c.game_id
            JOIN sets s ON s.id=p.set_id
            JOIN print_releases pr ON pr.print_id=p.id
            JOIN catalog_releases cr ON cr.id=pr.release_id
            WHERE g.slug='yugioh'
            ORDER BY p.id
            """
        )
    ).mappings()

    for row in rows:
        release_year = row["release_date"].year if row["release_date"] is not None else None
        attributes = {"release_year": release_year} if release_year is not None else {}
        aliases = [
            row["collector_number"],
            row["set_code"],
            row["yugoprodeck_id"],
            row["release_code"],
        ]
        aliases = [str(value).strip() for value in aliases if str(value or "").strip()]
        release_names = [row["release_name"]] if row["release_name"] else []
        keywords = [row["rarity"], row["release_name"]]
        yield {
            "print_id": int(row["print_id"]),
            "card_id": int(row["card_id"]),
            "normalized_name": normalize_search_text(row["name"]),
            "normalized_set_code": normalize_search_text(row["set_code"]).replace(" ", "-"),
            "normalized_collector_number": normalize_search_text(row["collector_number"]).replace(" ", "-"),
            "language": str(row["language"] or "").strip().lower() or None,
            "rarity": row["rarity"],
            "exact_variant": row["variant"],
            "variant_family": "rarity",
            "release_names_json": release_names,
            "aliases_json": aliases,
            "keywords_json": [value for value in keywords if value],
            "attributes_json": attributes,
            "search_text": build_search_text(
                row["name"],
                row["collector_number"],
                row["set_code"],
                row["rarity"],
                row["release_name"],
                row["release_code"],
                row["yugoprodeck_id"],
            ),
        }
