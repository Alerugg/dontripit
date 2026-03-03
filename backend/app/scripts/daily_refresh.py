from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone

from app import db
from app.ingest.registry import get_connector
from app.scripts.reindex_search import rebuild_search_documents

DEFAULT_POKEMON_SETS = ["base1", "sv1"]


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
        "pokemon": {"ok": False, "runs": [], "totals": _empty_stats()},
        "mtg": {"ok": False, "run": None, "totals": _empty_stats()},
        "reindex": {"ok": False, "stats": {}, "error": None},
    }

    pokemon_sets: list[str]
    if args.pokemon_set:
        pokemon_sets = [args.pokemon_set]
    elif args.pokemon_all:
        # TODO: fetch complete set list dynamically from TCGdex sets endpoint.
        pokemon_sets = DEFAULT_POKEMON_SETS
    else:
        pokemon_sets = [None]

    for pokemon_set in pokemon_sets:
        pokemon_run = _run_connector(
            "tcgdex_pokemon",
            args.path,
            set=pokemon_set,
            limit=args.pokemon_limit,
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
        limit=args.mtg_limit,
        incremental=args.incremental,
        fixture=args.fixture,
    )
    summary["mtg"]["run"] = mtg_run
    summary["mtg"]["ok"] = mtg_run["ok"]
    _accumulate(summary["mtg"]["totals"], mtg_run["stats"])

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

    both_failed = not summary["pokemon"]["ok"] and not summary["mtg"]["ok"]
    summary["exit_code"] = 1 if both_failed else 0
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Run daily incremental refresh (Pokemon + MTG + reindex)")
    parser.add_argument("--path", default=None, help="Optional fixture path")
    parser.add_argument("--pokemon-set", default=None, help="Single Pokemon set code (ex: base1)")
    parser.add_argument("--pokemon-all", type=_to_bool, default=False, help="Run a small curated list of Pokemon sets")
    parser.add_argument("--pokemon-limit", type=int, default=200)
    parser.add_argument("--mtg-limit", type=int, default=200)
    parser.add_argument("--incremental", type=_to_bool, default=True)
    parser.add_argument("--fixture", type=_to_bool, default=False)
    parser.add_argument("--sleep-seconds", type=float, default=1.0, help="Sleep between remote connector calls")
    args = parser.parse_args()

    db.init_engine()
    summary = run_daily_refresh(args)
    print(json.dumps(summary, ensure_ascii=False))
    return int(summary["exit_code"])


if __name__ == "__main__":
    raise SystemExit(main())
