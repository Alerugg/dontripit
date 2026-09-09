#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import time
from collections import Counter, defaultdict
from pathlib import Path

import requests


BASE_URL = "https://api.cardtrader.com/api/v2"
GAME_ALIASES = {
    "mtg": ("magic", "magic the gathering"),
    "pokemon": ("pokemon", "pokémon"),
    "yugioh": ("yu-gi-oh", "yugioh", "yu gi oh"),
    "onepiece": ("one piece",),
}


def norm(text: object) -> str:
    return " ".join(str(text or "").casefold().replace("!", "").replace("-", " ").split())


def request_json(session: requests.Session, url: str, *, params: dict | None = None):
    wait = 0.5
    for attempt in range(1, 6):
        response = session.get(url, params=params, timeout=180)
        if response.status_code in (429, 500, 502, 503, 504):
            if attempt == 5:
                response.raise_for_status()
            retry_after = response.headers.get("Retry-After")
            delay = float(retry_after) if retry_after else wait
            time.sleep(delay)
            wait *= 2
            continue
        response.raise_for_status()
        return response.json()
    raise RuntimeError(f"request failed after retries: {url}")


def match_game_ids(games: list[dict]) -> dict[str, int]:
    matched: dict[str, int] = {}
    for slug, aliases in GAME_ALIASES.items():
        ranked = []
        for row in games:
            name = norm(row.get("name"))
            if not name:
                continue
            score = 0
            for alias in aliases:
                alias_norm = norm(alias)
                if name == alias_norm:
                    score = max(score, 3)
                elif alias_norm in name or name in alias_norm:
                    score = max(score, 1)
            if score:
                ranked.append((score, int(row["id"]), row.get("name")))
        ranked.sort(reverse=True)
        if ranked:
            matched[slug] = ranked[0][1]
    return matched


