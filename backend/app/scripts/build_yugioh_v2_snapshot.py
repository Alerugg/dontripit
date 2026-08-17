from __future__ import annotations

import argparse
import hashlib
import json
import re
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import requests

from app.ingest.connectors.ygoprodeck_yugioh import YgoProDeckYugiohConnector
from app.ingest.normalization import build_card_key, build_print_key, canonical_text_slug, normalize_collector_number


CARDSETS_URL = "https://db.ygoprodeck.com/api/v7/cardsets.php"
DB_VERSION_URL = "https://db.ygoprodeck.com/api/v7/checkDBVer.php"
PAGE_SIZE = 500
NOISY_RARITY_LABELS = {
    "2",
    "3",
    "European & Oceanian debut",
    "European debut",
    "New",
    "New artwork",
    "Oceanian debut",
    "Reprint",
    "force-SMW",
}
RARITY_ALIASES = {
    "PLatinum Secret Rare": "Platinum Secret Rare",
    "Starfoil": "Starfoil Rare",
    "Cr": "Collector's Rare",
    "Extra Secret": "Extra Secret Rare",
}


def _clean(value: object) -> str:
    return str(value or "").strip()


def _fold(value: object) -> str:
    text = unicodedata.normalize("NFKD", _clean(value))
    text = "".join(ch for ch in text if not unicodedata.combining(ch)).casefold()
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")


def _canonical_rarity(raw: object) -> str:
    value = re.sub(r"\s+", " ", _clean(raw)).strip()
    if value in NOISY_RARITY_LABELS:
        return "Unknown"
    return RARITY_ALIASES.get(value, value or "Unknown")


def _variant_for_rarity(rarity: str) -> str:
    slug = canonical_text_slug(rarity or "unknown") or "unknown"
    value = f"rarity-{slug}"
    if len(value) <= 100:
        return value
    digest = hashlib.sha1(value.encode("utf-8")).hexdigest()[:12]
    return f"rarity-{slug[:78]}-{digest}"


def _release_external_id(name: str) -> str:
    folded = _fold(name)
    digest = hashlib.sha1(folded.encode("utf-8")).hexdigest()[:24]
    return f"ygoprodeck-release:{digest}"


def _print_external_id(identity: tuple[str, str, str, str]) -> str:
    raw = "|".join(identity)
    return "ygo-v2:" + hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _parse_date(value: object) -> str | None:
    text = _clean(value)
    return text if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text) else None


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":"), default=str) + "\n")
    return path.stat().st_size


def _fetch_json(http: requests.Session, url: str):
    response = http.get(url, timeout=60)
    response.raise_for_status()
    return response.json()


def _family_for_print(*, full_code: str, release_name: str, release_code: str, explicit_families: dict[str, Counter[str]]) -> tuple[str, str]:
    code = full_code.upper().strip()
    if "-" in code:
        family = code.split("-", 1)[0].strip()
        if not family:
            raise AssertionError(f"Empty family parsed from {full_code!r}")
        return family, "direct_prefix"

    families = explicit_families.get(release_name, Counter())
    if len(families) == 1:
        family, count = families.most_common(1)[0]
        if count > 0 and family == release_code:
            return family, "same_release_unanimous_fallback"
    raise AssertionError(
        f"No trusted Set-family rule for no-hyphen code={full_code!r} release={release_name!r} "
        f"release_code={release_code!r} explicit_families={dict(families)}"
    )


