from __future__ import annotations


MTG_FACETS = [
    # Identity
    {"scope":"print","key":"set","label":"Set","value_type":"string","ui_type":"autocomplete","group_name":"Identity","source_path":"set.code","searchable":True,"quick_filter":True,"display_order":10},
    {"scope":"print","key":"collector_number","label":"Collector Number","value_type":"string","ui_type":"autocomplete","group_name":"Identity","source_path":"print.collector_number","searchable":True,"display_order":20},
    {"scope":"print","key":"language","label":"Language","value_type":"enum","ui_type":"multi_select","group_name":"Identity","source_path":"print.language","multi_value":True,"display_order":30},
    {"scope":"print","key":"release_year","label":"Release Year","value_type":"integer","ui_type":"range","group_name":"Identity","source_path":"attributes.release_year","sortable":True,"display_order":40},
    {"scope":"print","key":"set_type","label":"Set Type","value_type":"enum","ui_type":"multi_select","group_name":"Identity","source_path":"attributes.set_type","multi_value":True,"searchable":True,"display_order":50},

    # Card / gameplay
    {"scope":"card","key":"color_identity","label":"Color Identity","value_type":"enum","ui_type":"multi_select","group_name":"Card","source_path":"attributes.color_identity","multi_value":True,"searchable":True,"quick_filter":True,"display_order":100,"options_json":["W","U","B","R","G"]},
    {"scope":"card","key":"card_type","label":"Card Type","value_type":"enum","ui_type":"multi_select","group_name":"Card","source_path":"attributes.card_types","multi_value":True,"searchable":True,"quick_filter":True,"display_order":110},
    {"scope":"card","key":"layout","label":"Layout","value_type":"enum","ui_type":"multi_select","group_name":"Card","source_path":"attributes.layout","multi_value":True,"searchable":True,"display_order":120},
    {"scope":"card","key":"mana_value","label":"Mana Value","value_type":"number","ui_type":"range","group_name":"Card","source_path":"attributes.mana_value","sortable":True,"display_order":130},
    {"scope":"card","key":"keyword","label":"Keyword","value_type":"string","ui_type":"autocomplete","group_name":"Card","source_path":"attributes.keywords","multi_value":True,"searchable":True,"display_order":140},

    # Printing / collecting
    {"scope":"print","key":"rarity","label":"Rarity","value_type":"enum","ui_type":"multi_select","group_name":"Collecting","source_path":"print.rarity","multi_value":True,"searchable":True,"quick_filter":True,"display_order":200},
    {"scope":"print","key":"finish","label":"Finish","value_type":"enum","ui_type":"chips","group_name":"Collecting","source_path":"print.variant","multi_value":True,"quick_filter":True,"display_order":210,"options_json":["nonfoil","foil","etched"]},
    {"scope":"print","key":"artist","label":"Artist","value_type":"string","ui_type":"autocomplete","group_name":"Collecting","source_path":"attributes.artist","searchable":True,"display_order":220},
    {"scope":"print","key":"frame","label":"Frame","value_type":"enum","ui_type":"multi_select","group_name":"Collecting","source_path":"attributes.frame","multi_value":True,"searchable":True,"display_order":230},
    {"scope":"print","key":"frame_effect","label":"Frame Effect","value_type":"enum","ui_type":"multi_select","group_name":"Collecting","source_path":"attributes.frame_effects","multi_value":True,"searchable":True,"display_order":240},
    {"scope":"print","key":"border_color","label":"Border Color","value_type":"enum","ui_type":"multi_select","group_name":"Collecting","source_path":"attributes.border_color","multi_value":True,"display_order":250},
    {"scope":"print","key":"promo_type","label":"Promo Type","value_type":"enum","ui_type":"multi_select","group_name":"Collecting","source_path":"attributes.promo_types","multi_value":True,"searchable":True,"display_order":260},
    {"scope":"print","key":"promo","label":"Promo","value_type":"boolean","ui_type":"toggle","group_name":"Collecting","source_path":"attributes.promo","display_order":270},
    {"scope":"print","key":"full_art","label":"Full Art","value_type":"boolean","ui_type":"toggle","group_name":"Collecting","source_path":"attributes.full_art","display_order":280},
    {"scope":"print","key":"textless","label":"Textless","value_type":"boolean","ui_type":"toggle","group_name":"Collecting","source_path":"attributes.textless","display_order":290},
    {"scope":"print","key":"reserved","label":"Reserved","value_type":"boolean","ui_type":"toggle","group_name":"Collecting","source_path":"attributes.reserved","display_order":300},
]


def mtg_facets() -> list[dict]:
    return [dict(row) for row in MTG_FACETS]
