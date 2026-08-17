from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import requests

from app.ingest.connectors.scryfall_mtg_v2 import ScryfallMtgV2Connector
from app.mtg_identity_v2 import card_identity_key, clean, finish_values, physical_print_key


SNAPSHOT_SCHEMA_VERSION = "mtg-canonical-v2.1"
IDENTITY_POLICY_VERSION = "oracle-or-rules-signature+scryfall-object-finish-v1"


def _json_dump(payload: object) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _norm_date(value: object) -> str | None:
    text = clean(value)
    return text or None


def _norm_list(value: object) -> list:
    return list(value) if isinstance(value, list) else []


def _norm_dict(value: object) -> dict:
    return dict(value) if isinstance(value, dict) else {}


def _face_payload(face: dict) -> dict:
    return {
        "name": clean(face.get("name")) or None,
        "mana_cost": clean(face.get("mana_cost")) or None,
        "type_line": clean(face.get("type_line")) or None,
        "oracle_text": clean(face.get("oracle_text")) or None,
        "colors": _norm_list(face.get("colors")),
        "power": clean(face.get("power")) or None,
        "toughness": clean(face.get("toughness")) or None,
        "loyalty": clean(face.get("loyalty")) or None,
        "defense": clean(face.get("defense")) or None,
    }


def _card_attributes(card: dict) -> dict:
    return {
        "layout": clean(card.get("layout")) or None,
        "mana_cost": clean(card.get("mana_cost")) or None,
        "mana_value": card.get("cmc"),
        "type_line": clean(card.get("type_line")) or None,
        "oracle_text": clean(card.get("oracle_text")) or None,
        "colors": _norm_list(card.get("colors")),
        "color_identity": _norm_list(card.get("color_identity")),
        "keywords": _norm_list(card.get("keywords")),
        "legalities": _norm_dict(card.get("legalities")),
        "power": clean(card.get("power")) or None,
        "toughness": clean(card.get("toughness")) or None,
        "loyalty": clean(card.get("loyalty")) or None,
        "defense": clean(card.get("defense")) or None,
        "produced_mana": _norm_list(card.get("produced_mana")),
        "reserved": bool(card.get("reserved")),
        "faces": [_face_payload(face) for face in _norm_list(card.get("card_faces")) if isinstance(face, dict)],
    }


def _print_attributes(card: dict, finish: str) -> dict:
    return {
        "finish": finish,
        "source_finishes": sorted({clean(value).lower() for value in _norm_list(card.get("finishes")) if clean(value)}),
        "released_at": _norm_date(card.get("released_at")),
        "set_type": clean(card.get("set_type")) or None,
        "scryfall_set_id": clean(card.get("set_id")) or None,
        "artist": clean(card.get("artist")) or None,
        "artist_ids": _norm_list(card.get("artist_ids")),
        "illustration_id": clean(card.get("illustration_id")) or None,
        "frame": clean(card.get("frame")) or None,
        "frame_effects": _norm_list(card.get("frame_effects")),
        "border_color": clean(card.get("border_color")) or None,
        "security_stamp": clean(card.get("security_stamp")) or None,
        "watermark": clean(card.get("watermark")) or None,
        "promo": bool(card.get("promo")),
        "promo_types": _norm_list(card.get("promo_types")),
        "full_art": bool(card.get("full_art")),
        "textless": bool(card.get("textless")),
        "booster": bool(card.get("booster")),
        "reprint": bool(card.get("reprint")),
        "oversized": bool(card.get("oversized")),
        "variation": bool(card.get("variation")),
        "variation_of": clean(card.get("variation_of")) or None,
        "story_spotlight": bool(card.get("story_spotlight")),
        "highres_image": bool(card.get("highres_image")),
        "image_status": clean(card.get("image_status")) or None,
        "printed_name": clean(card.get("printed_name")) or None,
        "printed_type_line": clean(card.get("printed_type_line")) or None,
        "printed_text": clean(card.get("printed_text")) or None,
        "flavor_name": clean(card.get("flavor_name")) or None,
        "flavor_text": clean(card.get("flavor_text")) or None,
        "scryfall_uri": clean(card.get("scryfall_uri")) or None,
        # Preserve future market-mapping evidence without pretending that these
        # source product IDs are exact finish identities today.
        "tcgplayer_id": card.get("tcgplayer_id"),
        "tcgplayer_etched_id": card.get("tcgplayer_etched_id"),
        "cardmarket_id": card.get("cardmarket_id"),
        "mtgo_id": card.get("mtgo_id"),
        "arena_id": card.get("arena_id"),
    }


