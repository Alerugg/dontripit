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


def _attempted_connector_states(summary: dict) -> list[tuple[str, bool]]:
    states: list[tuple[str, bool]] = []
    for key in ("pokemon", "onepiece", "mtg", "yugioh", "riftbound"):
        item = summary[key]
        if item.get("skipped"):
            continue
        states.append((key, bool(item.get("ok"))))
    return states


def run_daily_refresh(args: argparse.Namespace) -> dict:
    started_at = datetime.now(timezone.utc)
    summary: dict = {
        "started_at": started_at.isoformat(),
        "incremental": bool(args.incremental),
        "batch_size": args.batch_size,
        "pokemon": {
            "ok": False,
            "skipped": bool(args.skip_pokemon or args.pokemon_limit == 0),
            "runs": [],
            "totals": _empty_stats(),
        },
        "onepiece": {
            "ok": False,
            "skipped": bool(args.skip_onepiece or args.onepiece_limit == 0),
            "run": None,
            "totals": _empty_stats(),
        },
        "mtg": {"ok": False, "skipped": bool(args.mtg_limit == 0), "run": None, "totals": _empty_stats()},
        "yugioh": {"ok": False, "skipped": bool(args.yugioh_limit == 0), "run": None, "totals": _empty_stats()},
        "riftbound": {"ok": False, "skipped": bool(args.riftbound_limit == 0), "run": None, "totals": _empty_stats()},
        "reindex": {"ok": False, "stats": {}, "error": None},
    }

    if not summary["pokemon"]["skipped"]:
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
            print(
                "[daily_refresh] pokemon_run=" + json.dumps(pokemon_run, ensure_ascii=False, sort_keys=True),
                flush=True,
            )
            time.sleep(max(args.sleep_seconds, 0))

        summary["pokemon"]["ok"] = bool(summary["pokemon"]["runs"]) and all(
            run["ok"] for run in summary["pokemon"]["runs"]
        )

    if not summary["onepiece"]["skipped"]:
        onepiece_run = _run_connector(
            "onepiece",
            args.path,
            limit=args.onepiece_limit,
            incremental=args.incremental,
            fixture=args.fixture,
        )
        summary["onepiece"]["run"] = onepiece_run
        summary["onepiece"]["ok"] = onepiece_run["ok"]
        _accumulate(summary["onepiece"]["totals"], onepiece_run["stats"])
        print("[daily_refresh] onepiece_run=" + json.dumps(onepiece_run, ensure_ascii=False, sort_keys=True), flush=True)

    if not summary["mtg"]["skipped"]:
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

    if not summary["yugioh"]["skipped"]:
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

    if not summary["riftbound"]["skipped"]:
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

    connector_mutations = sum(
        summary[key]["totals"]["inserted"] + summary[key]["totals"]["updated"]
        for key in ("pokemon", "onepiece", "mtg", "yugioh", "riftbound")
    )
    should_reindex = (not args.incremental) and connector_mutations > 0
    summary["reindex"]["trigger"] = "full_refresh" if should_reindex else "connector_managed_or_no_mutations"

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
        summary["reindex"]["stats"] = {
            "skipped": True,
            "mutations": connector_mutations,
            "reason": "incremental connectors reindex touched entities themselves" if args.incremental else "no mutations",
        }

    summary["ended_at"] = datetime.now(timezone.utc).isoformat()
    summary["duration_seconds"] = (datetime.now(timezone.utc) - started_at).total_seconds()

    attempted = _attempted_connector_states(summary)
    succeeded = [name for name, ok in attempted if ok]
    failed = [name for name, ok in attempted if not ok]
    skipped = [
        key
        for key in ("pokemon", "onepiece", "mtg", "yugioh", "riftbound")
        if summary[key].get("skipped")
    ]

    summary["connectors"] = {
        "attempted": [name for name, _ok in attempted],
        "succeeded": succeeded,
        "failed": failed,
        "skipped": skipped,
    }

    if failed and succeeded:
        summary["status"] = "degraded"
    elif failed:
        summary["status"] = "failed"
    elif attempted:
        summary["status"] = "success"
    else:
        summary["status"] = "skipped"

    if not summary["reindex"]["ok"]:
        summary["status"] = "failed" if not succeeded else "degraded"

    # A degraded refresh must be visible to CI. Previously the script returned 0
    # as long as any connector succeeded, masking broken sources for months.
    summary["exit_code"] = 1 if failed or not summary["reindex"]["ok"] else 0
    return summary


