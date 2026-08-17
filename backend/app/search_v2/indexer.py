from __future__ import annotations

from collections import defaultdict

from sqlalchemy import delete, select

from app.catalog_release_models import CatalogRelease, PrintRelease
from app.models import Card, Game, Print, Set
from app.search_v2.facets import facets_for_game
from app.search_v2.normalization import (
    build_search_text,
    compact_search_text,
    normalize_search_text,
    variant_family,
)
from app.search_v2_models import CardSearchProfile, FacetDefinition, PrintSearchProfile


_LANGUAGE_LABELS = {
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


def _normalized_source_attrs(raw: dict | None) -> dict:
    raw = raw or {}
    return {
        "card_type": raw.get("card_type"),
        "color": list(raw.get("colors") or []),
        "cost": raw.get("cost"),
        "life": raw.get("life"),
        "power": raw.get("power"),
        "counter": raw.get("counter"),
        "attribute": list(raw.get("attributes") or []),
        "traits": list(raw.get("traits") or []),
        "block": raw.get("block"),
        "effect": raw.get("effect"),
        "trigger": raw.get("trigger"),
    }


def _card_level_attrs(raw: dict | None) -> dict:
    attrs = _normalized_source_attrs(raw)
    # Block is a print/reprint-era property and can legitimately differ between
    # physical versions of the same logical collector-number definition.
    attrs.pop("block", None)
    return attrs


def _print_level_attrs(raw: dict | None, *, collector_number: str, rarity: str | None, family: str) -> dict:
    attrs = _normalized_source_attrs(raw)
    normalized_collector = str(collector_number or "").upper()
    normalized_rarity = str(rarity or "").upper()
    attrs.update(
        {
            "is_promo": normalized_collector.startswith("P-"),
            "is_sp": normalized_rarity == "SP CARD",
            "is_treasure_rare": normalized_rarity == "TR",
            "is_reprint": family == "reprint",
            # Manga / illustration type require explicit source evidence. They are
            # intentionally omitted here rather than guessed from price or suffix.
        }
    )
    return attrs


def rebuild_onepiece_search_v2(session, *, source_attributes: dict[str, dict]) -> dict:
    game = session.execute(select(Game).where(Game.slug == "onepiece")).scalar_one()

    session.execute(delete(CardSearchProfile).where(CardSearchProfile.game_id == game.id))
    session.execute(delete(PrintSearchProfile).where(PrintSearchProfile.game_id == game.id))
    session.execute(delete(FacetDefinition).where(FacetDefinition.game_id == game.id))

    sets = session.execute(select(Set).where(Set.game_id == game.id)).scalars().all()
    set_by_id = {row.id: row for row in sets}
    cards = session.execute(select(Card).where(Card.game_id == game.id).order_by(Card.id.asc())).scalars().all()
    card_by_id = {row.id: row for row in cards}
    prints = session.execute(
        select(Print).join(Set, Set.id == Print.set_id).where(Set.game_id == game.id).order_by(Print.id.asc())
    ).scalars().all()

    prints_by_card: dict[int, list[Print]] = defaultdict(list)
    for print_row in prints:
        prints_by_card[print_row.card_id].append(print_row)

    release_rows = session.execute(
        select(PrintRelease.print_id, PrintRelease.source_print_id, CatalogRelease.name)
        .join(CatalogRelease, CatalogRelease.id == PrintRelease.release_id)
        .where(CatalogRelease.game_id == game.id)
        .order_by(PrintRelease.id.asc())
    ).all()
    release_names_by_print: dict[int, list[str]] = defaultdict(list)
    source_ids_by_print: dict[int, list[str]] = defaultdict(list)
    for print_id, source_print_id, release_name in release_rows:
        if release_name and release_name not in release_names_by_print[print_id]:
            release_names_by_print[print_id].append(release_name)
        normalized_source_id = str(source_print_id or "").strip().upper()
        if normalized_source_id and normalized_source_id not in source_ids_by_print[print_id]:
            source_ids_by_print[print_id].append(normalized_source_id)

    def source_attrs_for(print_row: Print) -> dict:
        for external_id in source_ids_by_print.get(print_row.id, []):
            if external_id in source_attributes:
                return source_attributes[external_id]
        # Exact print id is preferred, but the base card remains a safe gameplay
        # fallback for a reprint/parallel when source metadata is identical.
        collector = str(print_row.collector_number or "").strip().upper()
        return source_attributes.get(collector, {})

    card_profiles = []
    print_profiles = []
    coverage = defaultdict(int)

    for card in cards:
        card_prints = prints_by_card.get(card.id, [])
        preferred = sorted(
            card_prints,
            key=lambda row: (0 if str(row.variant or "default").lower() == "default" else 1, row.id),
        )
        preferred_attrs = source_attrs_for(preferred[0]) if preferred else {}
        card_attrs = _card_level_attrs(preferred_attrs)
        for key, value in card_attrs.items():
            if value not in (None, "", [], {}):
                coverage[f"card.{key}"] += 1

        collector_numbers = [row.collector_number for row in card_prints if row.collector_number]
        set_codes = [set_by_id[row.set_id].code for row in card_prints if row.set_id in set_by_id]
        card_profiles.append(
            CardSearchProfile(
                card_id=card.id,
                game_id=game.id,
                normalized_name=normalize_search_text(card.name),
                aliases_json=[],
                keywords_json=[],
                attributes_json=card_attrs,
                search_text=build_search_text(card.name, collector_numbers, set_codes, card_attrs),
            )
        )

    for print_row in prints:
        card = card_by_id[print_row.card_id]
        set_row = set_by_id[print_row.set_id]
        exact_variant = str(print_row.variant or "default").strip().lower() or "default"
        family = variant_family(exact_variant)
        release_names = release_names_by_print.get(print_row.id, [])
        raw_attrs = source_attrs_for(print_row)
        print_attrs = _print_level_attrs(
            raw_attrs,
            collector_number=print_row.collector_number,
            rarity=print_row.rarity,
            family=family,
        )
        for key, value in print_attrs.items():
            if value not in (None, "", [], {}):
                coverage[f"print.{key}"] += 1

        language = str(print_row.language or "").strip().lower() or None
        aliases = [
            compact_search_text(print_row.collector_number),
            compact_search_text(set_row.code),
        ]
        aliases = [alias for alias in aliases if alias]
        search_text = build_search_text(
            card.name,
            aliases,
            set_row.code,
            set_row.name,
            print_row.collector_number,
            print_row.rarity,
            exact_variant,
            family,
            language,
            _LANGUAGE_LABELS.get(language or "", ""),
            release_names,
            print_attrs,
        )
        print_profiles.append(
            PrintSearchProfile(
                print_id=print_row.id,
                card_id=card.id,
                game_id=game.id,
                normalized_name=normalize_search_text(card.name),
                normalized_set_code=normalize_search_text(set_row.code).replace(" ", "-") or None,
                normalized_collector_number=normalize_search_text(print_row.collector_number).replace(" ", "-") or None,
                language=language,
                rarity=print_row.rarity,
                exact_variant=exact_variant,
                variant_family=family,
                release_names_json=release_names,
                aliases_json=aliases,
                keywords_json=[],
                attributes_json=print_attrs,
                search_text=search_text,
            )
        )

    session.add_all(card_profiles)
    session.add_all(print_profiles)

    facet_rows = []
    for definition in facets_for_game("onepiece"):
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
        "game": "onepiece",
        "cards": len(card_profiles),
        "prints": len(print_profiles),
        "facets": len(facet_rows),
        "source_attribute_rows": len(source_attributes),
        "coverage": dict(sorted(coverage.items())),
    }
