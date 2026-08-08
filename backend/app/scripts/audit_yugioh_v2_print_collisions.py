from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import requests

from app.ingest.connectors.ygoprodeck_yugioh import YgoProDeckYugiohConnector
from app.ingest.normalization import build_card_key, build_print_key, normalize_collector_number
from app.scripts.build_yugioh_v2_snapshot import (
    CARDSETS_URL,
    PAGE_SIZE,
    _canonical_rarity,
    _clean,
    _family_for_print,
    _fold,
    _variant_for_rarity,
)


def _write(path: Path | None, payload: dict) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def _fetch_json(http: requests.Session, url: str):
    response = http.get(url, timeout=60)
    response.raise_for_status()
    return response.json()


def run(*, report_path: Path | None = None) -> dict:
    connector = YgoProDeckYugiohConnector()
    cards = connector._load_remote(limit=None, page_size=PAGE_SIZE)

    http = requests.Session()
    http.headers.update({"User-Agent": "dontripit-catalog-v2-ygo-print-collision-audit/1.0"})
    releases = _fetch_json(http, CARDSETS_URL)

    release_by_name: dict[str, dict] = {}
    release_by_fold: dict[str, list[dict]] = defaultdict(list)
    for release in releases:
        name = _clean(release.get("set_name"))
        release_by_name[name] = release
        release_by_fold[_fold(name)].append(release)

    explicit_families = defaultdict(lambda: defaultdict(int))
    for card in cards:
        for printing in card.get("card_sets") or []:
            source_name = _clean(printing.get("set_name"))
            release = release_by_name.get(source_name)
            if release is None:
                candidates = release_by_fold.get(_fold(source_name)) or []
                release = candidates[0] if len(candidates) == 1 else None
            if release is None:
                raise AssertionError(f"Unmatched official release: {source_name!r}")
            full_code = _clean(printing.get("set_code")).upper()
            if "-" in full_code:
                family = full_code.split("-", 1)[0].strip()
                if family:
                    explicit_families[_clean(release.get("set_name"))][family] += 1

    # _family_for_print expects Counter-like objects with most_common().
    from collections import Counter
    explicit_family_counters = {
        release: Counter(counts) for release, counts in explicit_families.items()
    }

    print_key_groups: dict[str, list[dict]] = defaultdict(list)
    shared_tuple_groups: dict[tuple, list[dict]] = defaultdict(list)
    exact_identity_groups: dict[tuple, list[dict]] = defaultdict(list)

    for card in cards:
        card_id = _clean(card.get("id"))
        card_name = _clean(card.get("name"))
        external_ids = [{"source": "ygoprodeck", "id_type": "card_id", "value": card_id}]
        card_key = build_card_key(
            game_slug="yugioh",
            canonical_name=card_name,
            identity_hints={},
            external_ids=external_ids,
        )
        for printing in card.get("card_sets") or []:
            source_name = _clean(printing.get("set_name"))
            release = release_by_name.get(source_name)
            if release is None:
                candidates = release_by_fold.get(_fold(source_name)) or []
                release = candidates[0] if len(candidates) == 1 else None
            if release is None:
                raise AssertionError(f"Unmatched official release: {source_name!r}")

            release_name = _clean(release.get("set_name"))
            release_code = _clean(release.get("set_code")).upper()
            full_code = _clean(printing.get("set_code")).upper()
            family, family_resolution = _family_for_print(
                full_code=full_code,
                release_name=release_name,
                release_code=release_code,
                explicit_families=explicit_family_counters,
            )
            raw_rarity = _clean(printing.get("set_rarity"))
            rarity_code = _clean(printing.get("set_rarity_code") or printing.get("set_rarity_short")).upper()
            canonical_rarity = _canonical_rarity(raw_rarity)
            proposed_variant = _variant_for_rarity(canonical_rarity)
            exact_identity = (card_id, full_code, rarity_code, canonical_rarity.casefold())
            print_key = build_print_key(
                card_key=card_key,
                set_code=family,
                collector_number=normalize_collector_number(full_code),
                language="en",
                finish="nonfoil",
                variant=proposed_variant,
            )
            shared_tuple = (family, full_code, "en", False, proposed_variant)
            row = {
                "card_id": card_id,
                "card_name": card_name,
                "family": family,
                "family_resolution": family_resolution,
                "full_collector_code": full_code,
                "release_name": release_name,
                "official_release_code": release_code or None,
                "rarity_raw": raw_rarity or None,
                "rarity_code": rarity_code or None,
                "rarity_canonical": canonical_rarity,
                "proposed_variant": proposed_variant,
                "proposed_print_key": print_key,
                "exact_identity": list(exact_identity),
                "set_price": _clean(printing.get("set_price")) or None,
            }
            print_key_groups[print_key].append(row)
            shared_tuple_groups[shared_tuple].append(row)
            exact_identity_groups[exact_identity].append(row)

    def collision_rows(groups):
        output = []
        for key, rows in groups.items():
            exact_ids = {tuple(row["exact_identity"]) for row in rows}
            if len(exact_ids) <= 1:
                continue
            output.append({
                "key": list(key) if isinstance(key, tuple) else key,
                "row_count": len(rows),
                "distinct_exact_identities": len(exact_ids),
                "rows": rows,
            })
        output.sort(key=lambda row: (-row["distinct_exact_identities"], str(row["key"])))
        return output

    print_key_collisions = collision_rows(print_key_groups)
    shared_tuple_collisions = collision_rows(shared_tuple_groups)

    # Determine which source dimensions actually distinguish colliding identities.
    discriminator_counts = defaultdict(int)
    code_pairs = defaultdict(int)
    rarity_pairs = defaultdict(int)
    release_pairs = defaultdict(int)
    collision_exact_rows = 0
    for group in print_key_collisions:
        rows = group["rows"]
        collision_exact_rows += group["distinct_exact_identities"]
        rarity_codes = {row.get("rarity_code") for row in rows}
        raw_rarities = {row.get("rarity_raw") for row in rows}
        canonical_rarities = {row.get("rarity_canonical") for row in rows}
        releases_in_group = {row.get("release_name") for row in rows}
        full_codes = {row.get("full_collector_code") for row in rows}
        if len(rarity_codes) > 1:
            discriminator_counts["rarity_code"] += 1
        if len(raw_rarities) > 1:
            discriminator_counts["raw_rarity"] += 1
        if len(canonical_rarities) > 1:
            discriminator_counts["canonical_rarity"] += 1
        if len(releases_in_group) > 1:
            discriminator_counts["release_name"] += 1
        if len(full_codes) > 1:
            discriminator_counts["full_collector_code"] += 1
        for code in sorted(str(v) for v in rarity_codes):
            code_pairs[code] += 1
        for rarity in sorted(str(v) for v in raw_rarities):
            rarity_pairs[rarity] += 1
        for release_name in sorted(str(v) for v in releases_in_group):
            release_pairs[release_name] += 1

    exact_identity_duplicate_groups = [
        {
            "identity": list(identity),
            "rows": rows,
        }
        for identity, rows in exact_identity_groups.items()
        if len(rows) > 1
    ]

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": "read_only_yugioh_v2_print_collision_audit",
        "status": "pass",
        "source_cards": len(cards),
        "print_key_collision_groups": len(print_key_collisions),
        "shared_constraint_collision_groups": len(shared_tuple_collisions),
        "collision_exact_identity_rows": collision_exact_rows,
        "exact_identity_duplicate_source_groups": len(exact_identity_duplicate_groups),
        "group_discriminator_counts": dict(sorted(discriminator_counts.items())),
        "rarity_codes_present_in_collisions": dict(sorted(code_pairs.items())),
        "raw_rarities_present_in_collisions": dict(sorted(rarity_pairs.items())),
        "releases_present_in_collisions": dict(sorted(release_pairs.items())),
        "print_key_collisions": print_key_collisions,
        "shared_constraint_collisions": shared_tuple_collisions,
        "exact_identity_duplicate_source_groups_sample": exact_identity_duplicate_groups[:50],
        "database_writes": 0,
    }
    _write(report_path, report)
    print(json.dumps({
        key: report[key]
        for key in (
            "status",
            "source_cards",
            "print_key_collision_groups",
            "shared_constraint_collision_groups",
            "collision_exact_identity_rows",
            "exact_identity_duplicate_source_groups",
            "group_discriminator_counts",
            "rarity_codes_present_in_collisions",
            "raw_rarities_present_in_collisions",
        )
    }, ensure_ascii=False, indent=2))
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report-path", type=Path, default=None)
    args = parser.parse_args()
    run(report_path=args.report_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
