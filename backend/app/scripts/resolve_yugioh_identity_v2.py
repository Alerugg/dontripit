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
from sqlalchemy import text

from app import db
from app.ingest.connectors.ygoprodeck_yugioh import YgoProDeckYugiohConnector


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
    value = unicodedata.normalize("NFKD", _clean(value))
    value = "".join(ch for ch in value if not unicodedata.combining(ch)).casefold()
    return re.sub(r"[^a-z0-9]+", "-", value).strip("-")


def _canonical_rarity(raw: object) -> str:
    value = re.sub(r"\s+", " ", _clean(raw)).strip()
    if value in NOISY_RARITY_LABELS:
        return "Unknown"
    return RARITY_ALIASES.get(value, value or "Unknown")


def _collector_family(full_code: object) -> str:
    code = _clean(full_code).upper()
    if not code:
        return ""
    return code.split("-", 1)[0].strip()


def _release_external_id(name: object) -> str:
    folded = _fold(name)
    digest = hashlib.sha1(folded.encode("utf-8")).hexdigest()[:24]
    return f"ygoprodeck-release:{digest}"


def _print_identity(card_id: str, printing: dict) -> tuple[str, str, str, str]:
    raw_rarity = _clean(printing.get("set_rarity"))
    return (
        card_id,
        _clean(printing.get("set_code")).upper(),
        _clean(printing.get("set_rarity_code") or printing.get("set_rarity_short")).upper(),
        _canonical_rarity(raw_rarity).casefold(),
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
    http.headers.update({"User-Agent": "dontripit-catalog-v2-ygo-identity-resolution/1.0"})
    official_sets = _fetch_json(http, CARDSETS_URL)
    db_version = _fetch_json(http, DB_VERSION_URL)
    if not isinstance(official_sets, list) or not official_sets:
        raise AssertionError("cardsets.php returned no release rows")

    release_by_name: dict[str, dict] = {}
    release_by_fold: dict[str, list[dict]] = defaultdict(list)
    release_code_groups: dict[str, list[dict]] = defaultdict(list)
    for row in official_sets:
        name = _clean(row.get("set_name"))
        code = _clean(row.get("set_code")).upper()
        if name:
            if name in release_by_name:
                raise AssertionError(f"Duplicate exact release name: {name}")
            release_by_name[name] = row
            release_by_fold[_fold(name)].append(row)
        if code:
            release_code_groups[code].append(row)

    folded_release_collisions = {
        key: [r.get("set_name") for r in rows]
        for key, rows in release_by_fold.items()
        if len(rows) > 1
    }
    duplicated_release_codes = {
        code: [
            {
                "name": row.get("set_name"),
                "release_date": row.get("tcg_date"),
                "num_of_cards": row.get("num_of_cards"),
            }
            for row in rows
        ]
        for code, rows in release_code_groups.items()
        if len(rows) > 1
    }

    raw_print_rows = 0
    unmatched_release_rows: list[dict] = []
    folded_matches = 0
    collector_family_counts = Counter()
    release_code_vs_family = Counter()
    release_family_mismatches: list[dict] = []
    print_identity_owner: dict[tuple[str, str, str, str], dict] = {}
    print_identity_collisions: list[dict] = []
    print_identity_to_releases: dict[tuple[str, str, str, str], set[str]] = defaultdict(set)
    noisy_counts = Counter()
    noisy_rarity_codes: dict[str, Counter] = defaultdict(Counter)
    noisy_release_names: dict[str, Counter] = defaultdict(Counter)
    noisy_samples: dict[str, list[dict]] = defaultdict(list)
    no_print_cards: list[dict] = []
    no_print_evidence_counts = Counter()
    alt_art_cards: list[dict] = []
    image_ids = set()
    cross_card_image_owner: dict[str, str] = {}
    cross_card_image_collisions: list[dict] = []

    for card in cards:
        card_id = _clean(card.get("id"))
        if not card_id:
            continue

        images = card.get("card_images") or []
        if len(images) > 1:
            alt_art_cards.append({
                "card_id": card_id,
                "name": card.get("name"),
                "image_ids": [_clean(row.get("id")) for row in images if _clean(row.get("id"))],
            })
        for image in images:
            image_id = _clean(image.get("id"))
            if not image_id:
                continue
            image_ids.add(image_id)
            prior = cross_card_image_owner.get(image_id)
            if prior and prior != card_id:
                cross_card_image_collisions.append({"image_id": image_id, "cards": [prior, card_id]})
            cross_card_image_owner[image_id] = card_id

        printings = card.get("card_sets") or []
        if not printings:
            misc_list = card.get("misc_info") or []
            misc = misc_list[0] if isinstance(misc_list, list) and misc_list and isinstance(misc_list[0], dict) else {}
            tcg_date = misc.get("tcg_date")
            ocg_date = misc.get("ocg_date")
            if tcg_date and ocg_date:
                evidence_class = "tcg_and_ocg_dates_but_no_card_sets"
            elif tcg_date:
                evidence_class = "tcg_date_only_but_no_card_sets"
            elif ocg_date:
                evidence_class = "ocg_date_only_no_tcg_print_surface"
            else:
                evidence_class = "no_release_dates_or_card_sets"
            no_print_evidence_counts[evidence_class] += 1
            if len(no_print_cards) < 80:
                no_print_cards.append({
                    "card_id": card_id,
                    "name": card.get("name"),
                    "type": card.get("type"),
                    "frame_type": card.get("frameType"),
                    "archetype": card.get("archetype"),
                    "tcg_date": tcg_date,
                    "ocg_date": ocg_date,
                    "evidence_class": evidence_class,
                    "misc_keys": sorted(misc.keys()) if misc else [],
                })
            continue

        for printing in printings:
            raw_print_rows += 1
            release_name = _clean(printing.get("set_name"))
            release = release_by_name.get(release_name)
            if release is None:
                candidates = release_by_fold.get(_fold(release_name)) or []
                if len(candidates) == 1:
                    release = candidates[0]
                    folded_matches += 1
            if release is None:
                if len(unmatched_release_rows) < 80:
                    unmatched_release_rows.append({
                        "card_id": card_id,
                        "card_name": card.get("name"),
                        "set_name": release_name,
                        "full_set_code": printing.get("set_code"),
                    })
                continue

            authoritative_name = _clean(release.get("set_name"))
            authoritative_code = _clean(release.get("set_code")).upper()
            full_code = _clean(printing.get("set_code")).upper()
            family = _collector_family(full_code)
            collector_family_counts[family or "<missing>"] += 1
            if family == authoritative_code:
                release_code_vs_family["match"] += 1
            else:
                release_code_vs_family["mismatch"] += 1
                if len(release_family_mismatches) < 120:
                    release_family_mismatches.append({
                        "card_id": card_id,
                        "card_name": card.get("name"),
                        "release_name": authoritative_name,
                        "release_code": authoritative_code,
                        "full_set_code": full_code,
                        "derived_family": family,
                    })

            identity = _print_identity(card_id, printing)
            prior = print_identity_owner.get(identity)
            row = {
                "card_id": card_id,
                "card_name": card.get("name"),
                "release_name": authoritative_name,
                "release_external_id": _release_external_id(authoritative_name),
                "release_code": authoritative_code,
                "full_set_code": full_code,
                "collector_family": family,
                "rarity_raw": _clean(printing.get("set_rarity")),
                "rarity_code": _clean(printing.get("set_rarity_code") or printing.get("set_rarity_short")).upper(),
                "rarity_canonical": _canonical_rarity(printing.get("set_rarity")),
            }
            if prior is not None and prior != row:
                # Same physical identity under another commercial release is expected;
                # a conflicting card/code/rarity tuple is not.
                same_physical = (
                    prior["card_id"] == row["card_id"]
                    and prior["full_set_code"] == row["full_set_code"]
                    and prior["rarity_code"] == row["rarity_code"]
                    and prior["rarity_canonical"] == row["rarity_canonical"]
                )
                if not same_physical:
                    print_identity_collisions.append({"identity": identity, "rows": [prior, row]})
            print_identity_owner.setdefault(identity, row)
            print_identity_to_releases[identity].add(authoritative_name)

            raw_rarity = _clean(printing.get("set_rarity"))
            if raw_rarity in NOISY_RARITY_LABELS:
                noisy_counts[raw_rarity] += 1
                rarity_code = _clean(printing.get("set_rarity_code") or printing.get("set_rarity_short")).upper() or "<missing>"
                noisy_rarity_codes[raw_rarity][rarity_code] += 1
                noisy_release_names[raw_rarity][authoritative_name] += 1
                if len(noisy_samples[raw_rarity]) < 12:
                    noisy_samples[raw_rarity].append(row)

    multi_release_prints = [
        {
            "identity": list(identity),
            "release_count": len(releases),
            "releases": sorted(releases),
            "sample": print_identity_owner.get(identity),
        }
        for identity, releases in print_identity_to_releases.items()
        if len(releases) > 1
    ]
    multi_release_prints.sort(key=lambda row: (-row["release_count"], row["identity"]))

    db.init_engine()
    with db.SessionLocal() as session:
        game_id = session.execute(text("SELECT id FROM games WHERE slug='yugioh' LIMIT 1")).scalar_one_or_none()
        if game_id is None:
            neon = {
                "sets": 0,
                "cards": 0,
                "prints": 0,
                "catalog_releases": 0,
                "print_releases": 0,
            }
        else:
            neon = dict(session.execute(text(
                """
                SELECT
                  (SELECT COUNT(*) FROM sets WHERE game_id=:game) AS sets,
                  (SELECT COUNT(*) FROM cards WHERE game_id=:game) AS cards,
                  (SELECT COUNT(*) FROM prints p JOIN cards c ON c.id=p.card_id WHERE c.game_id=:game) AS prints,
                  (SELECT COUNT(*) FROM catalog_releases WHERE game_id=:game) AS catalog_releases,
                  (SELECT COUNT(*) FROM print_releases pr JOIN prints p ON p.id=pr.print_id JOIN cards c ON c.id=p.card_id WHERE c.game_id=:game) AS print_releases
                """
            ), {"game": game_id}).mappings().one())
            neon = {key: int(value or 0) for key, value in neon.items()}
        session.rollback()

    hard_failures = []
    if folded_release_collisions:
        hard_failures.append(f"{len(folded_release_collisions)} folded release-name collisions")
    if unmatched_release_rows:
        hard_failures.append(f"{len(unmatched_release_rows)} printing rows cannot map to official release")
    if print_identity_collisions:
        hard_failures.append(f"{len(print_identity_collisions)} physical Print identity collisions")
    if cross_card_image_collisions:
        hard_failures.append(f"{len(cross_card_image_collisions)} image IDs belong to multiple Cards")

    status = "fail" if hard_failures else "pass"
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": "read_only_yugioh_identity_v2_blocker_resolution",
        "status": status,
        "source": {
            "database_version": db_version,
            "cards": len(cards),
            "official_release_rows": len(official_sets),
            "raw_card_set_rows": raw_print_rows,
        },
        "resolved_architecture": {
            "set": "collector-number family derived from full card_sets.set_code prefix; one canonical Set per family code",
            "catalog_release": "one source-backed commercial release per unique official cardsets.php set_name; set_code is non-unique metadata",
            "card": "YGOPRODeck card id",
            "print": "deduped physical identity: card id + full collector code + rarity code + canonical rarity; language=en for this source surface",
            "print_release": "many-to-many provenance from physical Print to official commercial release",
            "artwork": "preserve all card_images[] as Card-level artwork candidates; do not claim Print↔art mapping without source evidence",
        },
        "set_and_release_evidence": {
            "official_release_rows": len(official_sets),
            "unique_exact_release_names": len(release_by_name),
            "folded_release_name_collisions": len(folded_release_collisions),
            "distinct_official_release_codes": len(release_code_groups),
            "duplicate_release_code_groups": len(duplicated_release_codes),
            "collector_families_seen_in_prints": len(collector_family_counts),
            "release_code_matches_derived_family": int(release_code_vs_family["match"]),
            "release_code_mismatches_derived_family": int(release_code_vs_family["mismatch"]),
            "folded_release_matches_used": folded_matches,
        },
        "print_evidence": {
            "unique_physical_print_identities": len(print_identity_owner),
            "identity_collisions": len(print_identity_collisions),
            "prints_linked_to_multiple_official_releases": len(multi_release_prints),
        },
        "rarity_policy": {
            "canonical_rule": "known rarity aliases normalize; non-rarity source labels map to Unknown while raw label/code are preserved in provenance attributes",
            "noisy_row_count": int(sum(noisy_counts.values())),
            "noisy_counts": dict(noisy_counts.most_common()),
            "noisy_rarity_code_cross_tab": {
                label: dict(counter.most_common()) for label, counter in sorted(noisy_rarity_codes.items())
            },
        },
        "cards_without_print_evidence": {
            "count": int(sum(no_print_evidence_counts.values())),
            "evidence_classes": dict(no_print_evidence_counts.most_common()),
            "policy": "retain Card identity; create no synthetic Print. Certification must expose this source-surface gap explicitly.",
        },
        "alternate_artwork": {
            "cards": len(alt_art_cards),
            "unique_image_ids_across_source": len(image_ids),
            "cross_card_image_id_collisions": len(cross_card_image_collisions),
            "policy": "preserve Card-level artwork candidates; map an artwork to a Print only when a later source provides explicit evidence.",
        },
        "legacy_neon": neon,
        "hard_failures": hard_failures,
        "samples": {
            "duplicate_release_codes": dict(list(sorted(duplicated_release_codes.items()))[:50]),
            "release_family_mismatches": release_family_mismatches,
            "multi_release_physical_prints": multi_release_prints[:80],
            "noisy_rarity_rows": dict(noisy_samples),
            "noisy_release_names": {
                label: dict(counter.most_common(20)) for label, counter in sorted(noisy_release_names.items())
            },
            "cards_without_print_evidence": no_print_cards,
            "alternate_art_cards": alt_art_cards[:80],
            "unmatched_release_rows": unmatched_release_rows,
            "folded_release_name_collisions": folded_release_collisions,
            "print_identity_collisions": print_identity_collisions[:50],
        },
    }
    _write(report_path, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if hard_failures:
        raise AssertionError("Yu-Gi-Oh Identity V2 blocker resolution still has hard failures")
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report-path", type=Path, default=None)
    args = parser.parse_args()
    run(report_path=args.report_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
