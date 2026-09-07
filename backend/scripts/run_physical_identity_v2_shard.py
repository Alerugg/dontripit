#!/usr/bin/env python3
from __future__ import annotations

import argparse
import gzip
import json
import random
import time
from pathlib import Path

import requests

from app.identity_sources.onepiece_official_v2 import claims_from_payload as onepiece_claims_from_payload
from app.identity_sources.scryfall_mtg_v2 import claims_from_card as mtg_claims_from_card
from app.identity_sources.tcgdex_pokemon_v2 import claims_from_card as pokemon_claims_from_card
from app.identity_sources.ygoprodeck_yugioh_v2 import claims_from_card as yugioh_claims_from_card
from app.physical_identity_v2 import PhysicalIdentityDescriptor


USER_AGENT = "DontRipIt-PhysicalIdentityV2/1.0 (+https://github.com/Alerugg/dontripit)"


def _find_manifest(root: Path, shard_index: int, shard_count: int) -> Path:
    name = f"shard-{shard_index:05d}-of-{shard_count:05d}.ndjson.gz"
    matches = list(root.rglob(name))
    if len(matches) != 1:
        raise RuntimeError(f"Expected exactly one manifest {name}, found {len(matches)} under {root}")
    return matches[0]


def _load_manifest(path: Path) -> list[dict]:
    rows: list[dict] = []
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise RuntimeError(f"Invalid manifest JSON at {path}:{line_number}") from exc
            if isinstance(row, dict):
                rows.append(row)
    return rows


def _descriptor_record(descriptor: PhysicalIdentityDescriptor) -> dict:
    canonical = descriptor.canonical_payload()
    return {
        "fingerprint": descriptor.fingerprint(),
        "game": descriptor.game,
        "source": descriptor.source,
        "source_print_id": descriptor.source_print_id,
        "card_concept": descriptor.card_concept,
        "release": descriptor.release,
        "dimensions": canonical["dimensions"],
        "source_facts": dict(descriptor.source_facts),
    }


def _market_claim(*, game: str, external_product_id: str, fingerprint: str, source: str, source_object_id: str) -> dict:
    return {
        "game": game,
        "market": "cardmarket",
        "external_product_id": str(external_product_id),
        "fingerprint": fingerprint,
        "evidence_source": source,
        "source_object_id": source_object_id,
    }


def _request_json(session: requests.Session, url: str, *, attempts: int = 5) -> dict:
    delay = 0.7
    last_error: Exception | None = None
    retry_status = {403, 408, 425, 429, 500, 502, 503, 504}
    for attempt in range(1, attempts + 1):
        try:
            response = session.get(url, timeout=45)
            if response.status_code in retry_status:
                raise requests.HTTPError(f"retryable HTTP {response.status_code}", response=response)
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise RuntimeError(f"Expected object JSON from {url}, got {type(payload).__name__}")
            return payload
        except (requests.RequestException, ValueError, RuntimeError) as exc:
            last_error = exc
            if attempt >= attempts:
                break
            time.sleep(min(delay + random.random() * 0.3, 8.0))
            delay *= 2
    raise RuntimeError(f"Request failed after {attempts} attempts: {url}: {last_error}")


def _pokemon_object(row: dict, session: requests.Session) -> tuple[list, list[dict], dict | None]:
    language = str(row.get("language") or "en").strip().lower()
    region = str(row.get("region") or ("jp" if language == "ja" else "international")).strip()
    external_id = str(row.get("id") or "").strip()
    if not external_id:
        raise RuntimeError("Pokemon manifest row has no id")
    card = _request_json(session, f"https://api.tcgdex.net/v2/{language}/cards/{external_id}")
    claims = pokemon_claims_from_card(card, language=language, region=region)
    if not claims:
        return [], [], {
            "type": "unresolved_source_data",
            "reason": "tcgdex_full_card_has_no_detailed_physical_variants",
            "source": "tcgdex",
            "language": language,
            "region": region,
            "source_object_id": external_id,
            "source_facts": {
                "name": card.get("name"),
                "localId": card.get("localId"),
                "rarity": card.get("rarity"),
                "set": card.get("set"),
                "variants": card.get("variants"),
                "variants_detailed": card.get("variants_detailed"),
            },
        }
    market = []
    for claim in claims:
        if claim.cardmarket_id:
            market.append(
                _market_claim(
                    game="pokemon",
                    external_product_id=claim.cardmarket_id,
                    fingerprint=claim.descriptor.fingerprint(),
                    source="tcgdex:variants_detailed",
                    source_object_id=claim.variant_id,
                )
            )
    return claims, market, None