def _image_rows(card: dict) -> list[dict]:
    candidates: list[tuple[str, str]] = []

    image_uris = card.get("image_uris")
    if isinstance(image_uris, dict):
        for key in ("normal", "large", "png", "small"):
            url = clean(image_uris.get(key))
            if url:
                candidates.append((url, "scryfall"))
                break

    if not candidates:
        for index, face in enumerate(_norm_list(card.get("card_faces")), start=1):
            if not isinstance(face, dict):
                continue
            face_uris = face.get("image_uris")
            if not isinstance(face_uris, dict):
                continue
            for key in ("normal", "large", "png", "small"):
                url = clean(face_uris.get(key))
                if url:
                    candidates.append((url, f"scryfall:face:{index}"))
                    break

    output: list[dict] = []
    seen: set[str] = set()
    for url, source in candidates:
        if url in seen:
            continue
        seen.add(url)
        output.append({"url": url, "source": source})
    return output


def _is_paper(card: dict) -> bool:
    games = card.get("games")
    if not isinstance(games, list):
        return True
    return "paper" in {clean(value).lower() for value in games}


def _iter_bulk_rows(connector: ScryfallMtgV2Connector, url: str) -> Iterable[dict]:
    headers = {
        "User-Agent": connector._SCRYFALL_HEADERS["User-Agent"],
        "Accept": "application/gzip,application/jsonl,application/x-ndjson,*/*;q=0.8",
    }
    with requests.get(url, headers=headers, stream=True, timeout=240) as response:
        response.raise_for_status()
        response.raw.decode_content = False
        is_gzip = url.lower().endswith(".gz") or "gzip" in clean(response.headers.get("Content-Type")).lower()
        if is_gzip:
            with gzip.GzipFile(fileobj=response.raw, mode="rb") as compressed:
                with io.TextIOWrapper(compressed, encoding="utf-8") as stream:
                    for raw in stream:
                        line = raw.strip()
                        if line:
                            value = json.loads(line)
                            if isinstance(value, dict):
                                yield value
        else:
            for raw in response.iter_lines(decode_unicode=True):
                line = str(raw or "").strip()
                if line:
                    value = json.loads(line)
                    if isinstance(value, dict):
                        yield value


class JsonlWriter:
    def __init__(self, path: Path):
        self.path = path
        self.handle = path.open("w", encoding="utf-8", newline="\n")
        self.count = 0

    def write(self, row: dict) -> None:
        self.handle.write(_json_dump(row) + "\n")
        self.count += 1

    def close(self) -> None:
        self.handle.close()


def _assert_lengths(card: dict, card_key: str, print_key: str, finish: str) -> None:
    checks = {
        "card.name": (clean(card.get("name")), 255),
        "card.card_key": (card_key, 255),
        "card.oracle_id": (clean(card.get("oracle_id")), 64),
        "print.collector_number": (clean(card.get("collector_number")), 50),
        "print.language": (clean(card.get("lang")), 16),
        "print.rarity": (clean(card.get("rarity")), 100),
        "print.variant": (finish, 100),
        "print.print_key": (print_key, 512),
        "print.scryfall_id": (clean(card.get("id")), 64),
        "set.code": (clean(card.get("set")), 50),
        "set.name": (clean(card.get("set_name")), 255),
    }
    for label, (value, limit) in checks.items():
        if len(value) > limit:
            raise AssertionError(f"{label} exceeds schema limit {limit}: {value!r}")


