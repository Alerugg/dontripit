from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import requests

from app.ingest.connectors.ygoprodeck_yugioh import YgoProDeckYugiohConnector
from app.ingest.normalization import build_card_key, build_print_key, normalize_collector_number
from app.scripts.build_yugioh_v2_snapshot import (
    CARDSETS_URL,
    DB_VERSION_URL,
    NOISY_RARITY_LABELS,
    PAGE_SIZE,
    _canonical_rarity,
    _clean,
    _family_for_print,
    _fold,
    _parse_date,
    _release_external_id,
    _variant_for_rarity,
)


# One high-confidence source alias: both source rows are Skill Cards with the same
# text, same physical code SBCB-ENS08 and same rarity. Keep the stable/base name.
CARD_ALIAS_TO_CANONICAL = {
    "300302053": "300302018",  # Spell of Mask (Skill Card) -> Spell of Mask
}

# Proven bad card-to-code assignments. Never rewrite these source collector codes.
# Quarantine the raw rows and keep the independently correct source row instead.
SOURCE_PRINT_EXCLUSIONS: dict[tuple[str, str], dict] = {
    ("72843899", "BLCR-EN012"): {
        "reason": "source_card_code_assignment_conflict",
        "corroborated_code": "BLCR-EN013",
        "evidence": "Konami Neuron assigns BLCR-EN012 to Emerald Tortoise and BLCR-EN013 to Topaz Tiger",
    },
    ("46358784", "BLCR-EN013"): {
        "reason": "source_card_code_assignment_conflict",
        "corroborated_code": "BLCR-EN012",
        "evidence": "Konami Neuron assigns BLCR-EN012 to Emerald Tortoise and BLCR-EN013 to Topaz Tiger",
    },
    ("71620241", "BLCR-EN015"): {
        "reason": "source_card_code_assignment_conflict",
        "corroborated_code": "BLCR-EN016",
        "evidence": "Konami Neuron assigns BLCR-EN015 to Cobalt Eagle and BLCR-EN016 to Sapphire Pegasus",
    },
    ("45236142", "BLCR-EN016"): {
        "reason": "source_card_code_assignment_conflict",
        "corroborated_code": "BLCR-EN015",
        "evidence": "Konami Neuron assigns BLCR-EN015 to Cobalt Eagle and BLCR-EN016 to Sapphire Pegasus",
    },
    ("88120966", "LDS3-EN063"): {
        "reason": "source_card_code_assignment_conflict",
        "corroborated_code": None,
        "evidence": "Konami Neuron assigns LDS3-EN063 to Gimmick Puppet Bisque Doll; Number 15 has no LDS3 listing",
    },
    ("94820406", "SGX3-ENA11"): {
        "reason": "source_card_code_assignment_conflict",
        "corroborated_code": "SGX3-ENA13",
        "evidence": "Konami Neuron assigns SGX3-ENA11 to Dark Ruler Ha Des and SGX3-ENA13 to Dark Fusion",
    },
    ("24508238", "SGX3-ENE10"): {
        "reason": "source_card_code_assignment_conflict",
        "corroborated_code": "SGX3-ENF10",
        "evidence": "Konami Neuron assigns SGX3-ENE10 to Mist Archfiend; D.D. Crow is corroborated as SGX3-ENF10",
    },
    ("78060096", "SGX3-ENI25"): {
        "reason": "source_card_code_assignment_conflict",
        "corroborated_code": "SGX3-ENI26",
        "evidence": "Konami Neuron assigns SGX3-ENI25 to Shining Flare Wingman and SGX3-ENI26 to Terrorking Salmon",
    },
}

# YGOPRODeck is a live source. These historical certified counts are lower bounds,
# not immutable snapshots: official catalog growth must pass while an accidental
# truncated response must still fail closed.
SOURCE_MINIMUMS = {
    "source_cards": 14480,
    "releases": 1032,
}

