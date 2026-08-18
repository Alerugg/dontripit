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
    if not isinstance(value, (list, tuple)):
        return []
    result: list[str] = []
    seen: set[str] = set()
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
        "banlist_tcg": banlist.get("ban_tcg"),
        "banlist_ocg": banlist.get("ban_ocg"),
        "banlist_goat": banlist.get("ban_goat"),
    }
    return {key: value for key, value in attrs.items() if value not in (None, "", [], {})}


def iter_yugioh_card_profiles(session) -> Iterator[dict]:
    rows = session.execute(
        text(
            """
            SELECT c.id AS card_id, c.name, c.yugoprodeck_id, ca.attributes_json
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
        search_keywords = [
            attrs.get("card_class"), attrs.get("card_type"), attrs.get("frame_type"),
            attrs.get("attribute"), attrs.get("race"), attrs.get("archetype"),
        ]
        canonical_source_id = str(row["yugoprodeck_id"] or "").strip()
        yield {
            "card_id": int(row["card_id"]),
            "normalized_name": normalize_search_text(row["name"]),
            "aliases_json": source_aliases,
            "keywords_json": [],
            "attributes_json": attrs,
            "search_text": build_search_text(
                row["name"], canonical_source_id, source_aliases,
                [value for value in search_keywords if value],
            ),
        }


def iter_yugioh_print_profiles(session) -> Iterator[dict]:
    """Yield exactly one Search V2 profile for each physical Yu-Gi-Oh Print.

    Physical language lives on ``prints.language``. EN prints use canonical Card/Set
    names because they do not require PrintLocalization rows. ES/JA use the exact
    localization attached to that physical print. Multiple PrintRelease memberships
    are aggregated, never multiplied into duplicate search-profile rows.
    """
    rows = session.execute(
        text(
            """
            SELECT
              p.id AS print_id,
              p.card_id,
              c.name AS canonical_name,
              c.yugoprodeck_id,
              s.code AS set_code,
              s.name AS canonical_set_name,
              p.collector_number,
              lower(coalesce(p.language,'')) AS language,
              p.rarity,
              p.variant,
              pl.card_name AS localized_name,
              pl.set_name AS localized_set_name,
              COALESCE(rel.release_names, ARRAY[]::text[]) AS release_names,
              COALESCE(rel.release_codes, ARRAY[]::text[]) AS release_codes,
              rel.first_release_date
            FROM prints p
            JOIN cards c ON c.id=p.card_id
            JOIN games g ON g.id=c.game_id
            JOIN sets s ON s.id=p.set_id
            LEFT JOIN print_localizations pl
              ON pl.print_id=p.id AND lower(pl.language)=lower(coalesce(p.language,''))
            LEFT JOIN LATERAL (
              SELECT
                array_agg(DISTINCT cr.name ORDER BY cr.name) FILTER (WHERE cr.name IS NOT NULL) AS release_names,
                array_agg(DISTINCT cr.code ORDER BY cr.code) FILTER (WHERE cr.code IS NOT NULL) AS release_codes,
                MIN(cr.release_date) AS first_release_date
              FROM print_releases pr
              JOIN catalog_releases cr ON cr.id=pr.release_id
              WHERE pr.print_id=p.id
            ) rel ON TRUE
            WHERE g.slug='yugioh'
              AND lower(coalesce(p.language,'')) IN ('en','es','ja')
            ORDER BY p.id
            """
        )
    ).mappings()

    for row in rows:
        language = str(row["language"] or "").strip().lower() or None
        localized_name = str(row["localized_name"] or "").strip()
        canonical_name = str(row["canonical_name"] or "").strip()
        display_name = localized_name or canonical_name
        display_set_name = str(row["localized_set_name"] or row["canonical_set_name"] or "").strip()
        release_names = _clean_list(row["release_names"])
        release_codes = _clean_list(row["release_codes"])
        release_date = row["first_release_date"]
        release_year = release_date.year if release_date is not None else None
        attributes = {"release_year": release_year} if release_year is not None else {}
        aliases = [canonical_name] if localized_name and localized_name != canonical_name else []

        yield {
            "print_id": int(row["print_id"]),
            "card_id": int(row["card_id"]),
            "normalized_name": normalize_search_text(display_name),
            "normalized_set_code": normalize_search_text(row["set_code"]).replace(" ", "-"),
            "normalized_collector_number": normalize_search_text(row["collector_number"]).replace(" ", "-"),
            "language": language,
            "rarity": row["rarity"],
            "exact_variant": row["variant"],
            "variant_family": "rarity",
            "release_names_json": release_names,
            "aliases_json": aliases,
            "keywords_json": [],
            "attributes_json": attributes,
            "search_text": build_search_text(
                display_name,
                canonical_name,
                row["collector_number"],
                row["set_code"],
                display_set_name,
                row["rarity"],
                release_names,
                release_codes,
                row["yugoprodeck_id"],
                language,
            ),
        }
