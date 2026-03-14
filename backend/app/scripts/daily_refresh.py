from __future__ import annotations

import argparse
import json
import time
import traceback
from datetime import datetime, timezone

import requests

from app import db
from app.ingest.registry import get_connector
from app.scripts.reindex_search import rebuild_search_documents

DEFAULT_POKEMON_SETS = ["base1", "base2", "base3", "base4", "base5", "gym1", "gym2", "neo1", "neo2", "sv1"]
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
        try:
            sets = _fetch_all_pokemon_sets()
            summary["pokemon"]["set_source"] = "tcgdex"
            return sets
        except Exception as exc:  # noqa: BLE001
            summary["pokemon"]["set_source"] = "default_fallback"
            summary["pokemon"]["set_fetch_error"] = str(exc)
            return DEFAULT_POKEMON_SETS

    return [None]


def _run_connector(connector_name: str, path: str | None = None, **kwargs) -> dict:
    result = {
        "connector": connector_name,
        "ok": False,
        "error": None,
        "stats": _empty_stats(),
    }

    connector_kwargs = {
        "limit": kwargs.get("limit"),
        "incremental": kwargs.get("incremental"),
        "fixture": kwargs.get("fixture"),
        "set": kwargs.get("set"),
        "path": path,
    }
    print(
        "[daily_refresh] connector_start="
        + json.dumps({"connector": connector_name, **connector_kwargs}, ensure_ascii=False, sort_keys=True),
        flush=True,
    )

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
            print(
                f"[daily_refresh] connector_error connector={connector_name} error={exc}",
                flush=True,
            )
            print(traceback.format_exc(), flush=True)

    print(
        "[daily_refresh] connector_done="
        + json.dumps(
            {
                "connector": connector_name,
                "ok": result["ok"],
                "stats": result["stats"],
                "error": result["error"],
            },
            ensure_ascii=False,
            sort_keys=True,
        ),
        flush=True,
    )
    return result


def run_daily_refresh(args: argparse.Namespace) -> dict:
    started_at = datetime.now(timezone.utc)
    summary: dict = {
        "started_at": started_at.isoformat(),
        "incremental": bool(args.incremental),
        "batch_size": args.batch_size,
        "pokemon": {"ok": False, "skipped": bool(args.skip_pokemon or args.pokemon_limit == 0), "runs": [], "totals": _empty_stats()},
        "mtg": {"ok": False, "skipped": bool(args.mtg_limit == 0), "run": None, "totals": _empty_stats()},
        "yugioh": {"ok": False, "skipped": bool(args.yugioh_limit == 0), "run": None, "totals": _empty_stats()},
        "riftbound": {"ok": False, "skipped": bool(args.riftbound_limit == 0), "run": None, "totals": _empty_stats()},
        "reindex": {"ok": False, "stats": {}, "error": None},
    }

    if not args.skip_pokemon and args.pokemon_limit != 0:
        pokemon_sets = _resolve_pokemon_sets(args, summary)

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
            print(
                "[daily_refresh] pokemon_run=" + json.dumps(pokemon_run, ensure_ascii=False, sort_keys=True),
                flush=True,
            )
            time.sleep(max(args.sleep_seconds, 0))

    if args.mtg_limit != 0:
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
        print("[daily_refresh] mtg_run=" + json.dumps(mtg_run, ensure_ascii=False, sort_keys=True), flush=True)

    if args.yugioh_limit != 0:
        yugioh_run = _run_connector(
            "ygoprodeck_yugioh",
            args.path,
            limit=args.yugioh_limit,
            incremental=args.incremental,
            fixture=args.fixture,
        )
        summary["yugioh"]["run"] = yugioh_run
        summary["yugioh"]["ok"] = yugioh_run["ok"]
        _accumulate(summary["yugioh"]["totals"], yugioh_run["stats"])
        print("[daily_refresh] yugioh_run=" + json.dumps(yugioh_run, ensure_ascii=False, sort_keys=True), flush=True)

    if args.riftbound_limit != 0:
        riftbound_run = _run_connector(
            "riftbound",
            args.path,
            limit=args.riftbound_limit,
            incremental=args.incremental,
            fixture=args.riftbound_fixture,
        )
        summary["riftbound"]["run"] = riftbound_run
        summary["riftbound"]["ok"] = riftbound_run["ok"]
        _accumulate(summary["riftbound"]["totals"], riftbound_run["stats"])
        print("[daily_refresh] riftbound_run=" + json.dumps(riftbound_run, ensure_ascii=False, sort_keys=True), flush=True)

    connector_mutations = (
        summary["pokemon"]["totals"]["inserted"]
        + summary["pokemon"]["totals"]["updated"]
        + summary["mtg"]["totals"]["inserted"]
        + summary["mtg"]["totals"]["updated"]
        + summary["yugioh"]["totals"]["inserted"]
        + summary["yugioh"]["totals"]["updated"]
        + summary["riftbound"]["totals"]["inserted"]
        + summary["riftbound"]["totals"]["updated"]
    )
    should_reindex = (not args.incremental) and connector_mutations > 0
    summary["reindex"]["trigger"] = "full_refresh" if should_reindex else "skipped_targeted_connector_reindex"

    if should_reindex:
        try:
            with db.SessionLocal() as session:
                reindex_stats = rebuild_search_documents(session)
                session.commit()
            summary["reindex"]["ok"] = True
            summary["reindex"]["stats"] = reindex_stats
        except Exception as exc:  # noqa: BLE001
            summary["reindex"]["error"] = str(exc)
    else:
        summary["reindex"]["ok"] = True
        summary["reindex"]["stats"] = {"skipped": True, "mutations": connector_mutations}

    summary["ended_at"] = datetime.now(timezone.utc).isoformat()
    summary["duration_seconds"] = (datetime.now(timezone.utc) - started_at).total_seconds()

    all_failed = not summary["pokemon"]["ok"] and not summary["mtg"]["ok"] and not summary["yugioh"]["ok"] and not summary["riftbound"]["ok"]
    summary["exit_code"] = 1 if all_failed else 0
    return summary


