from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone

import requests

from app import db
from app.ingest.registry import get_connector
from app.scripts.reindex_search import rebuild_search_documents

DEFAULT_POKEMON_SETS = ["base1", "sv1"]
TCGDEX_SETS_ENDPOINT = "https://api.tcgdex.net/v2/en/sets"


def _to_bool(value: str | bool) -> bool:
    if isinstance(value, bool):
        return value
    return value.lower() in {"1", "true", "yes", "on"}


def _empty_stats() -> dict[str, int]:
    return {"files_seen": 0, "files_skipped": 0, "inserted": 0, "updated": 0, "errors": 0}


def _accumulate(target: dict[str, int], source: dict[str, int]) -> dict[str, int]:
    for key in target:
        target[key] += int(source.get(key, 0))
    return target


def _parse_set_list(raw_sets: str | None) -> list[str]:
    if not raw_sets:
        return []
    return [item.strip().lower() for item in raw_sets.split(",") if item.strip()]


def _fetch_all_pokemon_sets() -> list[str]:
    response = requests.get(TCGDEX_SETS_ENDPOINT, timeout=30)
    response.raise_for_status()
    payload = response.json()
    return [str(item.get("id", "")).lower() for item in payload if item.get("id")]


def _resolve_pokemon_sets(args: argparse.Namespace, summary: dict) -> list[str | None]:
    if args.pokemon_set:
        return [args.pokemon_set.lower()]

    if args.pokemon_sets:
        return _parse_set_list(args.pokemon_sets)

    if args.pokemon_all_sets:
        try:
            sets = _fetch_all_pokemon_sets()
            summary["pokemon"]["set_source"] = "tcgdex"
            return sets
        except Exception as exc:  # noqa: BLE001
            summary["pokemon"]["set_source"] = "default_fallback"
            summary["pokemon"]["set_fetch_error"] = str(exc)
            return DEFAULT_POKEMON_SETS

    if args.pokemon_all:
        return DEFAULT_POKEMON_SETS

    return [None]


def _run_connector(connector_name: str, path: str | None = None, **kwargs) -> dict:
    result = {
        "connector": connector_name,
        "ok": False,
        "error": None,
        "stats": _empty_stats(),
    }

    connector = get_connector(connector_name)
    with db.SessionLocal() as session:
        try:
            stats = connector.run(session, path, **kwargs)
            session.commit()
            result["ok"] = True
            result["stats"] = {
                "files_seen": stats.files_seen,
                "files_skipped": stats.files_skipped,
                "inserted": stats.records_inserted,
                "updated": stats.records_updated,
                "errors": stats.errors,
            }
        except Exception as exc:  # noqa: BLE001
            session.rollback()
            result["error"] = str(exc)
    return result


