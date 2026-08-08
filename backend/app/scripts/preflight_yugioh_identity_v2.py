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
LANGUAGE = "en"


def _write(path: Path | None, payload: dict) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def _clean(value: object) -> str:
    return str(value or "").strip()


def _fold(value: object) -> str:
    text = unicodedata.normalize("NFKD", _clean(value))
    text = "".join(ch for ch in text if not unicodedata.combining(ch)).lower()
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")


def _normalize_rarity(value: object) -> str:
    raw = re.sub(r"\s+", " ", _clean(value)).strip()
    aliases = {
        "PLatinum Secret Rare": "Platinum Secret Rare",
        "Starfoil": "Starfoil Rare",
        "Cr": "Collector's Rare",
        "Extra Secret": "Extra Secret Rare",
    }
    return aliases.get(raw, raw or "Unknown")


def _print_identity(card_id: str, printing: dict) -> tuple[str, str, str, str]:
    return (
        card_id,
        _clean(printing.get("set_code")).upper(),
        _clean(printing.get("set_rarity_code") or printing.get("set_rarity_short")).upper(),
        _normalize_rarity(printing.get("set_rarity")).casefold(),
    )


def _print_external_id(identity: tuple[str, str, str, str]) -> str:
    payload = "|".join(identity)
    return "ygo-v2:" + hashlib.sha1(payload.encode("utf-8")).hexdigest()[:24]


def _set_external_id(authoritative: dict) -> str:
    code = _clean(authoritative.get("set_code")).upper()
    if code:
        return f"ygoprodeck-set:{code}"
    return f"ygoprodeck-set-name:{_fold(authoritative.get('set_name'))}"


def _fetch_json(session: requests.Session, url: str):
    response = session.get(url, timeout=60)
    response.raise_for_status()
    return response.json()


