from __future__ import annotations


# Yu-Gi-Oh! facets are intentionally limited to dimensions backed by the
# certified canonical source. Finish, edition and exact artwork are absent
# because the current YGOPRODeck surface does not prove them per physical Print.
YUGIOH_FACETS = [
    # Identity / release
    {"scope": "print", "key": "set", "label": "Set Family", "value_type": "string", "ui_type": "autocomplete", "group_name": "Identity", "source_path": "set.code", "searchable": True, "quick_filter": True, "display_order": 10},
    {"scope": "print", "key": "collector_number", "label": "Collector Number", "value_type": "string", "ui_type": "autocomplete", "group_name": "Identity", "source_path": "print.collector_number", "searchable": True, "display_order": 20},
    {"scope": "release", "key": "release", "label": "Release / Product", "value_type": "string", "ui_type": "autocomplete", "group_name": "Identity", "source_path": "release.name", "searchable": True, "quick_filter": True, "display_order": 30},
    {"scope": "release", "key": "release_year", "label": "Release Year", "value_type": "integer", "ui_type": "range", "group_name": "Identity", "source_path": "release.release_date.year", "sortable": True, "display_order": 40},
    {"scope": "print", "key": "language", "label": "Language", "value_type": "enum", "ui_type": "multi_select", "group_name": "Identity", "source_path": "print.language", "multi_value": True, "display_order": 50},

    # Card identity / gameplay
    {"scope": "card", "key": "card_class", "label": "Card Class", "value_type": "enum", "ui_type": "chips", "group_name": "Card", "source_path": "attributes.card_class", "quick_filter": True, "display_order": 100, "options_json": ["Monster", "Spell", "Trap", "Skill", "Token"]},
    {"scope": "card", "key": "card_type", "label": "Card Type", "value_type": "enum", "ui_type": "multi_select", "group_name": "Card", "source_path": "attributes.card_type", "multi_value": True, "searchable": True, "display_order": 110},
    {"scope": "card", "key": "frame_type", "label": "Frame Type", "value_type": "enum", "ui_type": "multi_select", "group_name": "Card", "source_path": "attributes.frame_type", "multi_value": True, "searchable": True, "display_order": 120},
    {"scope": "card", "key": "attribute", "label": "Attribute", "value_type": "enum", "ui_type": "chips", "group_name": "Card", "source_path": "attributes.attribute", "quick_filter": True, "display_order": 130, "options_json": ["DARK", "EARTH", "LIGHT", "WATER", "WIND", "FIRE", "DIVINE"]},
    {"scope": "card", "key": "race", "label": "Monster Type / Spell-Trap Subtype", "value_type": "string", "ui_type": "autocomplete", "group_name": "Card", "source_path": "attributes.race", "searchable": True, "display_order": 140},
    {"scope": "card", "key": "archetype", "label": "Archetype", "value_type": "string", "ui_type": "autocomplete", "group_name": "Card", "source_path": "attributes.archetype", "searchable": True, "quick_filter": True, "display_order": 150},
    {"scope": "card", "key": "level", "label": "Level", "value_type": "integer", "ui_type": "range", "group_name": "Stats", "source_path": "attributes.level", "sortable": True, "display_order": 200},
    {"scope": "card", "key": "rank", "label": "Rank", "value_type": "integer", "ui_type": "range", "group_name": "Stats", "source_path": "attributes.rank", "sortable": True, "display_order": 210},
    {"scope": "card", "key": "atk", "label": "ATK", "value_type": "integer", "ui_type": "range", "group_name": "Stats", "source_path": "attributes.atk", "sortable": True, "display_order": 220},
    {"scope": "card", "key": "def", "label": "DEF", "value_type": "integer", "ui_type": "range", "group_name": "Stats", "source_path": "attributes.def", "sortable": True, "display_order": 230},
    {"scope": "card", "key": "pendulum_scale", "label": "Pendulum Scale", "value_type": "integer", "ui_type": "range", "group_name": "Stats", "source_path": "attributes.pendulum_scale", "sortable": True, "display_order": 240},
    {"scope": "card", "key": "link_value", "label": "Link Rating", "value_type": "integer", "ui_type": "range", "group_name": "Stats", "source_path": "attributes.link_value", "sortable": True, "display_order": 250},
    {"scope": "card", "key": "link_marker", "label": "Link Marker", "value_type": "enum", "ui_type": "multi_select", "group_name": "Stats", "source_path": "attributes.link_markers", "multi_value": True, "searchable": True, "display_order": 260},
    {"scope": "card", "key": "banlist", "label": "Banlist Status", "value_type": "enum", "ui_type": "multi_select", "group_name": "Rules", "source_path": "attributes.banlist_tcg", "multi_value": True, "searchable": True, "display_order": 300, "active": False},

    # Collecting
    {"scope": "print", "key": "rarity", "label": "Rarity", "value_type": "enum", "ui_type": "multi_select", "group_name": "Collecting", "source_path": "print.rarity", "multi_value": True, "searchable": True, "quick_filter": True, "display_order": 400},
]


def yugioh_facets() -> list[dict]:
    return [dict(row) for row in YUGIOH_FACETS]