def run_daily_refresh(args: argparse.Namespace) -> dict:
    started_at = datetime.now(timezone.utc)
    summary: dict = {
        "started_at": started_at.isoformat(),
        "incremental": bool(args.incremental),
        "batch_size": args.batch_size,
        "pokemon": {"ok": False, "skipped": bool(args.skip_pokemon), "runs": [], "totals": _empty_stats()},
        "mtg": {"ok": False, "run": None, "totals": _empty_stats()},
        "yugioh": {"ok": False, "run": None, "totals": _empty_stats()},
        "riftbound": {"ok": False, "run": None, "totals": _empty_stats()},
        "reindex": {"ok": False, "stats": {}, "error": None},
    }

    if not args.skip_pokemon:
        pokemon_sets = _resolve_pokemon_sets(args, summary)

        for pokemon_set in pokemon_sets:
            pokemon_run = _run_connector(
                "tcgdex_pokemon",
                args.path,
                set=pokemon_set,
                limit=args.pokemon_limit or args.batch_size,
                incremental=args.incremental,
                fixture=args.fixture,
            )
            pokemon_run["set"] = pokemon_set
            summary["pokemon"]["runs"].append(pokemon_run)
            _accumulate(summary["pokemon"]["totals"], pokemon_run["stats"])
            if pokemon_run["ok"]:
                summary["pokemon"]["ok"] = True
            time.sleep(max(args.sleep_seconds, 0))

    mtg_run = _run_connector(
        "scryfall_mtg",
        args.path,
        limit=args.mtg_limit or args.batch_size,
        incremental=args.incremental,
        fixture=args.fixture,
    )
    summary["mtg"]["run"] = mtg_run
    summary["mtg"]["ok"] = mtg_run["ok"]
    _accumulate(summary["mtg"]["totals"], mtg_run["stats"])

    yugioh_run = _run_connector(
        "ygoprodeck_yugioh",
        args.path,
        limit=args.yugioh_limit or args.batch_size,
        incremental=args.incremental,
        fixture=args.fixture,
    )
    summary["yugioh"]["run"] = yugioh_run
    summary["yugioh"]["ok"] = yugioh_run["ok"]
    _accumulate(summary["yugioh"]["totals"], yugioh_run["stats"])

    riftbound_run = _run_connector(
        "riftbound",
        args.path,
        limit=args.riftbound_limit or args.batch_size,
        incremental=args.incremental,
        fixture=args.riftbound_fixture,
    )
    summary["riftbound"]["run"] = riftbound_run
    summary["riftbound"]["ok"] = riftbound_run["ok"]
    _accumulate(summary["riftbound"]["totals"], riftbound_run["stats"])

    try:
        with db.SessionLocal() as session:
            reindex_stats = rebuild_search_documents(session)
            session.commit()
        summary["reindex"]["ok"] = True
        summary["reindex"]["stats"] = reindex_stats
    except Exception as exc:  # noqa: BLE001
        summary["reindex"]["error"] = str(exc)

    summary["ended_at"] = datetime.now(timezone.utc).isoformat()
    summary["duration_seconds"] = (datetime.now(timezone.utc) - started_at).total_seconds()

    all_failed = not summary["pokemon"]["ok"] and not summary["mtg"]["ok"] and not summary["yugioh"]["ok"] and not summary["riftbound"]["ok"]
    summary["exit_code"] = 1 if all_failed else 0
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Run daily incremental refresh (Pokemon + MTG + reindex)")
    parser.add_argument("--path", default=None, help="Optional fixture path")
    parser.add_argument("--pokemon-set", default=None, help="Single Pokemon set code (ex: base1)")
    parser.add_argument("--pokemon-sets", default=None, help="Comma-separated Pokemon set codes")
    parser.add_argument("--skip-pokemon", type=_to_bool, default=False, help="Skip Pokemon connector execution")
    parser.add_argument("--pokemon-all", type=_to_bool, default=False, help="Run a small curated list of Pokemon sets")
    parser.add_argument("--pokemon-all-sets", type=_to_bool, default=False, help="Iterate all Pokemon sets from TCGdex")
    parser.add_argument("--batch-size", type=int, default=200, help="Max records per connector call")
    parser.add_argument("--pokemon-limit", type=int, default=200)
    parser.add_argument("--mtg-limit", type=int, default=200)
    parser.add_argument("--yugioh-limit", type=int, default=200)
    parser.add_argument("--riftbound-limit", type=int, default=200)
    parser.add_argument("--riftbound-fixture", type=_to_bool, default=True)
    parser.add_argument("--incremental", type=_to_bool, default=True)
    parser.add_argument("--fixture", type=_to_bool, default=False)
    parser.add_argument("--sleep-seconds", type=float, default=1.0, help="Sleep between remote connector calls")
    args = parser.parse_args()

    if args.batch_size <= 0:
        args.batch_size = 200

    db.init_engine()
    summary = {"exit_code": 1, "error": "daily_refresh_failed"}
    try:
        summary = run_daily_refresh(args)
    except Exception as exc:  # noqa: BLE001
        summary["detail"] = str(exc)

    print(json.dumps(summary, ensure_ascii=False))
    return int(summary.get("exit_code", 1))


if __name__ == "__main__":
    raise SystemExit(main())