def run(*, report_path: Path | None = None) -> dict:
    connector = YgoProDeckYugiohConnector()
    cards = connector._load_remote(limit=None, page_size=PAGE_SIZE)
    http = requests.Session()
    http.headers.update({"User-Agent": "dontripit-catalog-v2-ygo-preflight/1.0"})
    official_sets = _fetch_json(http, CARDSETS_URL)
    db_version_payload = _fetch_json(http, DB_VERSION_URL)

    if not isinstance(official_sets, list) or not official_sets:
        raise AssertionError("YGOPRODeck cardsets.php returned no authoritative sets")

    set_names: Counter[str] = Counter()
    set_codes: Counter[str] = Counter()
    authoritative_by_name: dict[str, dict] = {}
    authoritative_by_folded_name: dict[str, list[dict]] = defaultdict(list)
    for row in official_sets:
        name = _clean(row.get("set_name"))
        code = _clean(row.get("set_code")).upper()
        if name:
            set_names[name] += 1
            authoritative_by_name.setdefault(name, row)
            authoritative_by_folded_name[_fold(name)].append(row)
        if code:
            set_codes[code] += 1

    duplicate_authoritative_names = sorted(name for name, count in set_names.items() if count > 1)
    duplicate_authoritative_codes = sorted(code for code, count in set_codes.items() if count > 1)

    canonical_cards: dict[str, dict] = {}
    card_sets_without_authoritative_match: list[dict] = []
    card_sets_folded_name_matches: list[dict] = []
    exact_prints: dict[str, dict] = {}
    identity_collisions: list[dict] = []
    tuple_collisions: list[dict] = []
    tuple_owner: dict[tuple[str, str, str, str], str] = {}
    cards_without_print_evidence: list[dict] = []
    cards_with_alt_art: list[dict] = []
    image_id_owner: dict[str, str] = {}
    cross_card_image_id_collisions: list[dict] = []
    rarity_counts = Counter()
    rarity_raw_to_normalized: dict[str, str] = {}
    format_type_counts = Counter()
    release_set_usage = Counter()

    for raw_card in cards:
        card_id = _clean(raw_card.get("id"))
        if not card_id:
            continue
        canonical_cards[card_id] = raw_card
        format_type_counts[_clean(raw_card.get("type")) or "Unknown"] += 1

        images = raw_card.get("card_images") or []
        if len(images) > 1:
            cards_with_alt_art.append({
                "card_id": card_id,
                "name": raw_card.get("name"),
                "image_ids": [_clean(image.get("id")) for image in images],
            })
        for image in images:
            image_id = _clean(image.get("id"))
            if not image_id:
                continue
            owner = image_id_owner.get(image_id)
            if owner and owner != card_id:
                cross_card_image_id_collisions.append({"image_id": image_id, "owners": [owner, card_id]})
            image_id_owner[image_id] = card_id

        printing_rows = raw_card.get("card_sets") or []
        if not printing_rows:
            misc = raw_card.get("misc_info") or []
            misc_row = misc[0] if isinstance(misc, list) and misc and isinstance(misc[0], dict) else {}
            cards_without_print_evidence.append({
                "card_id": card_id,
                "name": raw_card.get("name"),
                "type": raw_card.get("type"),
                "frame_type": raw_card.get("frameType"),
                "archetype": raw_card.get("archetype"),
                "tcg_date": misc_row.get("tcg_date"),
                "ocg_date": misc_row.get("ocg_date"),
                "has_misc": bool(misc_row),
            })
            continue

        for printing in printing_rows:
            set_name = _clean(printing.get("set_name"))
            release = authoritative_by_name.get(set_name)
            match_mode = "exact"
            if release is None:
                candidates = authoritative_by_folded_name.get(_fold(set_name)) or []
                if len(candidates) == 1:
                    release = candidates[0]
                    match_mode = "folded"
                    card_sets_folded_name_matches.append({
                        "card_id": card_id,
                        "set_name": set_name,
                        "authoritative_set_name": release.get("set_name"),
                    })
            if release is None:
                card_sets_without_authoritative_match.append({
                    "card_id": card_id,
                    "card_name": raw_card.get("name"),
                    "set_name": set_name,
                    "set_code": printing.get("set_code"),
                })
                continue

            release_external_id = _set_external_id(release)
            release_set_usage[release_external_id] += 1
            identity_tuple = _print_identity(card_id, printing)
            external_id = _print_external_id(identity_tuple)
            existing = exact_prints.get(external_id)
            if existing and existing["identity_tuple"] != identity_tuple:
                identity_collisions.append({
                    "external_id": external_id,
                    "existing": existing["identity_tuple"],
                    "incoming": identity_tuple,
                })
            if identity_tuple in tuple_owner and tuple_owner[identity_tuple] != external_id:
                tuple_collisions.append({
                    "identity_tuple": identity_tuple,
                    "external_ids": [tuple_owner[identity_tuple], external_id],
                })
            tuple_owner[identity_tuple] = external_id

            raw_rarity = _clean(printing.get("set_rarity"))
            normalized_rarity = _normalize_rarity(raw_rarity)
            rarity_counts[normalized_rarity] += 1
            rarity_raw_to_normalized[raw_rarity] = normalized_rarity
            exact_prints[external_id] = {
                "external_id": external_id,
                "identity_tuple": identity_tuple,
                "card_id": card_id,
                "card_name": raw_card.get("name"),
                "release_external_id": release_external_id,
                "release_name": release.get("set_name"),
                "release_code": _clean(release.get("set_code")).upper(),
                "release_date": release.get("tcg_date"),
                "collector_number": _clean(printing.get("set_code")).upper(),
                "rarity_raw": raw_rarity,
                "rarity": normalized_rarity,
                "rarity_code": _clean(printing.get("set_rarity_code") or printing.get("set_rarity_short")).upper(),
                "language": LANGUAGE,
                "match_mode": match_mode,
            }

    canonical_sets = {
        _set_external_id(row): {
            "external_id": _set_external_id(row),
            "name": _clean(row.get("set_name")),
            "code": _clean(row.get("set_code")).upper(),
            "num_of_cards": row.get("num_of_cards"),
            "release_date": row.get("tcg_date"),
        }
        for row in official_sets
    }

    weird_rarity_labels = sorted(
        raw for raw, normalized in rarity_raw_to_normalized.items()
        if raw in {"New", "2", "3", "Reprint", "New artwork", "European & Oceanian debut", "European debut", "Oceanian debut", "force-SMW"}
        or raw != normalized and raw not in {"PLatinum Secret Rare", "Starfoil", "Cr", "Extra Secret"}
    )

    db.init_engine()
    with db.SessionLocal() as session:
        game_id = session.execute(text("SELECT id FROM games WHERE slug='yugioh' LIMIT 1")).scalar_one_or_none()
        if game_id is None:
            old_counts = {"sets": 0, "cards": 0, "prints": 0}
            old_set_names = set()
            old_card_ids = set()
            old_print_ids = set()
        else:
            old_counts = dict(session.execute(text(
                """
                SELECT
                  (SELECT COUNT(*) FROM sets WHERE game_id=:game) AS sets,
                  (SELECT COUNT(*) FROM cards WHERE game_id=:game) AS cards,
                  (SELECT COUNT(*) FROM prints p JOIN cards c ON c.id=p.card_id WHERE c.game_id=:game) AS prints
                """
            ), {"game": game_id}).mappings().one())
            old_counts = {key: int(value or 0) for key, value in old_counts.items()}
            old_set_names = {
                _clean(value)
                for value in session.execute(text("SELECT name FROM sets WHERE game_id=:game"), {"game": game_id}).scalars().all()
                if _clean(value)
            }
            old_card_ids = {
                _clean(value)
                for value in session.execute(text("SELECT yugoprodeck_id FROM cards WHERE game_id=:game AND yugoprodeck_id IS NOT NULL"), {"game": game_id}).scalars().all()
                if _clean(value)
            }
            old_print_ids = {
                _clean(value)
                for value in session.execute(text(
                    "SELECT p.yugioh_id FROM prints p JOIN cards c ON c.id=p.card_id WHERE c.game_id=:game AND p.yugioh_id IS NOT NULL"
                ), {"game": game_id}).scalars().all()
                if _clean(value)
            }
        session.rollback()

    canonical_set_names = {row["name"] for row in canonical_sets.values()}
    exact_print_id_set = set(exact_prints)
    canonical_card_id_set = set(canonical_cards)

    hard_failures = []
    review_blockers = []
    if duplicate_authoritative_names:
        hard_failures.append(f"{len(duplicate_authoritative_names)} duplicate set names in cardsets.php")
    if duplicate_authoritative_codes:
        hard_failures.append(f"{len(duplicate_authoritative_codes)} duplicate authoritative set codes in cardsets.php")
    if identity_collisions or tuple_collisions:
        hard_failures.append("deterministic Print identity collisions detected")
    if card_sets_without_authoritative_match:
        review_blockers.append(
            f"{len(card_sets_without_authoritative_match)} card_sets rows do not map to authoritative cardsets.php releases"
        )
    if weird_rarity_labels:
        review_blockers.append(f"{len(weird_rarity_labels)} non-rarity/noisy source labels require explicit normalization policy")
    if cards_without_print_evidence:
        review_blockers.append(
            f"{len(cards_without_print_evidence)} source Cards have no card_sets physical Print evidence"
        )
    if cards_with_alt_art:
        review_blockers.append(
            f"{len(cards_with_alt_art)} Cards expose alternate artwork IDs without per-print artwork mapping"
        )

    if hard_failures:
        status = "fail"
    elif review_blockers:
        status = "review_required"
    else:
        status = "pass"

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": "read_only_yugioh_identity_v2_preflight",
        "source": {
            "cardinfo": "YGOPRODeck API v7 cardinfo.php",
            "cardsets": "YGOPRODeck API v7 cardsets.php",
            "database_version": db_version_payload,
        },
        "status": status,
        "proposed_identity": {
            "game": "yugioh",
            "set": "authoritative cardsets.php release; external id uses official set_code",
            "card": "YGOPRODeck card id",
            "print": "sha1(card_id | full card_sets.set_code | rarity_code | normalized rarity)",
            "collector_number": "full card_sets.set_code (for example MRD-EN005)",
            "language": "en for this surface",
            "artwork": "card_images[] preserved separately until source-backed Print↔art mapping exists",
        },
        "authoritative_sets": {
            "count": len(canonical_sets),
            "duplicate_names": len(duplicate_authoritative_names),
            "duplicate_codes": len(duplicate_authoritative_codes),
            "used_by_card_sets": len(release_set_usage),
            "unused_by_current_card_sets": len(set(canonical_sets) - set(release_set_usage)),
        },
        "canonical_plan": {
            "cards": len(canonical_card_id_set),
            "cards_with_no_print_evidence": len(cards_without_print_evidence),
            "exact_source_prints": len(exact_print_id_set),
            "deterministic_print_identity_collisions": len(identity_collisions),
            "deterministic_tuple_collisions": len(tuple_collisions),
            "alternate_art_cards": len(cards_with_alt_art),
            "alternate_art_image_ids": sum(len(row["image_ids"]) for row in cards_with_alt_art),
            "cross_card_image_id_collisions": len(cross_card_image_id_collisions),
            "rarity_counts_normalized": dict(rarity_counts.most_common()),
            "noisy_rarity_labels": weird_rarity_labels,
        },
        "source_reconciliation": {
            "card_set_rows_without_authoritative_release": len(card_sets_without_authoritative_match),
            "folded_name_release_matches": len(card_sets_folded_name_matches),
        },
        "legacy_neon": {
            **old_counts,
            "legacy_set_names_matching_authoritative_release_names": len(old_set_names & canonical_set_names),
            "canonical_cards_already_present": len(old_card_ids & canonical_card_id_set),
            "legacy_print_ids_matching_v2_ids": len(old_print_ids & exact_print_id_set),
        },
        "migration_implication": {
            "strategy": "rebuild Yu-Gi-Oh catalog identity rather than incrementally patch legacy Set rows",
            "reason": "legacy DB Set identity is card_sets.set_code (printing code), while V2 Set identity is authoritative cardsets.php release",
            "destructive_write_authorized": False,
        },
        "samples": {
            "authoritative_sets": list(canonical_sets.values())[:40],
            "unmatched_card_set_rows": card_sets_without_authoritative_match[:100],
            "folded_release_matches": card_sets_folded_name_matches[:100],
            "cards_without_print_evidence": cards_without_print_evidence[:100],
            "alternate_art_cards": cards_with_alt_art[:60],
            "identity_collisions": identity_collisions[:50],
            "tuple_collisions": tuple_collisions[:50],
            "cross_card_image_id_collisions": cross_card_image_id_collisions[:50],
        },
        "hard_failures": hard_failures,
        "review_blockers": review_blockers,
    }
    _write(report_path, report)
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report-path", type=Path)
    args = parser.parse_args()
    run(report_path=args.report_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