def _local_object(game: str, row: dict) -> tuple[list, list[dict]]:
    if game == "mtg":
        claims = mtg_claims_from_card(row)
        market = []
        for claim in claims:
            if claim.cardmarket_id:
                market.append(
                    _market_claim(
                        game="mtg",
                        external_product_id=claim.cardmarket_id,
                        fingerprint=claim.descriptor.fingerprint(),
                        source="scryfall:cardmarket_id",
                        source_object_id=claim.descriptor.source_print_id,
                    )
                )
        return claims, market
    if game == "yugioh":
        return yugioh_claims_from_card(row), []
    if game == "onepiece":
        payload = {
            "language": row.get("language"),
            "region": row.get("region"),
            "cards": [row.get("card") or {}],
        }
        return onepiece_claims_from_payload(payload), []
    raise RuntimeError(f"Unsupported local game: {game}")


def _unresolved_local(game: str, row: dict) -> dict:
    source = {"mtg": "scryfall", "yugioh": "ygoprodeck", "onepiece": "onepiece_official"}[game]
    return {
        "type": "unresolved_source_data",
        "reason": "source_object_has_no_certifiable_physical_claim",
        "source": source,
        "game": game,
        "source_object": row,
    }


def _write_checkpoint(path: Path, payload: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--game", required=True, choices=("pokemon", "mtg", "yugioh", "onepiece"))
    parser.add_argument("--shard-index", required=True, type=int)
    parser.add_argument("--shard-count", required=True, type=int)
    parser.add_argument("--manifest-root", required=True, type=Path)
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--retry-passes", type=int, default=3)
    parser.add_argument("--pokemon-throttle-seconds", type=float, default=0.12)
    args = parser.parse_args()

    if not (0 <= args.shard_index < args.shard_count):
        raise SystemExit("shard index must satisfy 0 <= index < count")

    manifest = _find_manifest(args.manifest_root, args.shard_index, args.shard_count)
    rows = _load_manifest(manifest)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    descriptor_path = args.out_dir / "descriptors.ndjson"
    market_path = args.out_dir / "market_claims.ndjson"
    unresolved_path = args.out_dir / "unresolved.ndjson"
    error_path = args.out_dir / "errors.ndjson"
    summary_path = args.out_dir / "summary.json"

    started = time.time()
    source_identity_to_fingerprint: dict[tuple[str, str], str] = {}
    fingerprints: set[str] = set()
    direct_market_ids: set[str] = set()
    conflicts: list[dict] = []
    completed_objects = 0
    unresolved_objects = 0
    descriptor_count = 0
    market_claim_count = 0
    pending = list(enumerate(rows))
    final_errors: list[dict] = []

    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})

    with descriptor_path.open("w", encoding="utf-8", buffering=1) as descriptor_file, market_path.open(
        "w", encoding="utf-8", buffering=1
    ) as market_file, unresolved_path.open("w", encoding="utf-8", buffering=1) as unresolved_file, error_path.open(
        "w", encoding="utf-8", buffering=1
    ) as error_file:
        for pass_index in range(args.retry_passes + 1):
            if not pending:
                break
            current = pending
            pending = []
            if pass_index:
                time.sleep(min(2.0 * pass_index, 8.0))

            for row_index, row in current:
                try:
                    unresolved = None
                    if args.game == "pokemon":
                        claims, market_claims, unresolved = _pokemon_object(row, session)
                        if args.pokemon_throttle_seconds > 0:
                            time.sleep(args.pokemon_throttle_seconds)
                    else:
                        claims, market_claims = _local_object(args.game, row)
                        if not claims:
                            unresolved = _unresolved_local(args.game, row)

                    if unresolved is not None:
                        unresolved.update({"row_index": row_index, "shard_index": args.shard_index})
                        unresolved_file.write(json.dumps(unresolved, ensure_ascii=False, separators=(",", ":"), default=str) + "\n")
                        unresolved_objects += 1
                        completed_objects += 1
                        continue

                    for claim in claims:
                        descriptor = claim.descriptor
                        record = _descriptor_record(descriptor)
                        source_key = (descriptor.source, descriptor.source_print_id)
                        fingerprint = record["fingerprint"]
                        previous = source_identity_to_fingerprint.get(source_key)
                        if previous is not None and previous != fingerprint:
                            conflict = {
                                "type": "source_identity_conflict",
                                "source": source_key[0],
                                "source_print_id": source_key[1],
                                "first_fingerprint": previous,
                                "second_fingerprint": fingerprint,
                            }
                            conflicts.append(conflict)
                            error_file.write(json.dumps(conflict, separators=(",", ":")) + "\n")
                            continue
                        if previous == fingerprint:
                            continue
                        source_identity_to_fingerprint[source_key] = fingerprint
                        descriptor_file.write(json.dumps(record, ensure_ascii=False, separators=(",", ":"), default=str) + "\n")
                        fingerprints.add(fingerprint)
                        descriptor_count += 1

                    for market_claim in market_claims:
                        market_file.write(json.dumps(market_claim, ensure_ascii=False, separators=(",", ":")) + "\n")
                        direct_market_ids.add(str(market_claim["external_product_id"]))
                        market_claim_count += 1

                    completed_objects += 1
                except Exception as exc:  # technical/source transport errors retry; source incompleteness does not
                    failure = {
                        "type": "source_object_error",
                        "row_index": row_index,
                        "pass": pass_index,
                        "row": row,
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                    }
                    error_file.write(json.dumps(failure, ensure_ascii=False, separators=(",", ":"), default=str) + "\n")
                    if pass_index < args.retry_passes:
                        pending.append((row_index, row))
                    else:
                        final_errors.append(failure)

                if (completed_objects + len(final_errors)) % 25 == 0:
                    _write_checkpoint(
                        summary_path,
                        {
                            "status": "running",
                            "game": args.game,
                            "shard_index": args.shard_index,
                            "shard_count": args.shard_count,
                            "manifest_rows": len(rows),
                            "completed_objects": completed_objects,
                            "unresolved_source_objects": unresolved_objects,
                            "pending_objects": len(pending),
                            "final_error_objects": len(final_errors),
                            "descriptors": descriptor_count,
                            "market_claim_rows": market_claim_count,
                            "source_identity_conflicts": len(conflicts),
                        },
                    )

    status = "pass" if not final_errors and not conflicts and completed_objects == len(rows) else "incomplete"
    summary = {
        "status": status,
        "game": args.game,
        "shard_index": args.shard_index,
        "shard_count": args.shard_count,
        "manifest": str(manifest),
        "manifest_rows": len(rows),
        "completed_objects": completed_objects,
        "unresolved_source_objects": unresolved_objects,
        "final_error_objects": len(final_errors),
        "descriptors": descriptor_count,
        "unique_fingerprints": len(fingerprints),
        "market_claim_rows": market_claim_count,
        "direct_cardmarket_ids": len(direct_market_ids),
        "source_identity_conflicts": len(conflicts),
        "duration_seconds": round(time.time() - started, 3),
        "production_writes": 0,
    }
    _write_checkpoint(summary_path, summary)
    print("IDENTITY_V2_SHARD=" + json.dumps(summary, separators=(",", ":")))
    return 0 if status == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
