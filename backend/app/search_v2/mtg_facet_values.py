from __future__ import annotations

from sqlalchemy import text

from app.search_v2.normalization import compact_search_text, normalize_search_text

MAX_FACET_VALUES = 100


def _limit(value) -> int:
    try:
        parsed = int(value or 30)
    except (TypeError, ValueError):
        parsed = 30
    return max(1, min(parsed, MAX_FACET_VALUES))


def _public(rows) -> list[dict]:
    return [{"value": row["value"], "label": row["label"], "count": int(row["count"] or 0)} for row in rows]


def mtg_facet_values(session, *, key: str, query: str | None = None, limit: int = 30) -> list[dict]:
    if session.bind.dialect.name != "postgresql":
        raise RuntimeError("MTG facet values require PostgreSQL")
    key = str(key or "").strip().lower()
    q = normalize_search_text(query or "")
    params = {"game":"mtg","limit":_limit(limit),"q":f"%{q}%","q_compact":f"%{compact_search_text(query or '')}%"}

    if key == "set":
        return _public(session.execute(text("""
            SELECT s.code AS value,s.name AS label,COUNT(*) AS count
            FROM print_search_profiles psp JOIN prints p ON p.id=psp.print_id
            JOIN sets s ON s.id=p.set_id JOIN games g ON g.id=psp.game_id
            WHERE g.slug=:game AND (:q='%%' OR lower(s.code) LIKE :q OR lower(s.name) LIKE :q)
            GROUP BY s.code,s.name ORDER BY count DESC,s.code LIMIT :limit
        """), params).mappings().all())

    if key == "collector_number":
        rows = session.execute(text("""
            SELECT p.collector_number AS value,c.name AS card_name,COUNT(*) AS count
            FROM print_search_profiles psp JOIN prints p ON p.id=psp.print_id
            JOIN cards c ON c.id=psp.card_id JOIN games g ON g.id=psp.game_id
            WHERE g.slug=:game AND (:q='%%' OR lower(p.collector_number) LIKE :q OR lower(c.name) LIKE :q
              OR regexp_replace(lower(p.collector_number),'[^a-z0-9]','','g') LIKE :q_compact)
            GROUP BY p.collector_number,c.name ORDER BY count DESC,p.collector_number,c.name LIMIT :limit
        """), params).mappings().all()
        return [{"value":row["value"],"label":f"{row['value']} · {row['card_name']}","count":int(row["count"] or 0)} for row in rows]

    direct_columns = {"language":"psp.language","rarity":"psp.rarity","finish":"psp.exact_variant"}
    if key in direct_columns:
        column = direct_columns[key]
        return _public(session.execute(text(f"""
            SELECT {column} AS value,{column} AS label,COUNT(*) AS count
            FROM print_search_profiles psp JOIN games g ON g.id=psp.game_id
            WHERE g.slug=:game AND {column} IS NOT NULL AND (:q='%%' OR lower(CAST({column} AS text)) LIKE :q)
            GROUP BY {column} ORDER BY count DESC,value LIMIT :limit
        """), params).mappings().all())

    card_scalars = {"layout":"layout"}
    print_scalars = {"set_type":"set_type","artist":"artist","frame":"frame","border_color":"border_color"}
    if key in card_scalars or key in print_scalars:
        is_card = key in card_scalars
        json_key = (card_scalars if is_card else print_scalars)[key]
        table = "card_search_profiles csp" if is_card else "print_search_profiles psp"
        alias = "csp" if is_card else "psp"
        return _public(session.execute(text(f"""
            SELECT {alias}.attributes_json->>'{json_key}' AS value,{alias}.attributes_json->>'{json_key}' AS label,COUNT(*) AS count
            FROM {table} JOIN games g ON g.id={alias}.game_id
            WHERE g.slug=:game AND COALESCE({alias}.attributes_json->>'{json_key}','')<>''
              AND (:q='%%' OR lower({alias}.attributes_json->>'{json_key}') LIKE :q)
            GROUP BY {alias}.attributes_json->>'{json_key}' ORDER BY count DESC,value LIMIT :limit
        """), params).mappings().all())

    card_arrays = {"color_identity":"color_identity","card_type":"card_types","keyword":"keywords"}
    print_arrays = {"frame_effect":"frame_effects","promo_type":"promo_types"}
    if key in card_arrays or key in print_arrays:
        is_card = key in card_arrays
        json_key = (card_arrays if is_card else print_arrays)[key]
        table = "card_search_profiles csp" if is_card else "print_search_profiles psp"
        alias = "csp" if is_card else "psp"
        return _public(session.execute(text(f"""
            SELECT vals.value AS value,vals.value AS label,COUNT(*) AS count
            FROM {table} JOIN games g ON g.id={alias}.game_id
            CROSS JOIN LATERAL jsonb_array_elements_text(COALESCE({alias}.attributes_json->'{json_key}','[]'::jsonb)) AS vals(value)
            WHERE g.slug=:game AND (:q='%%' OR lower(vals.value) LIKE :q)
            GROUP BY vals.value ORDER BY count DESC,vals.value LIMIT :limit
        """), params).mappings().all())

    if key == "release_year":
        return _public(session.execute(text("""
            SELECT psp.attributes_json->>'release_year' AS value,psp.attributes_json->>'release_year' AS label,COUNT(*) AS count
            FROM print_search_profiles psp JOIN games g ON g.id=psp.game_id
            WHERE g.slug=:game AND COALESCE(psp.attributes_json->>'release_year','')<>''
            GROUP BY psp.attributes_json->>'release_year' ORDER BY value DESC LIMIT :limit
        """), params).mappings().all())

    raise ValueError(f"MTG facet values are not available for: {key}")