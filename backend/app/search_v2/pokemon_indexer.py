from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime

from sqlalchemy import delete, select, text

from app.models import Card, Game, Print, Set
from app.search_v2.facets import facets_for_game
from app.search_v2.normalization import build_search_text, compact_search_text, normalize_search_text
from app.search_v2_models import CardSearchProfile, FacetDefinition, PrintSearchProfile


LANGUAGE_LABELS = {
    "en": "English",
    "ja": "Japanese",
    "es": "Spanish",
    "fr": "French",
    "de": "German",
    "it": "Italian",
    "pt": "Portuguese",
    "zh": "Chinese",
    "ko": "Korean",
}


def _year(value: object) -> int | None:
    if isinstance(value, (date, datetime)):
        return int(value.year)
    clean = str(value or "").strip()
    if len(clean) >= 4 and clean[:4].isdigit():
        return int(clean[:4])
    return None


def _card_attrs(raw: dict | None) -> dict:
    raw = raw or {}
    return {
        "category": raw.get("category"),
        "dex_id": list(raw.get("dex_id") or []),
        "hp": raw.get("hp"),
        "types": list(raw.get("types") or []),
        "evolve_from": raw.get("evolve_from"),
        "stage": raw.get("stage"),
        "suffix": raw.get("suffix"),
        "trainer_type": raw.get("trainer_type"),
        "energy_type": raw.get("energy_type"),
        "abilities": list(raw.get("abilities") or []),
        "attacks": list(raw.get("attacks") or []),
        "weaknesses": list(raw.get("weaknesses") or []),
        "resistances": list(raw.get("resistances") or []),
        "retreat": raw.get("retreat"),
    }


def _print_attrs(raw: dict | None, *, card_attrs: dict, rarity: str | None) -> dict:
    raw = raw or {}
    physical = raw.get("physical_variant") if isinstance(raw.get("physical_variant"), dict) else {}
    release_year = _year(raw.get("release_date"))
    return {
        **card_attrs,
        "rarity": rarity,
        "illustrator": raw.get("illustrator"),
        "regulation_mark": raw.get("regulation_mark"),
        "series": raw.get("series_name"),
        "series_id": raw.get("series_id"),
        "release_year": release_year,
        "finish": physical.get("type"),
        "foil_pattern": physical.get("foil"),
        "stamps": list(physical.get("stamps") or []),
        "variant_subtype": physical.get("subtype"),
        "release_context": physical.get("release_context"),
        "size": physical.get("size"),
        "variant_hash": physical.get("variant_hash"),
        "variant_baseline_reused": physical.get("baseline_reused"),
    }


