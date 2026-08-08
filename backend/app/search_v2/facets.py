from __future__ import annotations


# Facets are declarative on purpose: the frontend will render them dynamically.
# Adding another TCG should mean adding definitions/data, not rebuilding filter UI logic.
# Facets with active=False are deliberately staged but hidden until source-backed
# classification exists; the UI must never offer a filter that silently guesses.
ONEPIECE_FACETS = [
    # Identity
    {"scope": "print", "key": "set", "label": "Set", "value_type": "string", "ui_type": "autocomplete", "group_name": "Identity", "source_path": "set.code", "searchable": True, "quick_filter": True, "display_order": 10},
    {"scope": "print", "key": "collector_number", "label": "Collector Number", "value_type": "string", "ui_type": "autocomplete", "group_name": "Identity", "source_path": "print.collector_number", "searchable": True, "display_order": 20},
    {"scope": "release", "key": "release", "label": "Release / Product", "value_type": "string", "ui_type": "autocomplete", "group_name": "Identity", "source_path": "release.name", "multi_value": True, "searchable": True, "display_order": 30},
    {"scope": "print", "key": "language", "label": "Language", "value_type": "enum", "ui_type": "multi_select", "group_name": "Identity", "source_path": "print.language", "multi_value": True, "quick_filter": True, "display_order": 40},

    # Gameplay / card definition
    {"scope": "card", "key": "color", "label": "Color", "value_type": "enum", "ui_type": "chips", "group_name": "Card", "source_path": "attributes.color", "multi_value": True, "quick_filter": True, "display_order": 100, "options_json": ["Red", "Green", "Blue", "Purple", "Black", "Yellow", "Multicolor"]},
    {"scope": "card", "key": "card_type", "label": "Card Type", "value_type": "enum", "ui_type": "chips", "group_name": "Card", "source_path": "attributes.card_type", "quick_filter": True, "display_order": 110, "options_json": ["Leader", "Character", "Stage", "Event"]},
    {"scope": "card", "key": "cost", "label": "Cost", "value_type": "integer", "ui_type": "range", "group_name": "Card", "source_path": "attributes.cost", "sortable": True, "display_order": 120},
    {"scope": "card", "key": "life", "label": "Life", "value_type": "integer", "ui_type": "range", "group_name": "Card", "source_path": "attributes.life", "sortable": True, "display_order": 130},
    {"scope": "card", "key": "power", "label": "Power", "value_type": "integer", "ui_type": "range", "group_name": "Card", "source_path": "attributes.power", "sortable": True, "display_order": 140},
    {"scope": "card", "key": "counter", "label": "Counter", "value_type": "integer", "ui_type": "range", "group_name": "Card", "source_path": "attributes.counter", "sortable": True, "display_order": 150},
    {"scope": "card", "key": "attribute", "label": "Attribute", "value_type": "enum", "ui_type": "multi_select", "group_name": "Card", "source_path": "attributes.attribute", "multi_value": True, "searchable": True, "display_order": 160},
    {"scope": "card", "key": "traits", "label": "Type / Traits", "value_type": "string", "ui_type": "autocomplete", "group_name": "Card", "source_path": "attributes.traits", "multi_value": True, "searchable": True, "display_order": 170},
    {"scope": "print", "key": "block", "label": "Block", "value_type": "enum", "ui_type": "chips", "group_name": "Card", "source_path": "attributes.block", "quick_filter": True, "display_order": 180, "options_json": ["1", "2", "3", "4", "5", "X"]},

    # Collector dimensions
    {"scope": "print", "key": "rarity", "label": "Rarity", "value_type": "enum", "ui_type": "multi_select", "group_name": "Collecting", "source_path": "print.rarity", "multi_value": True, "quick_filter": True, "display_order": 200},
    {"scope": "print", "key": "illustration_type", "label": "Illustration Type", "value_type": "enum", "ui_type": "chips", "group_name": "Collecting", "source_path": "attributes.illustration_type", "multi_value": True, "quick_filter": True, "display_order": 210, "options_json": ["Comic", "Animation", "Original Illustrations", "Other"], "active": False},
    {"scope": "print", "key": "variant_family", "label": "Variant Family", "value_type": "enum", "ui_type": "chips", "group_name": "Collecting", "source_path": "print.variant_family", "quick_filter": True, "display_order": 220, "options_json": ["default", "parallel", "reprint"]},
    {"scope": "print", "key": "exact_variant", "label": "Exact Variant", "value_type": "enum", "ui_type": "multi_select", "group_name": "Collecting", "source_path": "print.exact_variant", "multi_value": True, "display_order": 230},
    {"scope": "print", "key": "promo", "label": "Promo", "value_type": "boolean", "ui_type": "toggle", "group_name": "Collecting", "source_path": "attributes.is_promo", "quick_filter": True, "display_order": 240},
    {"scope": "print", "key": "manga", "label": "Manga", "value_type": "boolean", "ui_type": "toggle", "group_name": "Collecting", "source_path": "attributes.is_manga", "quick_filter": True, "display_order": 250, "active": False},
    {"scope": "print", "key": "sp", "label": "SP", "value_type": "boolean", "ui_type": "toggle", "group_name": "Collecting", "source_path": "attributes.is_sp", "quick_filter": True, "display_order": 260},
    {"scope": "print", "key": "treasure_rare", "label": "Treasure Rare", "value_type": "boolean", "ui_type": "toggle", "group_name": "Collecting", "source_path": "attributes.is_treasure_rare", "quick_filter": True, "display_order": 270},
]