def build_refresh_args(
    *,
    path: str | None = None,
    pokemon_set: str | None = None,
    pokemon_limit: int | None = None,
    mtg_limit: int | None = None,
    yugioh_limit: int | None = None,
    riftbound_limit: int | None = None,
    incremental: bool = True,
    batch_size: int = 200,
    fixture: bool = False,
    riftbound_fixture: bool = False,
    skip_pokemon: bool = False,
    pokemon_all: bool = False,
    pokemon_all_sets: bool = False,
    pokemon_sets: str | None = None,
    sleep_seconds: float = 1.0,
) -> argparse.Namespace:
    return argparse.Namespace(
        path=path,
        pokemon_set=pokemon_set,
        pokemon_limit=pokemon_limit,
        mtg_limit=mtg_limit,
        yugioh_limit=yugioh_limit,
        riftbound_limit=riftbound_limit,
        incremental=incremental,
        batch_size=batch_size,
        fixture=fixture,
        riftbound_fixture=riftbound_fixture,
        skip_pokemon=skip_pokemon,
        pokemon_all=pokemon_all,
        pokemon_all_sets=pokemon_all_sets,
        pokemon_sets=pokemon_sets,
        sleep_seconds=sleep_seconds,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Run daily incremental refresh (Pokemon + MTG + reindex)")
    parser.add_argument("--path", default=None, help="Optional fixture path")
    parser.add_argument("--pokemon-set", default=None, help="Single Pokemon set code (ex: base1)")
    parser.add_argument("--pokemon-sets", default=None, help="Comma-separated Pokemon set codes")
    parser.add_argument("--skip-pokemon", type=_to_bool, default=False, help="Skip Pokemon connector execution")
    parser.add_argument("--pokemon-all", type=_to_bool, default=False, help="Run a small curated list of Pokemon sets")
    parser.add_argument("--pokemon-all-sets", type=_to_bool, default=False, help="Iterate all Pokemon sets from TCGdex")
    parser.add_argument("--batch-size", type=int, default=200, help="Max records per connector call")
    parser.add_argument("--pokemon-limit", type=int, default=None)
    parser.add_argument("--mtg-limit", type=int, default=None)
    parser.add_argument("--yugioh-limit", type=int, default=None)
    parser.add_argument("--riftbound-limit", type=int, default=None)
    parser.add_argument("--riftbound-fixture", type=_to_bool, default=False)
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