def run(*, output_dir: Path) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    connector = ScryfallMtgV2Connector()
    metadata = connector._bulk_metadata()
    download_url = connector._bulk_download_url(metadata)
    if not download_url:
        raise AssertionError("Scryfall default_cards bulk URL unavailable")

    generated_at = datetime.now(timezone.utc).isoformat()
    source_version = clean(metadata.get("updated_at")) or clean(metadata.get("id")) or generated_at

    writers = {
        name: JsonlWriter(output_dir / f"{name}.jsonl")
        for name in (
            "cards",
            "card_attributes",
            "prints",
            "print_attributes",
            "print_images",
            "print_identifiers",
        )
    }

    counts = Counter()
    sets: dict[str, dict] = {}
    set_release_dates: dict[str, set[str]] = defaultdict(set)
    cards: dict[str, dict] = {}
    card_payload_fingerprints: dict[str, str] = {}
    seen_scryfall_ids: set[str] = set()
    seen_print_keys: set[str] = set()
    seen_natural_prints: set[tuple[str, str, str, bool, str]] = set()
    finish_counts = Counter()

    try:
        for card in _iter_bulk_rows(connector, download_url):
            counts["bulk_objects_seen"] += 1
            if not _is_paper(card):
                continue
            counts["paper_source_objects"] += 1

            scryfall_id = clean(card.get("id")).lower()
            if not scryfall_id:
                raise AssertionError("Paper Scryfall object missing id")
            if scryfall_id in seen_scryfall_ids:
                raise AssertionError(f"Duplicate paper Scryfall id: {scryfall_id}")
            seen_scryfall_ids.add(scryfall_id)

            set_code = clean(card.get("set")).lower()
            set_name = clean(card.get("set_name"))
            if not set_code or not set_name:
                raise AssertionError(f"Paper Scryfall object missing set identity: {scryfall_id}")
            set_type = clean(card.get("set_type")) or None
            current_set = sets.get(set_code)
            if current_set is None:
                sets[set_code] = {"code": set_code, "name": set_name, "set_type": set_type}
            elif current_set["name"] != set_name or current_set["set_type"] != set_type:
                raise AssertionError(
                    f"Scryfall set metadata conflict for {set_code}: {current_set!r} vs {(set_name, set_type)!r}"
                )
            released_at = clean(card.get("released_at"))
            if released_at:
                set_release_dates[set_code].add(released_at)

            card_key = card_identity_key(card)
            oracle_id = clean(card.get("oracle_id")).lower() or None
            card_name = clean(card.get("name"))
            if not card_name:
                raise AssertionError(f"Paper Scryfall object missing Card name: {scryfall_id}")
            card_attrs = _card_attributes(card)
            fingerprint = hashlib.sha256(_json_dump(card_attrs).encode("utf-8")).hexdigest()

            existing = cards.get(card_key)
            if existing is None:
                cards[card_key] = {
                    "card_key": card_key,
                    "oracle_id": oracle_id,
                    "name": card_name,
                    "attributes": card_attrs,
                }
                card_payload_fingerprints[card_key] = fingerprint
            else:
                if existing["oracle_id"] != oracle_id or existing["name"] != card_name:
                    raise AssertionError(f"Logical Card metadata conflict for {card_key}")
                if card_payload_fingerprints[card_key] != fingerprint:
                    raise AssertionError(f"Logical Card rules/attribute conflict for {card_key}")

            if oracle_id is None:
                counts["paper_objects_without_oracle_id"] += 1

            finishes = finish_values(card)
            if len(finishes) > 1:
                counts["multi_finish_source_objects"] += 1
            images = _image_rows(card)
            if not images:
                counts["source_objects_without_image"] += 1

            for finish in finishes:
                print_key = physical_print_key(card, finish)
                _assert_lengths(card, card_key, print_key, finish)
                if print_key in seen_print_keys:
                    raise AssertionError(f"Exact MTG print_key collision: {print_key}")
                seen_print_keys.add(print_key)

                is_foil = finish != "nonfoil"
                natural = (
                    set_code,
                    clean(card.get("collector_number")),
                    clean(card.get("lang")).lower(),
                    is_foil,
                    finish,
                )
                if natural in seen_natural_prints:
                    raise AssertionError(f"Natural exact Print collision: {natural}")
                seen_natural_prints.add(natural)

                print_row = {
                    "print_key": print_key,
                    "card_key": card_key,
                    "set_code": set_code,
                    "collector_number": clean(card.get("collector_number")),
                    "language": clean(card.get("lang")).lower() or None,
                    "rarity": clean(card.get("rarity")).lower() or None,
                    "is_foil": is_foil,
                    "variant": finish,
                    "scryfall_id": scryfall_id,
                }
                writers["prints"].write(print_row)
                writers["print_attributes"].write(
                    {
                        "print_key": print_key,
                        "source": "scryfall",
                        "source_version": source_version,
                        "attributes": _print_attributes(card, finish),
                    }
                )
                writers["print_identifiers"].write(
                    {"print_key": print_key, "source": "scryfall", "external_id": scryfall_id}
                )
                for image_index, image in enumerate(images):
                    writers["print_images"].write(
                        {
                            "print_key": print_key,
                            "url": image["url"],
                            "is_primary": image_index == 0,
                            "source": image["source"],
                        }
                    )

                counts["exact_prints"] += 1
                finish_counts[finish] += 1

        for card_key in sorted(cards):
            row = cards[card_key]
            writers["cards"].write(
                {
                    "card_key": card_key,
                    "oracle_id": row["oracle_id"],
                    "name": row["name"],
                }
            )
            writers["card_attributes"].write(
                {
                    "card_key": card_key,
                    "source": "scryfall",
                    "source_version": source_version,
                    "attributes": row["attributes"],
                }
            )
    finally:
        for writer in writers.values():
            writer.close()

    sets_path = output_dir / "sets.jsonl"
    with sets_path.open("w", encoding="utf-8", newline="\n") as handle:
        for set_code in sorted(sets):
            row = sets[set_code]
            dates = sorted(set_release_dates.get(set_code) or [])
            # A Scryfall set container such as SLD can span many actual release
            # dates. Do not invent one canonical date: keep it null when source
            # card objects disagree and preserve each exact date on Print attrs.
            release_date = dates[0] if len(dates) == 1 else None
            handle.write(
                _json_dump(
                    {
                        "code": set_code,
                        "name": row["name"],
                        "release_date": release_date,
                        "set_type": row["set_type"],
                        "source_release_dates_count": len(dates),
                    }
                )
                + "\n"
            )
            if len(dates) > 1:
                counts["sets_with_multiple_source_release_dates"] += 1

    counts["sets"] = len(sets)
    counts["logical_cards"] = len(cards)
    counts["logical_cards_with_oracle_id"] = sum(1 for row in cards.values() if row["oracle_id"])
    counts["logical_cards_without_oracle_id"] = len(cards) - counts["logical_cards_with_oracle_id"]

    if counts["logical_cards"] <= 0 or counts["exact_prints"] <= 0 or counts["sets"] <= 0:
        raise AssertionError("MTG canonical snapshot is unexpectedly empty")
    if counts["exact_prints"] != sum(finish_counts.values()):
        raise AssertionError("Finish counts do not reconcile to exact Print count")
    if len(seen_scryfall_ids) != counts["paper_source_objects"]:
        raise AssertionError("Paper Scryfall object IDs do not reconcile")
    if len(seen_print_keys) != counts["exact_prints"]:
        raise AssertionError("Exact Print keys do not reconcile")
    if writers["cards"].count != counts["logical_cards"]:
        raise AssertionError("Card file row count mismatch")
    if writers["prints"].count != counts["exact_prints"]:
        raise AssertionError("Print file row count mismatch")
    if writers["card_attributes"].count != counts["logical_cards"]:
        raise AssertionError("Card attribute row count mismatch")
    if writers["print_attributes"].count != counts["exact_prints"]:
        raise AssertionError("Print attribute row count mismatch")
    if writers["print_identifiers"].count != counts["exact_prints"]:
        raise AssertionError("Print identifier row count mismatch")

    files = {}
    for path in sorted(output_dir.glob("*.jsonl")):
        files[path.name] = {
            "bytes": path.stat().st_size,
            "sha256": _sha256(path),
            "rows": sum(1 for _ in path.open("r", encoding="utf-8")),
        }

    manifest = {
        "status": "pass",
        "snapshot_schema_version": SNAPSHOT_SCHEMA_VERSION,
        "identity_policy_version": IDENTITY_POLICY_VERSION,
        "generated_at": generated_at,
        "source": {
            "name": "scryfall",
            "bulk_type": clean(metadata.get("type")) or "default_cards",
            "bulk_id": clean(metadata.get("id")) or None,
            "updated_at": clean(metadata.get("updated_at")) or None,
            "content_type": clean(metadata.get("content_type")) or None,
            "content_encoding": clean(metadata.get("content_encoding")) or None,
        },
        "counts": dict(sorted(counts.items())),
        "finish_counts": dict(sorted(finish_counts.items())),
        "files": files,
        "gates": {
            "duplicate_paper_scryfall_ids": 0,
            "exact_print_key_collisions": 0,
            "natural_exact_print_collisions": 0,
            "unknown_finishes": 0,
            "missing_scryfall_ids": 0,
            "raw_source_payload_persisted": False,
            "pricing_payload_persisted": False,
        },
    }
    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True))
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="Build canonical MTG V2 snapshot from one Scryfall bulk export")
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    run(output_dir=args.output_dir)


if __name__ == "__main__":
    main()