# These counts describe reviewed static source-policy evidence and therefore remain
# exact. Eight configured quarantine keys currently match nine raw rows because
# YGOPRODeck publishes 94820406 / SGX3-ENA11 twice (Common and Secret Rare).
# Any further alias, quarantine-key or raw-row multiplicity change must be reviewed.
EXPECTED_STATIC = {
    "source_card_aliases_merged": len(CARD_ALIAS_TO_CANONICAL),
    "excluded_source_print_rows": 9,
}


def _assert_minimum(label: str, actual: int, minimum: int) -> None:
    if actual < minimum:
        raise AssertionError(
            f"Snapshot source minimum failed: {label}={actual} minimum={minimum}"
        )


def _assert_snapshot_gates(counts: dict[str, int]) -> None:
    for key, minimum in SOURCE_MINIMUMS.items():
        _assert_minimum(key, int(counts[key]), minimum)
    for key, expected in EXPECTED_STATIC.items():
        actual = int(counts[key])
        if actual != expected:
            raise AssertionError(
                f"Snapshot static gate moved: {key}={actual} expected={expected}"
            )


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )


def _write_jsonl(path: Path, rows: list[dict]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(
                json.dumps(row, ensure_ascii=False, separators=(",", ":"), default=str)
                + "\n"
            )
    return path.stat().st_size


def _fetch_json(http: requests.Session, url: str):
    response = http.get(url, timeout=60)
    response.raise_for_status()
    return response.json()


def _canonical_card_id(source_card_id: object) -> str:
    source_id = _clean(source_card_id)
    return CARD_ALIAS_TO_CANONICAL.get(source_id, source_id)


def _print_external_id(identity: tuple[str, str, str]) -> str:
    raw = "|".join(identity)
    return "ygo-v2:" + hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _source_row_score(row: dict) -> tuple[int, int, int, str]:
    raw = _clean(row.get("rarity_raw"))
    canonical = _clean(row.get("rarity_canonical"))
    rarity_code = _clean(row.get("rarity_code_raw"))
    price = _clean(row.get("source_set_price"))
    try:
        nonzero_price = int(float(price) > 0) if price else 0
    except (TypeError, ValueError):
        nonzero_price = 0
    return (
        int(raw.casefold() == canonical.casefold()),
        int(bool(rarity_code)),
        nonzero_price,
        raw,
    )


def _assert_spell_of_mask_alias(source_cards_by_id: dict[str, dict]) -> None:
    canonical = source_cards_by_id["300302018"]
    alias = source_cards_by_id["300302053"]
    if _clean(canonical.get("type")) != "Skill Card" or _clean(alias.get("type")) != "Skill Card":
        raise AssertionError("Spell of Mask alias evidence changed: both rows must remain Skill Cards")
    if _clean(canonical.get("desc")) != _clean(alias.get("desc")):
        raise AssertionError("Spell of Mask alias evidence changed: card text diverged")
    canonical_codes = {_clean(row.get("set_code")).upper() for row in canonical.get("card_sets") or []}
    alias_codes = {_clean(row.get("set_code")).upper() for row in alias.get("card_sets") or []}
    if "SBCB-ENS08" not in canonical_codes or "SBCB-ENS08" not in alias_codes:
        raise AssertionError("Spell of Mask alias evidence changed: SBCB-ENS08 no longer shared")


def _resolve_release(
    source_release_name: str,
    release_by_name: dict[str, dict],
    release_by_fold: dict[str, list[dict]],
) -> dict:
    release = release_by_name.get(source_release_name)
    if release is not None:
        return release
    candidates = release_by_fold.get(_fold(source_release_name)) or []
    if len(candidates) == 1:
        return candidates[0]
    raise AssertionError(f"card_sets row cannot map to official release: {source_release_name!r}")


def _assert_unique(label: str, values) -> None:
    seen = set()
    duplicates = []
    for value in values:
        if value in seen:
            duplicates.append(value)
        seen.add(value)
    if duplicates:
        raise AssertionError(
            f"{label} uniqueness failed: {len(duplicates)} duplicates; sample={duplicates[:5]}"
        )


def run(*, output_dir: Path) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)

    source_cards = YgoProDeckYugiohConnector()._load_remote(limit=None, page_size=PAGE_SIZE)
    http = requests.Session()
    http.headers.update({"User-Agent": "dontripit-catalog-v2-ygo-snapshot/2.0"})
    releases = _fetch_json(http, CARDSETS_URL)
    db_version_payload = _fetch_json(http, DB_VERSION_URL)
    source_version = (
        _clean((db_version_payload or [{}])[0].get("database_version"))
        if isinstance(db_version_payload, list)
        else ""
    )

    _assert_minimum("source_cards", len(source_cards), SOURCE_MINIMUMS["source_cards"])
    _assert_minimum("releases", len(releases), SOURCE_MINIMUMS["releases"])

    source_cards_by_id = {_clean(card.get("id")): card for card in source_cards}
    if len(source_cards_by_id) != len(source_cards):
        raise AssertionError("Source Card IDs are not unique")
    for alias_id, canonical_id in CARD_ALIAS_TO_CANONICAL.items():
        if alias_id not in source_cards_by_id or canonical_id not in source_cards_by_id:
            raise AssertionError(f"Configured Card alias missing: {alias_id}->{canonical_id}")
    _assert_spell_of_mask_alias(source_cards_by_id)

    release_by_name: dict[str, dict] = {}
    release_by_fold: dict[str, list[dict]] = defaultdict(list)
    for release in releases:
        name = _clean(release.get("set_name"))
        if not name:
            raise AssertionError("Official release row missing set_name")
        if name in release_by_name:
            raise AssertionError(f"Duplicate exact official release name: {name}")
        release_by_name[name] = release
        release_by_fold[_fold(name)].append(release)
    folded_collisions = {key: rows for key, rows in release_by_fold.items() if len(rows) > 1}
    if folded_collisions:
        raise AssertionError(f"Folded official release-name collisions: {len(folded_collisions)}")

    canonical_groups: dict[str, list[dict]] = defaultdict(list)
    for source_card in source_cards:
        canonical_groups[_canonical_card_id(source_card.get("id"))].append(source_card)

    # Family fallback evidence is built only from accepted rows.
    explicit_families: dict[str, Counter[str]] = defaultdict(Counter)
    exclusion_hits = Counter()
    for source_card in source_cards:
        source_card_id = _clean(source_card.get("id"))
        for printing in source_card.get("card_sets") or []:
            full_code = _clean(printing.get("set_code")).upper()
            exclusion = SOURCE_PRINT_EXCLUSIONS.get((source_card_id, full_code))
            if exclusion is not None:
                exclusion_hits[(source_card_id, full_code)] += 1
                continue
            release = _resolve_release(
                _clean(printing.get("set_name")), release_by_name, release_by_fold
            )
            release_name = _clean(release.get("set_name"))
            if "-" in full_code:
                family = full_code.split("-", 1)[0].strip()
                if family:
                    explicit_families[release_name][family] += 1

    missing_exclusion_keys = sorted(set(SOURCE_PRINT_EXCLUSIONS) - set(exclusion_hits))
    if missing_exclusion_keys:
        raise AssertionError(f"Configured source conflict rows disappeared: {missing_exclusion_keys}")
    if sum(exclusion_hits.values()) != EXPECTED_STATIC["excluded_source_print_rows"]:
        raise AssertionError(
            f"Excluded raw source row count moved: {sum(exclusion_hits.values())}"
        )

    card_rows: list[dict] = []
    card_attr_rows: list[dict] = []
    artwork_rows: list[dict] = []
    source_conflict_rows: list[dict] = []

    # Exact physical identity intentionally excludes raw source rarity-code aliases.
    # Same canonical Card + full collector code + same canonical human rarity = one Print.
    print_map: dict[tuple[str, str, str], dict] = {}
    print_attr_map: dict[tuple[str, str, str], dict] = {}
    image_map: dict[tuple[str, str, str], dict] = {}
    print_release_pairs: dict[tuple[tuple[str, str, str], str], dict] = {}

    family_release_counts: dict[str, Counter[str]] = defaultdict(Counter)
    family_dates: dict[str, list[str]] = defaultdict(list)
    no_hyphen_fallback_rows = 0
    cards_without_print_evidence = 0
    noisy_rarity_rows = 0
    deduplicated_source_print_rows = 0
    seen_card_keys: set[str] = set()

    for canonical_card_id in sorted(canonical_groups, key=int):
        group = canonical_groups[canonical_card_id]
        primary = next(
            (row for row in group if _clean(row.get("id")) == canonical_card_id), None
        )
        if primary is None:
            raise AssertionError(f"Canonical source Card missing: {canonical_card_id}")

        card_name = _clean(primary.get("name"))
        source_alias_ids = sorted(
            _clean(row.get("id"))
            for row in group
            if _clean(row.get("id")) != canonical_card_id
        )
        source_names = sorted({_clean(row.get("name")) for row in group})
        external_ids = [
            {"source": "ygoprodeck", "id_type": "card_id", "value": canonical_card_id}
        ]
        card_key = build_card_key(
            game_slug="yugioh",
            canonical_name=card_name,
            identity_hints={},
            external_ids=external_ids,
        )
        if not card_key or card_key in seen_card_keys:
            raise AssertionError(f"Card-key collision: {card_key!r}")
        seen_card_keys.add(card_key)
        if len(card_name) > 255 or len(card_key) > 255 or len(canonical_card_id) > 64:
            raise AssertionError(f"Card row exceeds schema length: {canonical_card_id}")

        card_rows.append(
            {
                "source_card_id": canonical_card_id,
                "name": card_name,
                "card_key": card_key,
                "yugoprodeck_id": canonical_card_id,
            }
        )

        art_candidates: list[dict] = []
        artwork_ordinal = 0
        seen_artwork = set()
        for source_card in sorted(group, key=lambda row: _clean(row.get("id"))):
            origin_id = _clean(source_card.get("id"))
            for image in source_card.get("card_images") or []:
                image_id = _clean(image.get("id"))
                dedupe_key = (origin_id, image_id, _clean(image.get("image_url")))
                if dedupe_key in seen_artwork:
                    continue
                seen_artwork.add(dedupe_key)
                candidate = {
                    "source_card_id": canonical_card_id,
                    "origin_source_card_id": origin_id,
                    "image_id": image_id or None,
                    "ordinal": artwork_ordinal,
                    "image_url": _clean(image.get("image_url")) or None,
                    "image_url_small": _clean(image.get("image_url_small")) or None,
                    "image_url_cropped": _clean(image.get("image_url_cropped")) or None,
                    "mapping_status": "card_level_candidate_unresolved",
                }
                artwork_ordinal += 1
                artwork_rows.append(candidate)
                art_candidates.append(candidate)

        card_attr_rows.append(
            {
                "source_card_id": canonical_card_id,
                "source": "ygoprodeck",
                "source_version": source_version or None,
                "attributes": {
                    "category": _clean(primary.get("humanReadableCardType"))
                    or _clean(primary.get("type"))
                    or None,
                    "type": _clean(primary.get("type")) or None,
                    "frame_type": _clean(primary.get("frameType")) or None,
                    "description": _clean(primary.get("desc")) or None,
                    "race": _clean(primary.get("race")) or None,
                    "archetype": _clean(primary.get("archetype")) or None,
                    "attribute": _clean(primary.get("attribute")) or None,
                    "level": primary.get("level"),
                    "rank": primary.get("level")
                    if "XYZ" in _clean(primary.get("type")).upper()
                    else None,
                    "scale": primary.get("scale"),
                    "atk": primary.get("atk"),
                    "def": primary.get("def"),
                    "link_value": primary.get("linkval"),
                    "link_markers": primary.get("linkmarkers") or [],
                    "typeline": primary.get("typeline") or [],
                    "banlist_info": primary.get("banlist_info") or {},
                    "misc_info": primary.get("misc_info") or [],
                    "artwork_candidates": art_candidates,
                    "artwork_mapping_status": "card_level_only_unresolved",
                    "source_card_id": canonical_card_id,
                    "source_alias_ids": source_alias_ids,
                    "source_names": source_names,
                    "card_identity_resolution": (
                        "source_alias_merge" if source_alias_ids else "direct_source_id"
                    ),
                },
            }
        )

        representative_image = next(
            (
                row
                for row in art_candidates
                if row["origin_source_card_id"] == canonical_card_id and row.get("image_url")
            ),
            None,
        ) or next((row for row in art_candidates if row.get("image_url")), None)

        accepted_print_for_card = False
        for source_card in group:
            source_card_id = _clean(source_card.get("id"))
            source_card_name = _clean(source_card.get("name"))
            for printing in source_card.get("card_sets") or []:
                full_code = _clean(printing.get("set_code")).upper()
                source_release_name = _clean(printing.get("set_name"))
                exclusion = SOURCE_PRINT_EXCLUSIONS.get((source_card_id, full_code))
                if exclusion is not None:
                    source_conflict_rows.append(
                        {
                            "source_card_id": source_card_id,
                            "canonical_card_id": canonical_card_id,
                            "card_name": source_card_name,
                            "release_name": source_release_name,
                            "full_collector_code_raw": full_code,
                            "rarity_raw": _clean(printing.get("set_rarity")) or None,
                            "rarity_code_raw": _clean(
                                printing.get("set_rarity_code")
                                or printing.get("set_rarity_short")
                            ).upper()
                            or None,
                            "source_set_price": _clean(printing.get("set_price")) or None,
                            **exclusion,
                        }
                    )
                    continue

                release = _resolve_release(
                    source_release_name, release_by_name, release_by_fold
                )
                release_name = _clean(release.get("set_name"))
                release_code = _clean(release.get("set_code")).upper()
                release_date = _parse_date(release.get("tcg_date"))
                if not full_code or len(full_code) > 50:
                    raise AssertionError(
                        f"Invalid collector code card={source_card_id} code={full_code!r}"
                    )

                family, family_resolution = _family_for_print(
                    full_code=full_code,
                    release_name=release_name,
                    release_code=release_code,
                    explicit_families=explicit_families,
                )
                if family_resolution != "direct_prefix":
                    no_hyphen_fallback_rows += 1
                if len(family) > 50:
                    raise AssertionError(f"Set family too long: {family}")

                raw_rarity = _clean(printing.get("set_rarity"))
                rarity_code = _clean(
                    printing.get("set_rarity_code") or printing.get("set_rarity_short")
                ).upper()
                canonical_rarity = _canonical_rarity(raw_rarity)
                if raw_rarity in NOISY_RARITY_LABELS:
                    noisy_rarity_rows += 1

                identity = (
                    canonical_card_id,
                    full_code,
                    canonical_rarity.casefold(),
                )
                source_print_id = _print_external_id(identity)
                variant = _variant_for_rarity(canonical_rarity)
                print_key = build_print_key(
                    card_key=card_key,
                    set_code=family,
                    collector_number=normalize_collector_number(full_code),
                    language="en",
                    finish="nonfoil",
                    variant=variant,
                )
                if len(source_print_id) > 64 or len(variant) > 100 or len(print_key) > 512:
                    raise AssertionError(f"Print row exceeds schema length: {identity}")

                print_row = {
                    "source_print_id": source_print_id,
                    "source_card_id": canonical_card_id,
                    "set_family": family,
                    "collector_number": full_code,
                    "language": "en",
                    "rarity": canonical_rarity,
                    "is_foil": False,
                    "variant": variant,
                    "print_key": print_key,
                    "yugioh_id": source_print_id,
                }
                source_evidence = {
                    "source_card_id": source_card_id,
                    "source_card_name": source_card_name,
                    "release_name": release_name,
                    "release_code": release_code or None,
                    "release_date": release_date,
                    "full_collector_code_raw": full_code,
                    "collector_family": family,
                    "family_resolution": family_resolution,
                    "rarity_raw": raw_rarity or None,
                    "rarity_code_raw": rarity_code or None,
                    "rarity_canonical": canonical_rarity,
                    "source_set_price": _clean(printing.get("set_price")) or None,
                }

                prior = print_map.get(identity)
                if prior is None:
                    print_map[identity] = print_row
                    print_attr_map[identity] = {
                        "source_print_id": source_print_id,
                        "source": "ygoprodeck",
                        "source_version": source_version or None,
                        "attributes": {
                            "full_collector_code_raw": full_code,
                            "collector_family": family,
                            "family_resolution": family_resolution,
                            "rarity_raw": source_evidence["rarity_raw"],
                            "rarity_code_raw": source_evidence["rarity_code_raw"],
                            "rarity_canonical": canonical_rarity,
                            "source_set_price": source_evidence["source_set_price"],
                            "source_rows": [source_evidence],
                            "finish_evidence": "not_provided_by_source",
                            "artwork_mapping_status": "representative_card_image_only_unresolved",
                        },
                    }
                    if representative_image:
                        image_map[identity] = {
                            "source_print_id": source_print_id,
                            "url": representative_image["image_url"],
                            "is_primary": True,
                            "source": "ygoprodeck-card-representative-unresolved",
                        }
                else:
                    if prior != print_row:
                        raise AssertionError(f"Canonical Print conflict: {identity}")
                    deduplicated_source_print_rows += 1
                    attrs = print_attr_map[identity]["attributes"]
                    attrs["source_rows"].append(source_evidence)
                    preferred_current = {
                        "rarity_raw": attrs.get("rarity_raw"),
                        "rarity_code_raw": attrs.get("rarity_code_raw"),
                        "rarity_canonical": attrs.get("rarity_canonical"),
                        "source_set_price": attrs.get("source_set_price"),
                    }
                    if _source_row_score(source_evidence) > _source_row_score(preferred_current):
                        attrs["rarity_raw"] = source_evidence["rarity_raw"]
                        attrs["rarity_code_raw"] = source_evidence["rarity_code_raw"]
                        attrs["source_set_price"] = source_evidence["source_set_price"]

                accepted_print_for_card = True
                release_external_id = _release_external_id(release_name)
                pair_key = (identity, release_external_id)
                pair = {
                    "source_print_id": source_print_id,
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
                if prior_pair is None:
                    print_release_pairs[pair_key] = pair
                    family_release_counts[family][release_name] += 1
                    if release_date:
                        family_dates[family].append(release_date)

        if not accepted_print_for_card:
            cards_without_print_evidence += 1

    if len(source_conflict_rows) != EXPECTED_STATIC["excluded_source_print_rows"]:
        raise AssertionError(
            f"Source conflict quarantine count moved: {len(source_conflict_rows)}"
        )

    set_rows: list[dict] = []
    for family in sorted(family_release_counts):
        release_counts = family_release_counts[family]
        display_candidates = []
        for release_name, count in release_counts.items():
            release = release_by_name[release_name]
            same_code = int(_clean(release.get("set_code")).upper() == family)
            date = _parse_date(release.get("tcg_date")) or "9999-12-31"
            display_candidates.append((-same_code, -count, date, release_name))
        display_candidates.sort()
        release_date = min(family_dates.get(family) or ["9999-12-31"])
        set_rows.append(
            {
                "code": family,
                "name": display_candidates[0][3] if display_candidates else family,
                "yugioh_id": f"family:{family}",
                "release_date": None if release_date == "9999-12-31" else release_date,
                "release_names": sorted(release_counts),
            }
        )

    release_rows = []
    for release in sorted(
        releases,
        key=lambda row: (
            _clean(row.get("tcg_date")) or "9999-12-31",
            _clean(row.get("set_name")),
        ),
    ):
        name = _clean(release.get("set_name"))
        release_rows.append(
            {
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
            }
        )

    # Deterministic output order.
    card_rows.sort(key=lambda row: int(row["source_card_id"]))
    card_attr_rows.sort(key=lambda row: int(row["source_card_id"]))
    artwork_rows.sort(
        key=lambda row: (int(row["source_card_id"]), row["ordinal"], row.get("image_id") or "")
    )
    source_conflict_rows.sort(
        key=lambda row: (int(row["source_card_id"]), row["full_collector_code_raw"], row.get("rarity_raw") or "")
    )
    print_rows = sorted(
        print_map.values(),
        key=lambda row: (
            row["set_family"],
            row["collector_number"],
            row["variant"],
            int(row["source_card_id"]),
        ),
    )
    print_attr_rows = sorted(
        print_attr_map.values(), key=lambda row: row["source_print_id"]
    )
    image_rows = sorted(image_map.values(), key=lambda row: row["source_print_id"])
    print_release_rows = sorted(
        print_release_pairs.values(),
        key=lambda row: (row["source_print_id"], row["release_external_id"]),
    )

    # Simulate the exact important PostgreSQL uniqueness constraints.
    _assert_unique("Set.code", (row["code"] for row in set_rows))
    _assert_unique("Set.yugioh_id", (row["yugioh_id"] for row in set_rows))
    _assert_unique("Card.yugoprodeck_id", (row["yugoprodeck_id"] for row in card_rows))
    _assert_unique("Card.card_key", (row["card_key"] for row in card_rows))
    _assert_unique("Print.yugioh_id", (row["yugioh_id"] for row in print_rows))
    _assert_unique("Print.print_key", (row["print_key"] for row in print_rows))
    _assert_unique(
        "Print shared tuple",
        (
            (
                row["set_family"],
                row["collector_number"],
                row["language"],
                row["is_foil"],
                row["variant"],
            )
            for row in print_rows
        ),
    )
    _assert_unique(
        "CatalogRelease source identity",
        ((row["source"], row["external_id"]) for row in release_rows),
    )
    _assert_unique(
        "PrintRelease identity",
        (
            (row["source_print_id"], row["release_external_id"])
            for row in print_release_rows
        ),
    )

    counts = {
        "source_cards": len(source_cards),
        "sets": len(set_rows),
        "canonical_cards": len(card_rows),
        "prints": len(print_rows),
        "releases": len(release_rows),
        "print_releases": len(print_release_rows),
        "card_attributes": len(card_attr_rows),
        "print_attributes": len(print_attr_rows),
        "representative_print_images": len(image_rows),
        "artwork_candidates": len(artwork_rows),
        "cards_without_print_evidence": cards_without_print_evidence,
        "noisy_rarity_rows": noisy_rarity_rows,
        "no_hyphen_family_fallback_rows": no_hyphen_fallback_rows,
        "source_card_aliases_merged": len(CARD_ALIAS_TO_CANONICAL),
        "excluded_source_print_rows": len(source_conflict_rows),
        "deduplicated_source_print_rows": deduplicated_source_print_rows,
    }
    _assert_snapshot_gates(counts)

    files = {
        "sets.jsonl": set_rows,
        "cards.jsonl": card_rows,
        "card_attributes.jsonl": card_attr_rows,
        "artwork_candidates.jsonl": artwork_rows,
        "source_conflicts.jsonl": source_conflict_rows,
        "prints.jsonl": print_rows,
        "print_attributes.jsonl": print_attr_rows,
        "representative_print_images.jsonl": image_rows,
        "catalog_releases.jsonl": release_rows,
        "print_releases.jsonl": print_release_rows,
    }
    bytes_by_file = {
        filename: _write_jsonl(output_dir / filename, rows)
        for filename, rows in files.items()
    }

    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "pass",
        "mode": "deterministic_yugioh_v2_canonical_snapshot_no_database_writes",
        "source": {
            "database_version": db_version_payload,
            "source_cards": len(source_cards),
            "official_release_rows": len(releases),
        },
        "counts": counts,
        "source_minimum_gates": SOURCE_MINIMUMS,
        "expected_static_gates": EXPECTED_STATIC,
        "snapshot_bytes_uncompressed": sum(bytes_by_file.values()),
        "bytes_by_file": bytes_by_file,
        "identity_policy": {
            "card": "canonical source Card id after explicit high-confidence source-alias merge",
            "card_aliases": CARD_ALIAS_TO_CANONICAL,
            "set": "collector-number family; no-hyphen rows use unanimous same-release fallback without rewriting the raw code",
            "print": "canonical Card id + raw full collector code + canonical human rarity; raw rarity/rareness-code aliases remain provenance",
            "release": "unique official cardsets.php release name",
            "source_conflicts": "proven bad source card-to-code assignments are quarantined, never silently repaired",
            "artwork": "all source art candidates retained at Card level; Print image is representative/unresolved only",
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