POKEMON_FACETS = [
    # Identity
    {"scope": "print", "key": "set", "label": "Set", "value_type": "string", "ui_type": "autocomplete", "group_name": "Identity", "source_path": "set.code", "searchable": True, "quick_filter": True, "display_order": 10},
    {"scope": "print", "key": "collector_number", "label": "Collector Number", "value_type": "string", "ui_type": "autocomplete", "group_name": "Identity", "source_path": "print.collector_number", "searchable": True, "display_order": 20},
    {"scope": "print", "key": "series", "label": "Series / Era", "value_type": "string", "ui_type": "autocomplete", "group_name": "Identity", "source_path": "attributes.series_name", "searchable": True, "display_order": 30},
    {"scope": "print", "key": "release_year", "label": "Release Year", "value_type": "integer", "ui_type": "range", "group_name": "Identity", "source_path": "attributes.release_year", "sortable": True, "display_order": 40},
    {"scope": "print", "key": "language", "label": "Language", "value_type": "enum", "ui_type": "multi_select", "group_name": "Identity", "source_path": "print.language", "multi_value": True, "display_order": 50},

    # Card definition / gameplay
    {"scope": "card", "key": "category", "label": "Category", "value_type": "enum", "ui_type": "chips", "group_name": "Card", "source_path": "attributes.category", "quick_filter": True, "display_order": 100, "options_json": ["Pokemon", "Trainer", "Energy"]},
    {"scope": "card", "key": "types", "label": "Pokémon Type", "value_type": "enum", "ui_type": "multi_select", "group_name": "Card", "source_path": "attributes.types", "multi_value": True, "quick_filter": True, "searchable": True, "display_order": 110},
    {"scope": "card", "key": "stage", "label": "Stage", "value_type": "enum", "ui_type": "chips", "group_name": "Card", "source_path": "attributes.stage", "quick_filter": True, "display_order": 120, "options_json": ["Basic", "Stage1", "Stage2", "BREAK", "MEGA", "LEVEL-UP", "VSTAR", "VMAX"]},
    {"scope": "card", "key": "hp", "label": "HP", "value_type": "integer", "ui_type": "range", "group_name": "Card", "source_path": "attributes.hp", "sortable": True, "display_order": 130},
    {"scope": "card", "key": "trainer_type", "label": "Trainer Type", "value_type": "enum", "ui_type": "multi_select", "group_name": "Card", "source_path": "attributes.trainer_type", "multi_value": True, "searchable": True, "display_order": 140},
    {"scope": "card", "key": "energy_type", "label": "Energy Type", "value_type": "enum", "ui_type": "multi_select", "group_name": "Card", "source_path": "attributes.energy_type", "multi_value": True, "searchable": True, "display_order": 150},
    {"scope": "card", "key": "evolve_from", "label": "Evolves From", "value_type": "string", "ui_type": "autocomplete", "group_name": "Card", "source_path": "attributes.evolve_from", "searchable": True, "display_order": 160},
    {"scope": "card", "key": "dex_id", "label": "Pokédex #", "value_type": "integer", "ui_type": "range", "group_name": "Card", "source_path": "attributes.dex_id", "display_order": 170},

    # Collecting / physical print dimensions
    {"scope": "print", "key": "rarity", "label": "Rarity", "value_type": "enum", "ui_type": "multi_select", "group_name": "Collecting", "source_path": "print.rarity", "multi_value": True, "quick_filter": True, "searchable": True, "display_order": 200},
    {"scope": "print", "key": "regulation_mark", "label": "Regulation Mark", "value_type": "enum", "ui_type": "chips", "group_name": "Collecting", "source_path": "attributes.regulation_mark", "multi_value": True, "quick_filter": True, "display_order": 210},
    {"scope": "print", "key": "illustrator", "label": "Illustrator", "value_type": "string", "ui_type": "autocomplete", "group_name": "Collecting", "source_path": "attributes.illustrator", "searchable": True, "display_order": 220},
    {"scope": "print", "key": "finish", "label": "Finish", "value_type": "enum", "ui_type": "chips", "group_name": "Collecting", "source_path": "attributes.finish", "multi_value": True, "quick_filter": True, "display_order": 230, "options_json": ["normal", "holo", "reverse", "lenticular", "metal"]},
    {"scope": "print", "key": "foil_pattern", "label": "Foil Pattern", "value_type": "enum", "ui_type": "multi_select", "group_name": "Collecting", "source_path": "attributes.foil_pattern", "multi_value": True, "searchable": True, "display_order": 240},
    {"scope": "print", "key": "stamp", "label": "Stamp", "value_type": "enum", "ui_type": "multi_select", "group_name": "Collecting", "source_path": "attributes.stamps", "multi_value": True, "quick_filter": True, "searchable": True, "display_order": 250},
    {"scope": "print", "key": "variant_subtype", "label": "Variant Subtype", "value_type": "enum", "ui_type": "multi_select", "group_name": "Collecting", "source_path": "attributes.variant_subtype", "multi_value": True, "searchable": True, "display_order": 260},
    {"scope": "print", "key": "release_context", "label": "Release Context", "value_type": "enum", "ui_type": "chips", "group_name": "Collecting", "source_path": "attributes.release_context", "multi_value": True, "display_order": 270},
    {"scope": "print", "key": "size", "label": "Card Size", "value_type": "enum", "ui_type": "chips", "group_name": "Collecting", "source_path": "attributes.size", "multi_value": True, "display_order": 280},
    {"scope": "print", "key": "exact_variant", "label": "Exact Variant", "value_type": "enum", "ui_type": "multi_select", "group_name": "Collecting", "source_path": "print.exact_variant", "multi_value": True, "display_order": 290},
]


def facets_for_game(game_slug: str) -> list[dict]:
    slug = str(game_slug or "").strip().lower()
    if slug == "onepiece":
        return [dict(row) for row in ONEPIECE_FACETS]
    if slug == "pokemon":
        return [dict(row) for row in POKEMON_FACETS]
    return []
