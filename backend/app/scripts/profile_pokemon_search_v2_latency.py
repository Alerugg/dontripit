from __future__ import annotations

import json
import time

from sqlalchemy import text

from app import db
from app.search_v2.pokemon_advanced import advanced_pokemon_search
from app.search_v2.pokemon_facet_values import pokemon_facet_values
from app.search_v2.pokemon_query import normal_pokemon_search
from app.search_v2.query import facet_definitions


TIMEOUT_MS = 15000


def _run_case(name, fn):
    started = time.perf_counter()
    status = "pass"
    detail = None
    with db.SessionLocal() as session:
        try:
            session.execute(text(f"SET statement_timeout = '{TIMEOUT_MS}ms'"))
            value = fn(session)
            if isinstance(value, dict):
                detail = {"total": value.get("total"), "items": len(value.get("items") or [])}
            elif isinstance(value, list):
                detail = {"items": len(value)}
            else:
                detail = {"type": type(value).__name__}
            session.rollback()
        except Exception as exc:
            status = "timeout_or_error"
            detail = {"error": f"{type(exc).__name__}: {exc}"[:1000]}
            session.rollback()
    elapsed = round((time.perf_counter() - started) * 1000, 2)
    row = {"name": name, "status": status, "ms": elapsed, **(detail or {})}
    print(json.dumps(row, ensure_ascii=False), flush=True)
    return row


def main() -> int:
    db.init_engine()
    cases = [
        ("natural_pikachu", lambda s: normal_pokemon_search(s, query="Pikachu", limit=12)),
        ("natural_charizard", lambda s: normal_pokemon_search(s, query="Charizard", limit=12)),
        ("advanced_fire", lambda s: advanced_pokemon_search(s, filters={"types": ["Fire"]}, limit=25)),
        ("advanced_basic", lambda s: advanced_pokemon_search(s, filters={"category": ["Pokemon"], "stage": ["Basic"]}, limit=25)),
        ("advanced_hp_300", lambda s: advanced_pokemon_search(s, filters={"hp": {"min": 300}}, limit=25)),
        ("advanced_sir", lambda s: advanced_pokemon_search(s, filters={"rarity": ["Special illustration rare"]}, limit=25)),
        ("advanced_holo", lambda s: advanced_pokemon_search(s, filters={"finish": ["holo"]}, limit=25)),
        ("advanced_set_logo", lambda s: advanced_pokemon_search(s, filters={"stamp": ["set-logo"]}, limit=25)),
        ("advanced_dex25", lambda s: advanced_pokemon_search(s, filters={"dex_id": {"min": 25, "max": 25}}, query="Pikachu", limit=25)),
        ("facet_definitions", lambda s: facet_definitions(s, game_slug="pokemon")),
        ("facet_types", lambda s: pokemon_facet_values(s, key="types", limit=100)),
        ("facet_rarity", lambda s: pokemon_facet_values(s, key="rarity", limit=100)),
        ("facet_finish", lambda s: pokemon_facet_values(s, key="finish", limit=100)),
        ("facet_stamp", lambda s: pokemon_facet_values(s, key="stamp", limit=100)),
    ]
    results = [_run_case(name, fn) for name, fn in cases]
    print(json.dumps({"summary": results}, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