def run(*, output_dir: Path) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    connector = YgoProDeckYugiohConnector()
    cards = connector._load_remote(limit=None, page_size=PAGE_SIZE)

    http = requests.Session()
    http.headers.update({"User-Agent": "dontripit-catalog-v2-ygo-snapshot/1.0"})
    releases = _fetch_json(http, CARDSETS_URL)
    db_version_payload = _fetch_json(http, DB_VERSION_URL)
    source_version = _clean((db_version_payload or [{}])[0].get("database_version")) if isinstance(db_version_payload, list) else ""

    release_by_name: dict[str, dict] = {}
    release_by_fold: dict[str, list[dict]] = defaultdict(list)
    for row in releases:
        name = _clean(row.get("set_name"))
        if not name:
            raise AssertionError("Official release row missing set_name")
        if name in release_by_name:
            raise AssertionError(f"Duplicate exact official release name: {name}")
        release_by_name[name] = row
        release_by_fold[_fold(name)].append(row)
    folded_collisions = {key: rows for key, rows in release_by_fold.items() if len(rows) > 1}
    if folded_collisions:
        raise AssertionError(f"Folded official release-name collisions: {len(folded_collisions)}")

    # First pass: explicit family evidence per commercial release.
    explicit_families: dict[str, Counter[str]] = defaultdict(Counter)
    for card in cards:
        for printing in card.get("card_sets") or []:
            release_name = _clean(printing.get("set_name"))
            release = release_by_name.get(release_name)
            if release is None:
                folded = release_by_fold.get(_fold(release_name)) or []
                release = folded[0] if len(folded) == 1 else None
            if release is None:
                raise AssertionError(f"card_sets row cannot map to official release: {release_name!r}")
            official_name = _clean(release.get("set_name"))
            full_code = _clean(printing.get("set_code")).upper()
            if "-" in full_code:
                family = full_code.split("-", 1)[0].strip()
                if family:
                    explicit_families[official_name][family] += 1

    card_rows: list[dict] = []
    card_attr_rows: list[dict] = []
    artwork_rows: list[dict] = []
    print_map: dict[tuple[str, str, str, str], dict] = {}
    print_attr_map: dict[tuple[str, str, str, str], dict] = {}
    image_map: dict[tuple[str, str, str, str], dict] = {}
    print_release_pairs: dict[tuple[tuple[str, str, str, str], str], dict] = {}
    family_release_counts: dict[str, Counter[str]] = defaultdict(Counter)
    family_dates: dict[str, list[str]] = defaultdict(list)
    fallback_rows = 0
    no_print_cards = 0
    noisy_rarity_rows = 0
    duplicate_source_print_rows = 0

    seen_card_ids: set[str] = set()
    seen_card_keys: set[str] = set()

    for card in cards:
        card_id = _clean(card.get("id"))
        card_name = _clean(card.get("name"))
        if not card_id or not card_name:
            raise AssertionError("Source Card missing id/name")
        if card_id in seen_card_ids:
            raise AssertionError(f"Duplicate Card id: {card_id}")
        seen_card_ids.add(card_id)

        external_ids = [{"source": "ygoprodeck", "id_type": "card_id", "value": card_id}]
        card_key = build_card_key(
            game_slug="yugioh",
            canonical_name=card_name,
            identity_hints={},
            external_ids=external_ids,
        )
        if not card_key or card_key in seen_card_keys:
            raise AssertionError(f"Card-key collision: {card_key!r} card={card_id}")
        seen_card_keys.add(card_key)
        if len(card_name) > 255 or len(card_key) > 255 or len(card_id) > 64:
            raise AssertionError(f"Card row exceeds schema length: {card_id} {card_name[:80]}")

        card_rows.append({
            "source_card_id": card_id,
            "name": card_name,
            "card_key": card_key,
            "yugoprodeck_id": card_id,
        })

        images = card.get("card_images") or []
        art_candidates = []
        for index, image in enumerate(images):
            image_id = _clean(image.get("id"))
            candidate = {
                "source_card_id": card_id,
                "image_id": image_id or None,
                "ordinal": index,
                "image_url": _clean(image.get("image_url")) or None,
                "image_url_small": _clean(image.get("image_url_small")) or None,
                "image_url_cropped": _clean(image.get("image_url_cropped")) or None,
                "mapping_status": "card_level_candidate_unresolved",
            }
            artwork_rows.append(candidate)
            art_candidates.append(candidate)

        attrs = {
            "category": _clean(card.get("humanReadableCardType")) or _clean(card.get("type")) or None,
            "type": _clean(card.get("type")) or None,
            "frame_type": _clean(card.get("frameType")) or None,
            "description": _clean(card.get("desc")) or None,
            "race": _clean(card.get("race")) or None,
            "archetype": _clean(card.get("archetype")) or None,
            "attribute": _clean(card.get("attribute")) or None,
            "level": card.get("level"),
            "rank": card.get("level") if "XYZ" in _clean(card.get("type")).upper() else None,
            "scale": card.get("scale"),
            "atk": card.get("atk"),
            "def": card.get("def"),
            "link_value": card.get("linkval"),
            "link_markers": card.get("linkmarkers") or [],
            "typeline": card.get("typeline") or [],
            "banlist_info": card.get("banlist_info") or {},
            "misc_info": card.get("misc_info") or [],
            "artwork_candidates": art_candidates,
            "artwork_mapping_status": "card_level_only_unresolved",
            "source_card_id": card_id,
        }
        card_attr_rows.append({
            "source_card_id": card_id,
            "source": "ygoprodeck",
            "source_version": source_version or None,
            "attributes": attrs,
        })

        card_printings = card.get("card_sets") or []
        if not card_printings:
            no_print_cards += 1
            continue

        primary_image = next((row for row in art_candidates if row.get("image_url")), None)

        for printing in card_printings:
            source_release_name = _clean(printing.get("set_name"))
            release = release_by_name.get(source_release_name)
            if release is None:
                folded = release_by_fold.get(_fold(source_release_name)) or []
                release = folded[0] if len(folded) == 1 else None
            if release is None:
                raise AssertionError(f"Unmatched release during snapshot: {source_release_name!r}")

            release_name = _clean(release.get("set_name"))
            release_code = _clean(release.get("set_code")).upper()
            release_date = _parse_date(release.get("tcg_date"))
            full_code = _clean(printing.get("set_code")).upper()
            if not full_code:
                raise AssertionError(f"Print missing full collector code card={card_id} release={release_name}")
            if len(full_code) > 50:
                raise AssertionError(f"Collector number too long: {full_code}")

            family, family_resolution = _family_for_print(
                full_code=full_code,
                release_name=release_name,
                release_code=release_code,
                explicit_families=explicit_families,
            )
            if family_resolution != "direct_prefix":
                fallback_rows += 1
            if len(family) > 50:
                raise AssertionError(f"Set family too long: {family}")

            raw_rarity = _clean(printing.get("set_rarity"))
            canonical_rarity = _canonical_rarity(raw_rarity)
            rarity_code = _clean(printing.get("set_rarity_code") or printing.get("set_rarity_short")).upper()
            if raw_rarity in NOISY_RARITY_LABELS:
                noisy_rarity_rows += 1
            identity = (card_id, full_code, rarity_code, canonical_rarity.casefold())
            ygo_print_id = _print_external_id(identity)
            variant = _variant_for_rarity(canonical_rarity)
            collector_norm = normalize_collector_number(full_code)
            print_key = build_print_key(
                card_key=card_key,
                set_code=family,
                collector_number=collector_norm,
                language="en",
                finish="nonfoil",
                variant=variant,
            )
            if len(ygo_print_id) > 64 or len(variant) > 100 or len(print_key) > 512:
                raise AssertionError(f"Print row exceeds schema length: {identity}")

            row = {
                "source_print_id": ygo_print_id,
                "source_card_id": card_id,
                "set_family": family,
                "collector_number": full_code,
                "language": "en",
                "rarity": canonical_rarity,
                "is_foil": False,
                "variant": variant,
                "print_key": print_key,
                "yugioh_id": ygo_print_id,
            }
            prior = print_map.get(identity)
            if prior is not None:
                if prior != row:
                    raise AssertionError(f"Physical Print identity conflict: {identity}")
                duplicate_source_print_rows += 1
            else:
                print_map[identity] = row
                print_attr_map[identity] = {
                    "source_print_id": ygo_print_id,
                    "source": "ygoprodeck",
                    "source_version": source_version or None,
                    "attributes": {
                        "full_collector_code_raw": full_code,
                        "collector_family": family,
                        "family_resolution": family_resolution,
                        "rarity_raw": raw_rarity or None,
                        "rarity_code_raw": rarity_code or None,
                        "rarity_canonical": canonical_rarity,
                        "source_set_price": _clean(printing.get("set_price")) or None,
                        "finish_evidence": "not_provided_by_source",
                        "artwork_mapping_status": "representative_card_image_only_unresolved",
                    },
                }
                if primary_image:
                    image_map[identity] = {
                        "source_print_id": ygo_print_id,
                        "url": primary_image["image_url"],
                        "is_primary": True,
                        "source": "ygoprodeck-card-representative-unresolved",
                    }

            release_external_id = _release_external_id(release_name)
            pair_key = (identity, release_external_id)
            pair = {
                "source_print_id": ygo_print_id,
                "release_external_id": release_external_id,
                "source_print_reference": full_code,
                "appearance_type": "card_sets",
                "metadata": {
                    "release_name": release_name,
                    "release_code": release_code or None,
                    "release_date": release_date,
                    "source_set_code": full_code,
                },
            }
            prior_pair = print_release_pairs.get(pair_key)
            if prior_pair is not None and prior_pair != pair:
                raise AssertionError(f"PrintRelease conflict: {pair_key}")
            print_release_pairs[pair_key] = pair

            family_release_counts[family][release_name] += 1
            if release_date:
                family_dates[family].append(release_date)

    set_rows: list[dict] = []
    for family in sorted(family_release_counts):
        release_counts = family_release_counts[family]
        candidates = []
        for release_name, count in release_counts.items():
            release = release_by_name[release_name]
            same_code = int(_clean(release.get("set_code")).upper() == family)
            date = _parse_date(release.get("tcg_date")) or "9999-12-31"
            candidates.append((-same_code, -count, date, release_name))
        candidates.sort()
        display_name = candidates[0][3] if candidates else family
        release_date = min(family_dates.get(family) or ["9999-12-31"])
        if release_date == "9999-12-31":
            release_date = None
        set_rows.append({
            "code": family,
            "name": display_name,
            "yugioh_id": f"family:{family}",
            "release_date": release_date,
            "release_names": sorted(release_counts),
        })

    release_rows = []
    for release in sorted(releases, key=lambda row: (_clean(row.get("tcg_date")) or "9999-12-31", _clean(row.get("set_name")))):
        name = _clean(release.get("set_name"))
        release_rows.append({
            "source": "ygoprodeck",
            "external_id": _release_external_id(name),
            "name": name,
            "code": _clean(release.get("set_code")).upper() or None,
            "release_type": None,
            "release_date": _parse_date(release.get("tcg_date")),
            "language": "en",
            "region": "TCG",
            "metadata": {
                "num_of_cards": release.get("num_of_cards"),
                "source_set_code": _clean(release.get("set_code")).upper() or None,
            },
        })

    print_rows = list(print_map.values())
    print_attr_rows = list(print_attr_map.values())
    image_rows = list(image_map.values())
    print_release_rows = list(print_release_pairs.values())

    # Simulate the important current PostgreSQL uniqueness constraints using stable snapshot keys.
    def assert_unique(label: str, values):
        seen = set()
        dup = []
        for value in values:
            if value in seen:
                dup.append(value)
            seen.add(value)
        if dup:
            raise AssertionError(f"{label} uniqueness failed: {len(dup)} duplicates; sample={dup[:5]}")

    assert_unique("Set.code", (row["code"] for row in set_rows))
    assert_unique("Set.yugioh_id", (row["yugioh_id"] for row in set_rows))
    assert_unique("Card.yugoprodeck_id", (row["yugoprodeck_id"] for row in card_rows))
    assert_unique("Card.card_key", (row["card_key"] for row in card_rows))
    assert_unique("Print.yugioh_id", (row["yugioh_id"] for row in print_rows))
    assert_unique("Print.print_key", (row["print_key"] for row in print_rows))
    assert_unique(
        "Print shared constraint tuple",
        ((row["set_family"], row["collector_number"], row["language"], row["is_foil"], row["variant"]) for row in print_rows),
    )
    assert_unique("CatalogRelease external identity", ((row["source"], row["external_id"]) for row in release_rows))
    assert_unique("PrintRelease identity", ((row["source_print_id"], row["release_external_id"]) for row in print_release_rows))

    expected = {
        "cards": 14480,
        "prints": 44285,
        "releases": 1032,
        "cards_without_print_evidence": 490,
        "noisy_rarity_rows": 206,
        "no_hyphen_family_fallback_rows": 12,
    }
    actual = {
        "sets": len(set_rows),
        "cards": len(card_rows),
        "prints": len(print_rows),
        "releases": len(release_rows),
        "print_releases": len(print_release_rows),
        "card_attributes": len(card_attr_rows),
        "print_attributes": len(print_attr_rows),
        "representative_print_images": len(image_rows),
        "artwork_candidates": len(artwork_rows),
        "cards_without_print_evidence": no_print_cards,
        "noisy_rarity_rows": noisy_rarity_rows,
        "no_hyphen_family_fallback_rows": fallback_rows,
        "duplicate_source_print_rows_deduped": duplicate_source_print_rows,
    }
    for key, value in expected.items():
        if actual.get(key) != value:
            raise AssertionError(f"Snapshot count moved: {key}={actual.get(key)} expected={value}")

    files = {
        "sets.jsonl": set_rows,
        "cards.jsonl": card_rows,
        "card_attributes.jsonl": card_attr_rows,
        "artwork_candidates.jsonl": artwork_rows,
        "prints.jsonl": print_rows,
        "print_attributes.jsonl": print_attr_rows,
        "representative_print_images.jsonl": image_rows,
        "catalog_releases.jsonl": release_rows,
        "print_releases.jsonl": print_release_rows,
    }
    bytes_by_file = {name: _write_jsonl(output_dir / name, rows) for name, rows in files.items()}

    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "pass",
        "mode": "deterministic_yugioh_v2_snapshot_no_database_writes",
        "source": {
            "database_version": db_version_payload,
            "source_cards": len(cards),
            "official_release_rows": len(releases),
        },
        "counts": actual,
        "expected_fixed_gates": expected,
        "snapshot_bytes_uncompressed": sum(bytes_by_file.values()),
        "bytes_by_file": bytes_by_file,
        "identity_policy": {
            "card": "YGOPRODeck card id",
            "set": "explicit collector-code prefix; 12 DB1 no-hyphen rows use unanimous same-release fallback while raw collector code is unchanged",
            "print": "card id + full collector code + rarity code + canonical rarity + en source surface",
            "release": "unique official cardsets.php release name",
            "artwork": "Card-level candidates; Print image is representative/unresolved, never claimed exact art",
        },
        "constraint_simulation": "pass",
        "database_writes": 0,
    }
    _write_json(output_dir / "manifest.json", manifest)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    run(output_dir=args.output_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