def build_refresh_args(
    *,
    path: str | None = None,
    pokemon_set: str | None = None,
    pokemon_limit: int | None = None,
    onepiece_limit: int | None = None,
    mtg_limit: int | None = None,
    yugioh_limit: int | None = None,
    riftbound_limit: int | None = None,
    incremental: bool = True,
    batch_size: int = 200,
    fixture: bool = False,
    riftbound_fixture: bool = False,
    skip_pokemon: bool = False,
    skip_onepiece: bool = False,
    pokemon_all: bool = False,
    pokemon_all_sets: bool = False,
    pokemon_sets: str | None = None,
    sleep_seconds: float = 1.0,
) -> argparse.Namespace:
    return argparse.Namespace(
        path=path,
        pokemon_set=pokemon_set,
        pokemon_limit=pokemon_limit,
        onepiece_limit=onepiece_limit,
        mtg_limit=mtg_limit,
        yugioh_limit=yugioh_limit,
        riftbound_limit=riftbound_limit,
        incremental=incremental,
        batch_size=batch_size,
        fixture=fixture,
        riftbound_fixture=riftbound_fixture,
        skip_pokemon=skip_pokemon,
        skip_onepiece=skip_onepiece,
        pokemon_all=pokemon_all,
        pokemon_all_sets=pokemon_all_sets,
        pokemon_sets=pokemon_sets,
        sleep_seconds=sleep_seconds,
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run catalog refresh (Pokemon + One Piece + MTG + Yu-Gi-Oh + Riftbound)"
    )
    parser.add_argument("--path", default=None, help="Optional fixture path")
    parser.add_argument("--pokemon-set", default=None, help="Single Pokemon set code (ex: base1)")
    parser.add_argument("--pokemon-sets", default=None, help="Comma-separated Pokemon set codes")
    parser.add_argument("--skip-pokemon", type=_to_bool, default=False, help="Skip Pokemon connector execution")
    parser.add_argument("--skip-onepiece", type=_to_bool, default=False, help="Skip One Piece connector execution")
    parser.add_argument("--pokemon-all", type=_to_bool, default=False, help="Run all Pokemon sets from TCGdex")
    parser.add_argument("--pokemon-all-sets", type=_to_bool, default=False, help="Iterate all Pokemon sets from TCGdex")
    parser.add_argument("--batch-size", type=int, default=200, help="Compatibility setting for refresh jobs")
    parser.add_argument("--pokemon-limit", type=int, default=None)
    parser.add_argument("--onepiece-limit", type=int, default=None)
    parser.add_argument("--mtg-limit", type=int, default=None)
    parser.add_argument("--yugioh-limit", type=int, default=None)
    parser.add_argument("--riftbound-limit", type=int, default=None)
    parser.add_argument("--riftbound-fixture", type=_to_bool, default=False)
    parser.add_argument("--incremental", type=_to_bool, default=True)
    parser.add_argument("--fixture", type=_to_bool, default=False)
    parser.add_argument("--sleep-seconds", type=float, default=1.0, help="Sleep between Pokemon set connector calls")
    args = parser.parse_args()

    if args.batch_size <= 0:
        args.batch_size = 200

    db.init_engine()
    summary = {"exit_code": 1, "status": "failed", "error": "daily_refresh_failed"}
    try:
        summary = run_daily_refresh(args)
    except Exception as exc:  # noqa: BLE001
        summary["detail"] = str(exc)

    print(json.dumps(summary, ensure_ascii=False))
    return int(summary.get("exit_code", 1))


if __name__ == "__main__":
    raise SystemExit(main())
