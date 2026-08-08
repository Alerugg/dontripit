from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import text

from app import db
from app.search_v2.pokemon_advanced import advanced_pokemon_search
from app.search_v2.pokemon_facet_values import pokemon_facet_values
from app.search_v2.query import facet_definitions, normal_search


EXPECTED_CARDS = 21065
EXPECTED_PRINTS = 33757
EXPECTED_FACETS = 23
EXPECTED_VARIANT_PRINTS = 27241


def _write_json(path: Path | None, payload: dict) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def _timed(fn, *args, **kwargs):
    start = time.perf_counter()
    result = fn(*args, **kwargs)
    elapsed_ms = round((time.perf_counter() - start) * 1000, 2)
    return result, elapsed_ms


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _lower_list(value) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip().lower() for item in value]


def run(*, report_path: Path | None = None) -> dict:
    db.init_engine()
    checks: list[dict] = []

    with db.SessionLocal() as session:
        game_id = int(session.execute(text("SELECT id FROM games WHERE slug='pokemon' LIMIT 1")).scalar_one())
        counts = dict(session.execute(text(
            """
            SELECT
              (SELECT COUNT(*) FROM card_search_profiles WHERE game_id=:game) AS cards,
              (SELECT COUNT(*) FROM print_search_profiles WHERE game_id=:game) AS prints,
              (SELECT COUNT(*) FROM facet_definitions WHERE game_id=:game AND active=TRUE) AS facets,
              (SELECT COUNT(*) FROM print_search_profiles WHERE game_id=:game AND attributes_json->>'finish' IS NOT NULL) AS variant_prints
            """
        ), {"game": game_id}).mappings().one())
        _assert(int(counts["cards"]) == EXPECTED_CARDS, f"card profiles={counts['cards']}")
        _assert(int(counts["prints"]) == EXPECTED_PRINTS, f"print profiles={counts['prints']}")
        _assert(int(counts["facets"]) == EXPECTED_FACETS, f"facets={counts['facets']}")
        _assert(int(counts["variant_prints"]) == EXPECTED_VARIANT_PRINTS, f"variant profiles={counts['variant_prints']}")
        checks.append({"name": "certified_counts", "status": "pass", **{k: int(v) for k, v in counts.items()}})

        for query, expected_name in (("Pikachu", "pikachu"), ("Charizard", "charizard")):
            result, elapsed = _timed(normal_search, session, query=query, game_slug="pokemon", limit=12)
            names = [str(row.get("name") or "").lower() for row in result]
            _assert(result, f"Natural search {query!r} returned no results")
            _assert(any(expected_name in name for name in names), f"Natural search {query!r} did not return {expected_name}")
            checks.append({"name": f"natural_{expected_name}", "status": "pass", "result_count": len(result), "ms": elapsed, "top_names": names[:5]})

        result, elapsed = _timed(advanced_pokemon_search, session, filters={"types": ["Fire"]}, limit=25)
        _assert(result["total"] > 0, "Fire type filter returned zero")
        _assert(all("fire" in _lower_list(row["attributes"].get("types")) for row in result["items"]), "Fire filter returned a non-Fire item")
        checks.append({"name": "advanced_type_fire", "status": "pass", "total": result["total"], "ms": elapsed})

        result, elapsed = _timed(advanced_pokemon_search, session, filters={"category": ["Pokemon"], "stage": ["Basic"]}, limit=25)
        _assert(result["total"] > 0, "Pokemon + Basic filter returned zero")
        _assert(all(str(row["attributes"].get("category") or "").lower() == "pokemon" for row in result["items"]), "Category filter mismatch")
        _assert(all(str(row["attributes"].get("stage") or "").lower() == "basic" for row in result["items"]), "Stage filter mismatch")
        checks.append({"name": "advanced_basic_pokemon", "status": "pass", "total": result["total"], "ms": elapsed})

        result, elapsed = _timed(advanced_pokemon_search, session, filters={"hp": {"min": 300}}, limit=25)
        _assert(result["total"] > 0, "HP >= 300 filter returned zero")
        _assert(all(int(row["attributes"].get("hp")) >= 300 for row in result["items"] if str(row["attributes"].get("hp") or "").isdigit()), "HP range mismatch")
        checks.append({"name": "advanced_hp_300_plus", "status": "pass", "total": result["total"], "ms": elapsed})

        result, elapsed = _timed(advanced_pokemon_search, session, filters={"rarity": ["Special illustration rare"]}, limit=25)
        _assert(result["total"] > 0, "Special illustration rare returned zero")
        _assert(all(str(row.get("rarity") or "").lower() == "special illustration rare" for row in result["items"]), "Rarity filter mismatch")
        checks.append({"name": "advanced_special_illustration_rare", "status": "pass", "total": result["total"], "ms": elapsed})

        result, elapsed = _timed(advanced_pokemon_search, session, filters={"finish": ["holo"]}, limit=25)
        _assert(result["total"] > 0, "Holo finish returned zero")
        _assert(all(str(row["attributes"].get("finish") or "").lower() == "holo" for row in result["items"]), "Finish filter mismatch")
        checks.append({"name": "advanced_finish_holo", "status": "pass", "total": result["total"], "ms": elapsed})

        result, elapsed = _timed(advanced_pokemon_search, session, filters={"stamp": ["set-logo"]}, limit=25)
        _assert(result["total"] > 0, "set-logo stamp returned zero")
        _assert(all("set-logo" in _lower_list(row["attributes"].get("stamps")) for row in result["items"]), "Stamp filter mismatch")
        checks.append({"name": "advanced_stamp_set_logo", "status": "pass", "total": result["total"], "ms": elapsed})

        result, elapsed = _timed(advanced_pokemon_search, session, filters={"dex_id": {"min": 25, "max": 25}}, query="Pikachu", limit=25)
        _assert(result["total"] > 0, "Pokédex #25 + Pikachu returned zero")
        _assert(any("pikachu" in str(row.get("name") or "").lower() for row in result["items"]), "Pokédex #25 did not return Pikachu")
        checks.append({"name": "advanced_pokedex_25_pikachu", "status": "pass", "total": result["total"], "ms": elapsed})

        definitions, elapsed = _timed(facet_definitions, session, game_slug="pokemon")
        keys = {row.get("key") for row in definitions}
        required_keys = {"set", "types", "stage", "rarity", "regulation_mark", "finish", "stamp", "dex_id"}
        _assert(required_keys.issubset(keys), f"Missing facet definitions: {sorted(required_keys - keys)}")
        checks.append({"name": "facet_definitions", "status": "pass", "count": len(definitions), "ms": elapsed})

        for key, expected in (("types", "fire"), ("rarity", "special illustration rare"), ("finish", "holo"), ("stamp", "set-logo")):
            values, elapsed = _timed(pokemon_facet_values, session, key=key, limit=100)
            normalized = {str(row.get("value") or "").lower() for row in values}
            _assert(expected in normalized, f"Facet {key!r} does not expose expected value {expected!r}")
            checks.append({"name": f"facet_values_{key}", "status": "pass", "count": len(values), "ms": elapsed, "expected": expected})

        paged_a = advanced_pokemon_search(session, filters={"types": ["Fire"]}, limit=5, offset=0)
        paged_b = advanced_pokemon_search(session, filters={"types": ["Fire"]}, limit=5, offset=5)
        ids_a = [row["print_id"] for row in paged_a["items"]]
        ids_b = [row["print_id"] for row in paged_b["items"]]
        _assert(ids_a and ids_b and set(ids_a).isdisjoint(ids_b), "Pagination returned overlapping first two pages")
        checks.append({"name": "pagination", "status": "pass", "page1": ids_a, "page2": ids_b})

        rejected = False
        try:
            advanced_pokemon_search(session, filters={"invented_filter": ["x"]}, limit=5)
        except ValueError:
            rejected = True
        _assert(rejected, "Unsupported advanced filter was silently accepted")
        checks.append({"name": "unsupported_filter_rejected", "status": "pass"})

    timings = [float(row["ms"]) for row in checks if "ms" in row]
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "game": "pokemon",
        "status": "pass",
        "checks": checks,
        "performance": {
            "timed_checks": len(timings),
            "max_ms": max(timings) if timings else None,
            "mean_ms": round(sum(timings) / len(timings), 2) if timings else None,
        },
    }
    _write_json(report_path, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report-path", type=Path)
    args = parser.parse_args()
    run(report_path=args.report_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