def main() -> int:
    ap = argparse.ArgumentParser(description="Read-only CardTrader identity bridge probe.")
    ap.add_argument("--max-expansions-per-game", type=int, default=3)
    ap.add_argument("--report", type=Path, default=Path("/tmp/cardtrader-identity-bridge-v2.json"))
    args = ap.parse_args()

    token = str(os.getenv("CARDTRADER_API_TOKEN") or "").strip()
    if not token:
        payload = {
            "mode": "read_only",
            "audit": "cardtrader_identity_bridge_v2",
            "status": "missing_token",
            "production_writes": 0,
            "message": "CARDTRADER_API_TOKEN is not configured; no network calls were made.",
        }
        print("CARDTRADER_IDENTITY_BRIDGE_V2=" + json.dumps(payload, separators=(",", ":")))
        args.report.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        return 0

    session = requests.Session()
    session.headers.update({
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "User-Agent": "DontRipIt-PhysicalIdentityV2/1.0",
    })

    games_payload = request_json(session, f"{BASE_URL}/games")
    if not isinstance(games_payload, list):
        raise RuntimeError(f"unexpected CardTrader games payload: {type(games_payload).__name__}")
    game_ids = match_game_ids(games_payload)

    expansions_payload = request_json(session, f"{BASE_URL}/expansions")
    if not isinstance(expansions_payload, list):
        raise RuntimeError(f"unexpected CardTrader expansions payload: {type(expansions_payload).__name__}")

    by_game: dict[int, list[dict]] = defaultdict(list)
    for row in expansions_payload:
        if not isinstance(row, dict) or row.get("game_id") is None or row.get("id") is None:
            continue
        by_game[int(row["game_id"])].append(row)
    for rows in by_game.values():
        rows.sort(key=lambda r: int(r.get("id") or 0), reverse=True)

    game_results = []
    for slug in GAME_ALIASES:
        game_id = game_ids.get(slug)
        if game_id is None:
            game_results.append({"game": slug, "status": "game_not_found"})
            continue
        expansions = by_game.get(game_id, [])
        selected = expansions[: max(int(args.max_expansions_per_game), 0)]
        blueprints: list[dict] = []
        for expansion in selected:
            rows = request_json(
                session,
                f"{BASE_URL}/blueprints/export",
                params={"expansion_id": int(expansion["id"])},
            )
            if not isinstance(rows, list):
                raise RuntimeError(
                    f"unexpected CardTrader blueprint payload game={slug} expansion={expansion['id']}: "
                    f"{type(rows).__name__}"
                )
            for row in rows:
                if isinstance(row, dict):
                    item = dict(row)
                    item["_expansion_name"] = expansion.get("name")
                    item["_expansion_code"] = expansion.get("code")
                    blueprints.append(item)

        cm_to_blueprints: dict[str, set[int]] = defaultdict(set)
        blueprints_with_cm = 0
        blueprints_multi_cm = 0
        versions_present = 0
        editable_property_names = Counter()
        for bp in blueprints:
            bp_id = int(bp["id"])
            cm_ids = [str(x).strip() for x in (bp.get("card_market_ids") or []) if str(x).strip()]
            if cm_ids:
                blueprints_with_cm += 1
            if len(set(cm_ids)) > 1:
                blueprints_multi_cm += 1
            if str(bp.get("version") or "").strip():
                versions_present += 1
            for cm_id in set(cm_ids):
                cm_to_blueprints[cm_id].add(bp_id)
            for prop in bp.get("editable_properties") or []:
                if isinstance(prop, dict) and prop.get("name"):
                    editable_property_names[str(prop["name"])] += 1

        cm_ids_total = len(cm_to_blueprints)
        cm_ids_conflicting = sum(1 for owners in cm_to_blueprints.values() if len(owners) > 1)
        alias_cm_ids = sum(
            len(set(str(x).strip() for x in (bp.get("card_market_ids") or []) if str(x).strip()))
            for bp in blueprints
            if len(set(str(x).strip() for x in (bp.get("card_market_ids") or []) if str(x).strip())) > 1
        )

        samples = []
        for bp in blueprints:
            cm_ids = [str(x).strip() for x in (bp.get("card_market_ids") or []) if str(x).strip()]
            if len(set(cm_ids)) > 1 or str(bp.get("version") or "").strip():
                samples.append({
                    "blueprint_id": bp.get("id"),
                    "name": bp.get("name"),
                    "version": bp.get("version"),
                    "expansion_id": bp.get("expansion_id"),
                    "expansion_name": bp.get("_expansion_name"),
                    "scryfall_id": bp.get("scryfall_id"),
                    "card_market_ids": cm_ids,
                    "editable_property_names": [
                        prop.get("name") for prop in (bp.get("editable_properties") or [])
                        if isinstance(prop, dict)
                    ],
                })
            if len(samples) >= 25:
                break

        game_results.append({
            "game": slug,
            "status": "ok",
            "cardtrader_game_id": game_id,
            "expansions_available": len(expansions),
            "expansions_probed": len(selected),
            "blueprints_probed": len(blueprints),
            "blueprints_with_cardmarket_ids": blueprints_with_cm,
            "blueprints_with_multiple_cardmarket_ids": blueprints_multi_cm,
            "blueprints_with_version": versions_present,
            "distinct_cardmarket_ids_observed": cm_ids_total,
            "cardmarket_ids_claimed_by_multiple_blueprints": cm_ids_conflicting,
            "cardmarket_ids_inside_multi_id_blueprints": alias_cm_ids,
            "editable_property_names": editable_property_names.most_common(20),
            "samples": samples,
        })

    payload = {
        "mode": "read_only",
        "audit": "cardtrader_identity_bridge_v2",
        "status": "ok",
        "production_writes": 0,
        "max_expansions_per_game": args.max_expansions_per_game,
        "matched_game_ids": game_ids,
        "games": game_results,
    }
    print("CARDTRADER_IDENTITY_BRIDGE_V2=" + json.dumps({
        "status": payload["status"],
        "matched_game_ids": payload["matched_game_ids"],
        "games": [
            {
                "game": row.get("game"),
                "status": row.get("status"),
                "expansions_probed": row.get("expansions_probed"),
                "blueprints_probed": row.get("blueprints_probed"),
                "blueprints_with_cardmarket_ids": row.get("blueprints_with_cardmarket_ids"),
                "blueprints_with_multiple_cardmarket_ids": row.get("blueprints_with_multiple_cardmarket_ids"),
                "cardmarket_ids_claimed_by_multiple_blueprints": row.get("cardmarket_ids_claimed_by_multiple_blueprints"),
            }
            for row in game_results
        ],
        "production_writes": 0,
    }, separators=(",", ":")))
    args.report.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
