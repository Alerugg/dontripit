from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import text

from app import db
from app.ingest.connectors.ygoprodeck_yugioh import YgoProDeckYugiohConnector


PAGE_SIZE = 500


def _write(path: Path | None, payload: dict) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def _clean(value: object) -> str:
    return str(value or "").strip()


def _prefix(set_code: str) -> str:
    code = _clean(set_code).upper()
    if not code:
        return ""
    return code.split("-", 1)[0]


def _stable_print_tuple(card_id: str, row: dict) -> tuple[str, str, str, str]:
    return (
        card_id,
        _clean(row.get("set_code")).upper(),
        _clean(row.get("set_rarity_code") or row.get("set_rarity_short")).upper(),
        _clean(row.get("set_rarity")).casefold(),
    )


def run(*, report_path: Path | None = None) -> dict:
    connector = YgoProDeckYugiohConnector()
    cards = connector._load_remote(limit=None, page_size=PAGE_SIZE)

    card_ids: list[str] = []
    card_id_duplicates: list[str] = []
    seen_card_ids: set[str] = set()
    source_set_codes: set[str] = set()
    source_set_names: set[str] = set()
    source_set_prefixes: set[str] = set()
    set_name_to_codes: dict[str, set[str]] = defaultdict(set)
    set_name_to_prefixes: dict[str, set[str]] = defaultdict(set)
    raw_print_rows = 0
    generated_print_ids: set[str] = set()
    generated_print_id_duplicates: list[str] = []
    stable_tuple_counts: Counter[tuple[str, str, str, str]] = Counter()
    rarity_counts: Counter[str] = Counter()
    cards_without_sets: list[str] = []
    cards_with_multiple_images: list[dict] = []
    total_image_rows = 0
    coverage = Counter()
    banlist_counts = Counter()

    for card in cards:
        card_id = _clean(card.get("id"))
        if not card_id:
            continue
        if card_id in seen_card_ids:
            card_id_duplicates.append(card_id)
        seen_card_ids.add(card_id)
        card_ids.append(card_id)

        for field in ("name", "type", "frameType", "desc", "atk", "def", "level", "race", "attribute", "scale", "linkval", "archetype", "misc_info"):
            value = card.get(field)
            if value not in (None, "", [], {}):
                coverage[field] += 1
        banlist = card.get("banlist_info") or {}
        if banlist:
            coverage["banlist_info"] += 1
            for key, value in banlist.items():
                if value not in (None, ""):
                    banlist_counts[f"{key}:{value}"] += 1

        images = card.get("card_images") or []
        total_image_rows += len(images)
        if len(images) > 1:
            cards_with_multiple_images.append({
                "card_id": card_id,
                "name": card.get("name"),
                "image_rows": len(images),
                "image_ids": [_clean(image.get("id")) for image in images[:10]],
            })

        card_sets = card.get("card_sets") or []
        if not card_sets:
            cards_without_sets.append(card_id)
            continue

        for idx, set_row in enumerate(card_sets, start=1):
            raw_print_rows += 1
            set_code = _clean(set_row.get("set_code"))
            set_name = _clean(set_row.get("set_name"))
            prefix = _prefix(set_code)
            if set_code:
                source_set_codes.add(set_code.lower())
            if set_name:
                source_set_names.add(set_name)
                if set_code:
                    set_name_to_codes[set_name].add(set_code)
                if prefix:
                    set_name_to_prefixes[set_name].add(prefix)
            if prefix:
                source_set_prefixes.add(prefix)

            rarity = _clean(set_row.get("set_rarity") or "unknown")
            rarity_counts[rarity] += 1
            stable_tuple_counts[_stable_print_tuple(card_id, set_row)] += 1

            generated_id = f"{card_id}::{set_code}::{idx}"
            if generated_id in generated_print_ids:
                generated_print_id_duplicates.append(generated_id)
            generated_print_ids.add(generated_id)

    duplicate_stable_tuples = [
        {"card_id": key[0], "set_code": key[1], "rarity_code": key[2], "rarity": key[3], "rows": count}
        for key, count in stable_tuple_counts.items()
        if count > 1
    ]
    set_names_multi_codes = [
        {
            "set_name": name,
            "set_codes": len(codes),
            "prefixes": sorted(set_name_to_prefixes.get(name) or []),
            "sample_codes": sorted(codes)[:12],
        }
        for name, codes in set_name_to_codes.items()
        if len(codes) > 1
    ]
    set_names_multi_codes.sort(key=lambda row: (-row["set_codes"], row["set_name"]))

    db.init_engine()
    with db.SessionLocal() as session:
        game_id = session.execute(text("SELECT id FROM games WHERE slug='yugioh' LIMIT 1")).scalar_one_or_none()
        if game_id is None:
            neon = {
                "game_present": False,
                "cards": 0,
                "cards_with_source_id": 0,
                "prints": 0,
                "prints_with_source_id": 0,
                "sets": 0,
                "sets_with_source_id": 0,
                "primary_images": 0,
            }
            neon_card_ids: set[str] = set()
            neon_print_ids: set[str] = set()
            neon_set_ids: set[str] = set()
            duplicate_card_source_ids: list[dict] = []
            duplicate_print_source_ids: list[dict] = []
        else:
            neon = dict(session.execute(text(
                """
                SELECT
                  TRUE AS game_present,
                  (SELECT COUNT(*) FROM cards WHERE game_id=:game) AS cards,
                  (SELECT COUNT(*) FROM cards WHERE game_id=:game AND yugoprodeck_id IS NOT NULL) AS cards_with_source_id,
                  (SELECT COUNT(*) FROM prints p JOIN cards c ON c.id=p.card_id WHERE c.game_id=:game) AS prints,
                  (SELECT COUNT(*) FROM prints p JOIN cards c ON c.id=p.card_id WHERE c.game_id=:game AND p.yugioh_id IS NOT NULL) AS prints_with_source_id,
                  (SELECT COUNT(*) FROM sets WHERE game_id=:game) AS sets,
                  (SELECT COUNT(*) FROM sets WHERE game_id=:game AND yugioh_id IS NOT NULL) AS sets_with_source_id,
                  (SELECT COUNT(*) FROM print_images pi JOIN prints p ON p.id=pi.print_id JOIN cards c ON c.id=p.card_id WHERE c.game_id=:game AND pi.is_primary=TRUE) AS primary_images,
                  (SELECT COUNT(*) FROM prints p JOIN cards c ON c.id=p.card_id WHERE c.game_id=:game AND p.print_key IS NULL) AS prints_missing_print_key,
                  (SELECT COUNT(*) FROM prints p JOIN cards c ON c.id=p.card_id WHERE c.game_id=:game AND lower(COALESCE(p.rarity,'')) IN ('','unknown')) AS prints_unknown_rarity
                """
            ), {"game": game_id}).mappings().one())
            neon = {key: (bool(value) if key == "game_present" else int(value or 0)) for key, value in neon.items()}

            neon_card_ids = {
                _clean(value)
                for value in session.execute(text(
                    "SELECT yugoprodeck_id FROM cards WHERE game_id=:game AND yugoprodeck_id IS NOT NULL"
                ), {"game": game_id}).scalars().all()
                if _clean(value)
            }
            neon_print_ids = {
                _clean(value)
                for value in session.execute(text(
                    "SELECT p.yugioh_id FROM prints p JOIN cards c ON c.id=p.card_id WHERE c.game_id=:game AND p.yugioh_id IS NOT NULL"
                ), {"game": game_id}).scalars().all()
                if _clean(value)
            }
            neon_set_ids = {
                _clean(value).lower()
                for value in session.execute(text(
                    "SELECT yugioh_id FROM sets WHERE game_id=:game AND yugioh_id IS NOT NULL"
                ), {"game": game_id}).scalars().all()
                if _clean(value)
            }
            duplicate_card_source_ids = [dict(row) for row in session.execute(text(
                """
                SELECT yugoprodeck_id, COUNT(*) AS rows
                FROM cards WHERE game_id=:game AND yugoprodeck_id IS NOT NULL
                GROUP BY yugoprodeck_id HAVING COUNT(*) > 1
                ORDER BY rows DESC, yugoprodeck_id LIMIT 100
                """
            ), {"game": game_id}).mappings().all()]
            duplicate_print_source_ids = [dict(row) for row in session.execute(text(
                """
                SELECT p.yugioh_id, COUNT(*) AS rows
                FROM prints p JOIN cards c ON c.id=p.card_id
                WHERE c.game_id=:game AND p.yugioh_id IS NOT NULL
                GROUP BY p.yugioh_id HAVING COUNT(*) > 1
                ORDER BY rows DESC, p.yugioh_id LIMIT 100
                """
            ), {"game": game_id}).mappings().all()]
        session.rollback()

    source_card_id_set = set(card_ids)
    source_print_id_set = generated_print_ids
    source_set_code_set = source_set_codes

    missing_cards = sorted(source_card_id_set - neon_card_ids)
    extra_cards = sorted(neon_card_ids - source_card_id_set)
    missing_generated_print_ids = sorted(source_print_id_set - neon_print_ids)
    extra_generated_print_ids = sorted(neon_print_ids - source_print_id_set)
    missing_source_set_codes = sorted(source_set_code_set - neon_set_ids)
    extra_neon_set_codes = sorted(neon_set_ids - source_set_code_set)

    warnings: list[str] = []
    if len(source_set_codes) > len(source_set_names) * 5:
        warnings.append(
            "Current source exposes far more set_code values than set_name values; treating card_sets[].set_code as canonical Set identity likely models individual print codes as Sets."
        )
    if raw_print_rows:
        warnings.append(
            "Current generated YGO print IDs contain card_sets array position (idx+1); this identity is order-dependent and should not be canonical."
        )
    if cards_with_multiple_images:
        warnings.append(
            f"{len(cards_with_multiple_images)} cards expose multiple card_images rows; current connector selects only the first image and requires alternate-art review."
        )
    if duplicate_stable_tuples:
        warnings.append(
            f"{len(duplicate_stable_tuples)} card/set/rarity tuples repeat in the source and need a richer deterministic identity before write."
        )

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": "read_only_yugioh_catalog_health_v2",
        "source": "YGOPRODeck API v7 cardinfo",
        "source_surface": {
            "cards": len(source_card_id_set),
            "duplicate_card_ids": len(set(card_id_duplicates)),
            "cards_without_card_sets": len(cards_without_sets),
            "raw_card_set_rows": raw_print_rows,
            "generated_order_dependent_print_ids": len(source_print_id_set),
            "duplicate_generated_print_ids": len(set(generated_print_id_duplicates)),
            "stable_card_set_rarity_tuples": len(stable_tuple_counts),
            "duplicate_stable_tuple_groups": len(duplicate_stable_tuples),
            "unique_set_codes": len(source_set_codes),
            "unique_set_names": len(source_set_names),
            "unique_set_code_prefixes": len(source_set_prefixes),
            "set_names_with_multiple_codes": len(set_names_multi_codes),
            "cards_with_multiple_images": len(cards_with_multiple_images),
            "total_card_image_rows": total_image_rows,
            "rarity_counts": dict(rarity_counts.most_common()),
            "field_coverage": dict(coverage),
            "banlist_counts": dict(banlist_counts.most_common()),
        },
        "neon": neon,
        "identity_gap": {
            "source_cards_missing_in_neon": len(missing_cards),
            "neon_card_ids_not_in_source": len(extra_cards),
            "current_generated_print_ids_missing_in_neon": len(missing_generated_print_ids),
            "neon_print_ids_not_in_current_generated_surface": len(extra_generated_print_ids),
            "source_set_codes_missing_in_neon": len(missing_source_set_codes),
            "neon_set_source_ids_not_in_source_codes": len(extra_neon_set_codes),
            "duplicate_neon_card_source_ids": len(duplicate_card_source_ids),
            "duplicate_neon_print_source_ids": len(duplicate_print_source_ids),
        },
        "semantic_risk": {
            "set_identity": "card_sets[].set_name must be evaluated as release identity; current connector stores set_code as Set.code/yugioh_id",
            "print_identity": "current connector source ID includes card_sets array index and is order-dependent",
            "images": "current connector chooses first card_images row only",
        },
        "samples": {
            "missing_cards": missing_cards[:100],
            "extra_cards": extra_cards[:100],
            "missing_generated_print_ids": missing_generated_print_ids[:100],
            "extra_generated_print_ids": extra_generated_print_ids[:100],
            "missing_source_set_codes": missing_source_set_codes[:100],
            "extra_neon_set_codes": extra_neon_set_codes[:100],
            "set_names_with_multiple_codes": set_names_multi_codes[:50],
            "duplicate_stable_tuples": duplicate_stable_tuples[:100],
            "cards_with_multiple_images": cards_with_multiple_images[:50],
            "cards_without_sets": cards_without_sets[:100],
            "duplicate_neon_card_source_ids": duplicate_card_source_ids,
            "duplicate_neon_print_source_ids": duplicate_print_source_ids,
        },
        "warnings": warnings,
        "status": "pass_read_only",
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