def rebuild_pokemon_search_v2(session) -> dict:
    """Rebuild Pokémon Search V2 strictly from certified canonical attributes.

    Cards without `card_attributes` are intentionally excluded. This prevents
    preserved stale/legacy rows from silently entering the certified English
    search surface.
    """
    if session.bind.dialect.name != "postgresql":
        raise RuntimeError("Pokémon Search V2 rebuild requires PostgreSQL")

    game = session.execute(select(Game).where(Game.slug == "pokemon")).scalar_one()

    session.execute(delete(CardSearchProfile).where(CardSearchProfile.game_id == game.id))
    session.execute(delete(PrintSearchProfile).where(PrintSearchProfile.game_id == game.id))
    session.execute(delete(FacetDefinition).where(FacetDefinition.game_id == game.id))

    canonical_card_rows = session.execute(text(
        """
        SELECT c.id, c.name, c.card_key, c.tcgdex_id, ca.attributes_json
        FROM cards c
        JOIN card_attributes ca ON ca.card_id=c.id
        WHERE c.game_id=:game
          AND ca.source='tcgdex/cards-database'
        ORDER BY c.id
        """
    ), {"game": game.id}).mappings().all()
    card_ids = [int(row["id"]) for row in canonical_card_rows]
    if not card_ids:
        raise AssertionError("No certified Pokémon card_attributes found; refuse to build an empty index")

    print_rows = session.execute(text(
        """
        SELECT
          p.id, p.card_id, p.set_id, p.collector_number, p.language,
          p.rarity, p.variant, p.is_foil, p.print_key, p.tcgdex_id,
          pa.attributes_json AS print_attributes,
          s.code AS set_code, s.name AS set_name, s.release_date,
          pi.url AS primary_image_url
        FROM prints p
        JOIN sets s ON s.id=p.set_id
        JOIN print_attributes pa ON pa.print_id=p.id
        LEFT JOIN LATERAL (
          SELECT url FROM print_images
          WHERE print_id=p.id
          ORDER BY is_primary DESC, id ASC
          LIMIT 1
        ) pi ON TRUE
        WHERE p.card_id = ANY(:card_ids)
          AND pa.source='tcgdex/cards-database'
        ORDER BY p.id
        """
    ), {"card_ids": card_ids}).mappings().all()

    card_by_id = {int(row["id"]): row for row in canonical_card_rows}
    prints_by_card: dict[int, list[dict]] = defaultdict(list)
    for row in print_rows:
        prints_by_card[int(row["card_id"])].append(dict(row))

    card_profiles: list[CardSearchProfile] = []
    print_profiles: list[PrintSearchProfile] = []
    coverage = defaultdict(int)

    for card_row in canonical_card_rows:
        card_id = int(card_row["id"])
        attrs = _card_attrs(card_row["attributes_json"] or {})
        for key, value in attrs.items():
            if value not in (None, "", [], {}):
                coverage[f"card.{key}"] += 1

        card_prints = prints_by_card.get(card_id, [])
        collectors = [row.get("collector_number") for row in card_prints if row.get("collector_number")]
        sets = [row.get("set_code") for row in card_prints if row.get("set_code")]
        rarities = [row.get("rarity") for row in card_prints if row.get("rarity")]
        card_profiles.append(
            CardSearchProfile(
                card_id=card_id,
                game_id=game.id,
                normalized_name=normalize_search_text(card_row["name"]),
                aliases_json=[compact_search_text(value) for value in collectors + sets if compact_search_text(value)],
                keywords_json=[],
                attributes_json=attrs,
                search_text=build_search_text(card_row["name"], collectors, sets, rarities, attrs),
            )
        )

    for row in print_rows:
        card = card_by_id[int(row["card_id"])]
        card_attrs = _card_attrs(card["attributes_json"] or {})
        attrs = _print_attrs(row["print_attributes"] or {}, card_attrs=card_attrs, rarity=row["rarity"])
        for key, value in attrs.items():
            if value not in (None, "", [], {}):
                coverage[f"print.{key}"] += 1

        language = str(row["language"] or "").strip().lower() or None
        exact_variant = str(row["variant"] or "default").strip().lower() or "default"
        finish = str(attrs.get("finish") or "").strip().lower()
        family = finish or ("default" if exact_variant == "default" else "physical")
        collector = str(row["collector_number"] or "").strip()
        set_code = str(row["set_code"] or "").strip()
        aliases = [value for value in (compact_search_text(collector), compact_search_text(set_code), compact_search_text(card["tcgdex_id"])) if value]
        search_text = build_search_text(
            card["name"],
            aliases,
            set_code,
            row["set_name"],
            collector,
            row["rarity"],
            exact_variant,
            family,
            language,
            LANGUAGE_LABELS.get(language or "", ""),
            attrs,
        )
        print_profiles.append(
            PrintSearchProfile(
                print_id=int(row["id"]),
                card_id=int(row["card_id"]),
                game_id=game.id,
                normalized_name=normalize_search_text(card["name"]),
                normalized_set_code=normalize_search_text(set_code).replace(" ", "-") or None,
                normalized_collector_number=normalize_search_text(collector).replace(" ", "-") or None,
                language=language,
                rarity=row["rarity"],
                exact_variant=exact_variant,
                variant_family=family,
                release_names_json=[value for value in (attrs.get("series"),) if value],
                aliases_json=aliases,
                keywords_json=[],
                attributes_json=attrs,
                search_text=search_text,
            )
        )

    session.add_all(card_profiles)
    session.add_all(print_profiles)

    facet_rows = []
    for definition in facets_for_game("pokemon"):
        row = dict(definition)
        row.setdefault("multi_value", False)
        row.setdefault("filterable", True)
        row.setdefault("sortable", False)
        row.setdefault("searchable", False)
        row.setdefault("quick_filter", False)
        row.setdefault("active", True)
        row.setdefault("display_order", 0)
        facet_rows.append(FacetDefinition(game_id=game.id, **row))
    session.add_all(facet_rows)
    session.flush()

    return {
        "game": "pokemon",
        "cards": len(card_profiles),
        "prints": len(print_profiles),
        "facets": len(facet_rows),
        "coverage": dict(sorted(coverage.items())),
    }
