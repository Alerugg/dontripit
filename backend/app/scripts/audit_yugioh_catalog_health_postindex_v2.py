from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import text

from app import db
from app.scripts.audit_yugioh_catalog_health_v2 import EXPECTED, QUARANTINED_ASSIGNMENTS, _scalar


EXPECTED_SEARCH = {
    "search_documents": 0,
    "card_search_profiles": 14479,
    "print_search_profiles": 44226,
    "facet_definitions": 20,
    "active_facets": 19,
}


def _write(path: Path | None, payload: dict) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def run(*, report_path: Path | None = None) -> dict:
    db.init_engine()
    with db.SessionLocal() as session:
        game_id = session.execute(text("SELECT id FROM games WHERE slug='yugioh' LIMIT 1")).scalar_one_or_none()
        if game_id is None:
            raise AssertionError("Yu-Gi-Oh game row is missing")
        game_id = int(game_id)

        canonical_counts = {
            "sets": _scalar(session, "SELECT COUNT(*) FROM sets WHERE game_id=:game", {"game": game_id}),
            "cards": _scalar(session, "SELECT COUNT(*) FROM cards WHERE game_id=:game", {"game": game_id}),
            "prints": _scalar(session, "SELECT COUNT(*) FROM prints p JOIN cards c ON c.id=p.card_id WHERE c.game_id=:game", {"game": game_id}),
            "catalog_releases": _scalar(session, "SELECT COUNT(*) FROM catalog_releases WHERE game_id=:game", {"game": game_id}),
            "print_releases": _scalar(session, "SELECT COUNT(*) FROM print_releases pr JOIN prints p ON p.id=pr.print_id JOIN cards c ON c.id=p.card_id WHERE c.game_id=:game", {"game": game_id}),
            "card_attributes": _scalar(session, "SELECT COUNT(*) FROM card_attributes ca JOIN cards c ON c.id=ca.card_id WHERE c.game_id=:game", {"game": game_id}),
            "print_attributes": _scalar(session, "SELECT COUNT(*) FROM print_attributes pa JOIN prints p ON p.id=pa.print_id JOIN cards c ON c.id=p.card_id WHERE c.game_id=:game", {"game": game_id}),
            "print_images": _scalar(session, "SELECT COUNT(*) FROM print_images pi JOIN prints p ON p.id=pi.print_id JOIN cards c ON c.id=p.card_id WHERE c.game_id=:game", {"game": game_id}),
            "cards_without_prints": _scalar(session, "SELECT COUNT(*) FROM cards c WHERE c.game_id=:game AND NOT EXISTS (SELECT 1 FROM prints p WHERE p.card_id=c.id)", {"game": game_id}),
        }
        for key, expected in EXPECTED.items():
            if key in canonical_counts and canonical_counts[key] != expected:
                raise AssertionError(f"YGO canonical count moved: {key}={canonical_counts[key]} expected={expected}")

        identity_duplicates = {
            "set_codes": _scalar(session, "SELECT COUNT(*) FROM (SELECT code FROM sets WHERE game_id=:game GROUP BY code HAVING COUNT(*)>1) q", {"game": game_id}),
            "card_external_ids": _scalar(session, "SELECT COUNT(*) FROM (SELECT yugoprodeck_id FROM cards WHERE game_id=:game AND yugoprodeck_id IS NOT NULL GROUP BY yugoprodeck_id HAVING COUNT(*)>1) q", {"game": game_id}),
            "print_external_ids": _scalar(session, "SELECT COUNT(*) FROM (SELECT p.yugioh_id FROM prints p JOIN cards c ON c.id=p.card_id WHERE c.game_id=:game AND p.yugioh_id IS NOT NULL GROUP BY p.yugioh_id HAVING COUNT(*)>1) q", {"game": game_id}),
            "print_keys": _scalar(session, "SELECT COUNT(*) FROM (SELECT p.print_key FROM prints p JOIN cards c ON c.id=p.card_id WHERE c.game_id=:game AND p.print_key IS NOT NULL GROUP BY p.print_key HAVING COUNT(*)>1) q", {"game": game_id}),
            "shared_print_tuple": _scalar(session, "SELECT COUNT(*) FROM (SELECT p.set_id,p.collector_number,p.language,p.is_foil,p.variant FROM prints p JOIN cards c ON c.id=p.card_id WHERE c.game_id=:game GROUP BY p.set_id,p.collector_number,p.language,p.is_foil,p.variant HAVING COUNT(*)>1) q", {"game": game_id}),
        }
        if any(identity_duplicates.values()):
            raise AssertionError(f"Canonical YGO duplicate identity detected: {identity_duplicates}")

        linkage = {
            "prints_without_image": _scalar(session, "SELECT COUNT(*) FROM prints p JOIN cards c ON c.id=p.card_id WHERE c.game_id=:game AND NOT EXISTS (SELECT 1 FROM print_images pi WHERE pi.print_id=p.id)", {"game": game_id}),
            "prints_without_release": _scalar(session, "SELECT COUNT(*) FROM prints p JOIN cards c ON c.id=p.card_id WHERE c.game_id=:game AND NOT EXISTS (SELECT 1 FROM print_releases pr WHERE pr.print_id=p.id)", {"game": game_id}),
            "prints_with_multiple_releases": _scalar(session, "SELECT COUNT(*) FROM (SELECT p.id FROM prints p JOIN cards c ON c.id=p.card_id JOIN print_releases pr ON pr.print_id=p.id WHERE c.game_id=:game GROUP BY p.id HAVING COUNT(*)>1) q", {"game": game_id}),
        }
        if any(linkage.values()):
            raise AssertionError(f"YGO Print linkage moved: {linkage}")

        edge_cases = {
            "fallback_rows": _scalar(session, "SELECT COUNT(*) FROM print_attributes pa JOIN prints p ON p.id=pa.print_id JOIN cards c ON c.id=p.card_id WHERE c.game_id=:game AND pa.attributes_json->>'family_resolution'='same_release_unanimous_fallback'", {"game": game_id}),
            "canonical_unknown_rarity_prints": _scalar(session, "SELECT COUNT(*) FROM prints p JOIN cards c ON c.id=p.card_id WHERE c.game_id=:game AND p.rarity='Unknown'", {"game": game_id}),
            "non_en_prints": _scalar(session, "SELECT COUNT(*) FROM prints p JOIN cards c ON c.id=p.card_id WHERE c.game_id=:game AND COALESCE(p.language,'')<>'en'", {"game": game_id}),
        }
        if edge_cases["fallback_rows"] != EXPECTED["fallback_rows"]:
            raise AssertionError(f"YGO fallback row count moved: {edge_cases['fallback_rows']}")
        if edge_cases["canonical_unknown_rarity_prints"] != EXPECTED["noisy_rarity_source_rows"]:
            raise AssertionError(f"YGO Unknown rarity count moved: {edge_cases['canonical_unknown_rarity_prints']}")
        if edge_cases["non_en_prints"] != 0:
            raise AssertionError(f"Unexpected non-English YGO Prints entered certified surface: {edge_cases['non_en_prints']}")

        quarantined_present = []
        for external_id, collector in QUARANTINED_ASSIGNMENTS:
            rows = _scalar(session, "SELECT COUNT(*) FROM prints p JOIN cards c ON c.id=p.card_id WHERE c.game_id=:game AND c.yugoprodeck_id=:card AND p.collector_number=:collector", {"game": game_id, "card": external_id, "collector": collector})
            if rows:
                quarantined_present.append({"card": external_id, "collector": collector, "rows": rows})
        if quarantined_present:
            raise AssertionError(f"Quarantined YGO source conflicts entered canonical catalog: {quarantined_present}")

        search_state = {
            "search_documents": _scalar(session, "SELECT COUNT(*) FROM search_documents WHERE game_id=:game", {"game": game_id}),
            "card_search_profiles": _scalar(session, "SELECT COUNT(*) FROM card_search_profiles WHERE game_id=:game", {"game": game_id}),
            "print_search_profiles": _scalar(session, "SELECT COUNT(*) FROM print_search_profiles WHERE game_id=:game", {"game": game_id}),
            "facet_definitions": _scalar(session, "SELECT COUNT(*) FROM facet_definitions WHERE game_id=:game", {"game": game_id}),
            "active_facets": _scalar(session, "SELECT COUNT(*) FROM facet_definitions WHERE game_id=:game AND active=true", {"game": game_id}),
        }
        if search_state != EXPECTED_SEARCH:
            raise AssertionError(f"YGO Search V2 post-index state moved: {search_state} != {EXPECTED_SEARCH}")

        search_integrity = {
            "cards_missing_profile": _scalar(session, "SELECT COUNT(*) FROM cards c WHERE c.game_id=:game AND NOT EXISTS (SELECT 1 FROM card_search_profiles csp WHERE csp.card_id=c.id AND csp.game_id=:game)", {"game": game_id}),
            "prints_missing_profile": _scalar(session, "SELECT COUNT(*) FROM prints p JOIN cards c ON c.id=p.card_id WHERE c.game_id=:game AND NOT EXISTS (SELECT 1 FROM print_search_profiles psp WHERE psp.print_id=p.id AND psp.game_id=:game)", {"game": game_id}),
            "card_profiles_wrong_game": _scalar(session, "SELECT COUNT(*) FROM card_search_profiles csp JOIN cards c ON c.id=csp.card_id WHERE csp.game_id=:game AND c.game_id<>:game", {"game": game_id}),
            "print_profiles_wrong_game": _scalar(session, "SELECT COUNT(*) FROM print_search_profiles psp JOIN prints p ON p.id=psp.print_id JOIN cards c ON c.id=p.card_id WHERE psp.game_id=:game AND c.game_id<>:game", {"game": game_id}),
        }
        if any(search_integrity.values()):
            raise AssertionError(f"YGO Search V2 profile integrity failed: {search_integrity}")

        database_bytes = _scalar(session, "SELECT pg_database_size(current_database())")
        session.rollback()

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": "read_only_yugioh_catalog_health_v2_post_index",
        "status": "pass",
        "canonical_counts": canonical_counts,
        "identity_duplicates": identity_duplicates,
        "linkage": linkage,
        "edge_cases": {**edge_cases, "quarantined_assignments_present": quarantined_present},
        "search_v2": search_state,
        "search_integrity": search_integrity,
        "source_honesty": {
            "cards_without_physical_print_evidence": EXPECTED["cards_without_prints"],
            "unknown_rarity_prints": EXPECTED["noisy_rarity_source_rows"],
            "exact_print_art_mapping_claimed": False,
            "finish_claimed": False,
            "edition_claimed": False,
        },
        "database": {
            "bytes": database_bytes,
            "mib": round(database_bytes / 1024 / 1024, 2),
            "limit_mib": 512,
            "remaining_mib": round((512 * 1024 * 1024 - database_bytes) / 1024 / 1024, 2),
        },
        "database_writes": 0,
    }
    _write(report_path, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report-path", type=Path, default=None)
    args = parser.parse_args()
    run(report_path=args.report_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
