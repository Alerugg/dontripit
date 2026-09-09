#!/usr/bin/env python3
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from pathlib import Path
from typing import Iterable

import requests

from app.ingest.connectors.onepiece_incremental_guard import SelfHealingOnePieceCanonicalConnector
from app.ingest.connectors.scryfall_mtg_v2 import ScryfallMtgV2Connector
from app.ingest.connectors.ygoprodeck_yugioh_v2 import YgoProDeckYugiohV2Connector


USER_AGENT = "DontRipIt-PhysicalIdentityV2/1.0 (+https://github.com/Alerugg/dontripit)"
POKEMON_LANGUAGES = ("en", "es", "ja")


def stable_shard(key: str, shard_count: int) -> int:
    digest = hashlib.sha256(key.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") % shard_count


def _request_json(url: str):
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})
    response = session.get(url, timeout=60)
    response.raise_for_status()
    return response.json()


def _write_rows(rows: Iterable[tuple[str, dict]], *, game: str, shard_count: int, out_dir: Path) -> dict:
    game_dir = out_dir / game
    game_dir.mkdir(parents=True, exist_ok=True)
    paths = [game_dir / f"shard-{i:05d}-of-{shard_count:05d}.ndjson.gz" for i in range(shard_count)]
    handles = [gzip.open(path, "wt", encoding="utf-8", compresslevel=6) for path in paths]
    counts = [0] * shard_count
    seen_keys: set[str] = set()
    duplicates = 0
    total = 0
    try:
        for key, row in rows:
            if key in seen_keys:
                duplicates += 1
                continue
            seen_keys.add(key)
            shard = stable_shard(key, shard_count)
            handles[shard].write(json.dumps(row, ensure_ascii=False, separators=(",", ":"), default=str) + "\n")
            counts[shard] += 1
            total += 1
    finally:
        for handle in handles:
            handle.close()

    meta = {
        "game": game,
        "shard_count": shard_count,
        "total_rows": total,
        "duplicate_source_keys_skipped": duplicates,
        "rows_per_shard": counts,
        "empty_shards": [index for index, count in enumerate(counts) if count == 0],
        "partition": "sha256(source_key) modulo shard_count",
    }
    (game_dir / "manifest.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    print("IDENTITY_V2_PREPARE=" + json.dumps(meta, separators=(",", ":")))
    return meta


def pokemon_rows() -> Iterable[tuple[str, dict]]:
    for language in POKEMON_LANGUAGES:
        payload = _request_json(f"https://api.tcgdex.net/v2/{language}/cards")
        if not isinstance(payload, list):
            raise RuntimeError(f"Unexpected TCGdex cards payload for {language}: {type(payload).__name__}")
        region = "jp" if language == "ja" else "international"
        for item in payload:
            if not isinstance(item, dict):
                continue
            external_id = str(item.get("id") or "").strip()
            if not external_id:
                continue
            source_key = f"{language}:{external_id}"
            yield source_key, {
                "language": language,
                "region": region,
                "id": external_id,
            }


def mtg_rows() -> Iterable[tuple[str, dict]]:
    connector = ScryfallMtgV2Connector()
    for card in connector._download_default_cards():
        if not connector._is_paper_card(card):
            continue
        scryfall_id = str(card.get("id") or "").strip()
        if not scryfall_id:
            continue
        compact = {
            key: card.get(key)
            for key in (
                "id",
                "name",
                "oracle_id",
                "set",
                "collector_number",
                "lang",
                "rarity",
                "cardmarket_id",
                "illustration_id",
                "security_stamp",
                "promo_types",
                "frame_effects",
                "border_color",
                "variation_of",
                "variation",
                "finishes",
                "nonfoil",
                "foil",
                "promo",
            )
        }
        yield scryfall_id, compact


def yugioh_rows() -> Iterable[tuple[str, dict]]:
    connector = YgoProDeckYugiohV2Connector()
    for card in connector._load_remote():
        card_id = str(card.get("id") or "").strip()
        if not card_id:
            continue
        compact = {
            "id": card.get("id"),
            "name": card.get("name"),
            "card_sets": card.get("card_sets") or [],
            "card_images": card.get("card_images") or [],
        }
        yield card_id, compact


def onepiece_rows() -> Iterable[tuple[str, dict]]:
    connector = SelfHealingOnePieceCanonicalConnector()
    loaded = connector.load(None, fixture=False)
    for _path, payload, _checksum in loaded:
        if not isinstance(payload, dict):
            continue
        language = str(payload.get("language") or "").strip()
        region = str(payload.get("region") or "").strip()
        for card in payload.get("cards") or []:
            if not isinstance(card, dict):
                continue
            card_id = str(card.get("id") or "").strip()
            if not card_id:
                continue
            source_key = f"{language}:{region}:{card_id}"
            yield source_key, {
                "language": language,
                "region": region,
                "card": card,
            }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--game", required=True, choices=("pokemon", "mtg", "yugioh", "onepiece"))
    parser.add_argument("--shard-count", required=True, type=int)
    parser.add_argument("--out-dir", required=True, type=Path)
    args = parser.parse_args()
    if args.shard_count < 1:
        raise SystemExit("--shard-count must be >= 1")

    factories = {
        "pokemon": pokemon_rows,
        "mtg": mtg_rows,
        "yugioh": yugioh_rows,
        "onepiece": onepiece_rows,
    }
    meta = _write_rows(
        factories[args.game](),
        game=args.game,
        shard_count=args.shard_count,
        out_dir=args.out_dir,
    )
    if meta["total_rows"] <= 0:
        raise RuntimeError(f"Prepared zero source rows for {args.game}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
