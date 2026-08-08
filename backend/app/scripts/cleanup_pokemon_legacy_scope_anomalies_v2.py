from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import text

from app import db


TARGET_CARD_ID = 366
TARGET_PRINT_IDS = (1, 512, 513)
_SAFE_IDENT = re.compile(r"^[a-z_][a-z0-9_]*$")

EXPECTED_CARD = {
    "id": 366,
    "name": "Pineco",
    "card_key": None,
    "tcgdex_id": "sv1-1",
}
EXPECTED_PRINTS = {
    1: {
        "card_id": 1,
        "card_name": "Pikachu",
        "card_tcgdex_id": "base1-58",
        "print_tcgdex_id": None,
        "print_key": None,
        "set_code": "svi",
        "set_name": "Scarlet & Violet",
        "collector_number": "001",
    },
    512: {
        "card_id": 366,
        "card_name": "Pineco",
        "card_tcgdex_id": "sv1-1",
        "print_tcgdex_id": "sv1-1",
        "print_key": None,
        "set_code": "svi",
        "set_name": "Scarlet & Violet",
        "collector_number": "1",
    },
    513: {
        "card_id": 1,
        "card_name": "Pikachu",
        "card_tcgdex_id": "base1-58",
        "print_tcgdex_id": "sv1-62",
        "print_key": None,
        "set_code": "svi",
        "set_name": "Scarlet & Violet",
        "collector_number": "62",
    },
}


def _write(path: Path | None, payload: dict) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def _count_snapshot(session, game_id: int) -> dict[str, int]:
    row = session.execute(text(
        """
        SELECT
          (SELECT COUNT(*) FROM cards c WHERE c.game_id=:game_id) AS cards,
          (SELECT COUNT(*) FROM prints p JOIN cards c ON c.id=p.card_id WHERE c.game_id=:game_id) AS prints,
          (SELECT COUNT(*) FROM card_attributes ca JOIN cards c ON c.id=ca.card_id WHERE c.game_id=:game_id) AS card_attributes,
          (SELECT COUNT(*) FROM print_attributes pa JOIN prints p ON p.id=pa.print_id JOIN cards c ON c.id=p.card_id WHERE c.game_id=:game_id) AS print_attributes,
          (SELECT COUNT(*) FROM card_search_profiles csp WHERE csp.game_id=:game_id) AS card_search_profiles,
          (SELECT COUNT(*) FROM print_search_profiles psp WHERE psp.game_id=:game_id) AS print_search_profiles
        """
    ), {"game_id": game_id}).mappings().one()
    return {key: int(value or 0) for key, value in row.items()}


def _nonzero_column_usage(session, *, column_name: str, target_ids: list[int]) -> list[dict]:
    rows = session.execute(text(
        """
        SELECT table_name
        FROM information_schema.columns
        WHERE table_schema='public' AND column_name=:column_name
        ORDER BY table_name
        """
    ), {"column_name": column_name}).scalars().all()

    evidence: list[dict] = []
    for table_name in rows:
        table_name = str(table_name)
        if not _SAFE_IDENT.fullmatch(table_name) or not _SAFE_IDENT.fullmatch(column_name):
            raise AssertionError(f"Unsafe metadata identifier: {table_name}.{column_name}")
        count = int(session.execute(text(
            f'SELECT COUNT(*) FROM "{table_name}" WHERE "{column_name}" = ANY(:target_ids)'
        ), {"target_ids": target_ids}).scalar_one())
        if count:
            evidence.append({"table": table_name, "column": column_name, "rows": count})
    return evidence


def _assert_target_signatures(session) -> tuple[dict, list[dict]]:
    card = dict(session.execute(text(
        """
        SELECT id, name, card_key, tcgdex_id
        FROM cards
        WHERE id=:card_id
        FOR UPDATE
        """
    ), {"card_id": TARGET_CARD_ID}).mappings().one())
    if card != EXPECTED_CARD:
        raise AssertionError(f"Legacy Card signature changed; refusing cleanup: {card}")

    print_rows = [dict(row) for row in session.execute(text(
        """
        SELECT
          p.id,
          p.card_id,
          c.name AS card_name,
          c.tcgdex_id AS card_tcgdex_id,
          p.tcgdex_id AS print_tcgdex_id,
          p.print_key,
          s.code AS set_code,
          s.name AS set_name,
          p.collector_number
        FROM prints p
        JOIN cards c ON c.id=p.card_id
        JOIN sets s ON s.id=p.set_id
        WHERE p.id = ANY(:print_ids)
        ORDER BY p.id
        FOR UPDATE OF p
        """
    ), {"print_ids": list(TARGET_PRINT_IDS)}).mappings().all()]
    if len(print_rows) != len(TARGET_PRINT_IDS):
        raise AssertionError(f"Expected {len(TARGET_PRINT_IDS)} legacy Prints, found {len(print_rows)}")
    for row in print_rows:
        print_id = int(row.pop("id"))
        expected = EXPECTED_PRINTS.get(print_id)
        if expected is None or row != expected:
            raise AssertionError(f"Legacy Print {print_id} signature changed; refusing cleanup: {row}")
        row["id"] = print_id
    return card, sorted(print_rows, key=lambda item: item["id"])


def _assert_canonical_replacements(session, game_id: int) -> list[dict]:
    rows = [dict(row) for row in session.execute(text(
        """
        SELECT
          c.id AS card_id,
          c.name AS card_name,
          c.card_key,
          c.tcgdex_id AS card_tcgdex_id,
          p.id AS print_id,
          p.print_key,
          p.tcgdex_id AS print_tcgdex_id,
          s.code AS set_code,
          s.name AS set_name,
          p.collector_number,
          (ca.card_id IS NOT NULL) AS has_card_attributes,
          (pa.print_id IS NOT NULL) AS has_print_attributes,
          (csp.card_id IS NOT NULL) AS has_card_search_profile,
          (psp.print_id IS NOT NULL) AS has_print_search_profile
        FROM cards c
        JOIN prints p ON p.card_id=c.id
        JOIN sets s ON s.id=p.set_id
        JOIN card_attributes ca ON ca.card_id=c.id
        JOIN print_attributes pa ON pa.print_id=p.id
        JOIN card_search_profiles csp ON csp.card_id=c.id
        JOIN print_search_profiles psp ON psp.print_id=p.id
        WHERE c.game_id=:game_id
          AND p.tcgdex_id IN ('sv01-001', 'sv01-062')
        ORDER BY p.tcgdex_id
        """
    ), {"game_id": game_id}).mappings().all()]

    by_source = {row["print_tcgdex_id"]: row for row in rows}
    pineco = by_source.get("sv01-001")
    tatsugiri = by_source.get("sv01-062")
    if not pineco or pineco["card_name"] != "Pineco" or pineco["set_code"] != "sv01" or pineco["collector_number"] != "001":
        raise AssertionError(f"Canonical Pineco replacement missing or changed: {pineco}")
    if not tatsugiri or tatsugiri["card_name"] != "Tatsugiri" or tatsugiri["set_code"] != "sv01" or tatsugiri["collector_number"] != "062":
        raise AssertionError(f"Canonical Tatsugiri replacement missing or changed: {tatsugiri}")
    for row in (pineco, tatsugiri):
        if not row["card_key"] or not row["print_key"]:
            raise AssertionError(f"Canonical replacement lacks V2 identity key: {row}")
        if not all((row["has_card_attributes"], row["has_print_attributes"], row["has_card_search_profile"], row["has_print_search_profile"])):
            raise AssertionError(f"Canonical replacement is outside certified V2 projection: {row}")
    return [pineco, tatsugiri]


def run(*, report_path: Path | None = None) -> dict:
    db.init_engine()
    session = db.SessionLocal()
    try:
        game_id = int(session.execute(text("SELECT id FROM games WHERE slug='pokemon'")).scalar_one())
        before = _count_snapshot(session, game_id)
        expected_before = {
            "cards": 21066,
            "prints": 33760,
            "card_attributes": 21065,
            "print_attributes": 33757,
            "card_search_profiles": 21065,
            "print_search_profiles": 33757,
        }
        if before != expected_before:
            raise AssertionError(f"Pokémon baseline changed; refusing cleanup: {before}")

        card_signature, print_signatures = _assert_target_signatures(session)
        canonical_replacements = _assert_canonical_replacements(session, game_id)

        card_usage = _nonzero_column_usage(session, column_name="card_id", target_ids=[TARGET_CARD_ID])
        print_usage = _nonzero_column_usage(session, column_name="print_id", target_ids=list(TARGET_PRINT_IDS))
        expected_card_usage = [{"table": "prints", "column": "card_id", "rows": 1}]
        expected_print_usage = [
            {"table": "print_identifiers", "column": "print_id", "rows": 3},
            {"table": "print_images", "column": "print_id", "rows": 3},
        ]
        if card_usage != expected_card_usage:
            raise AssertionError(f"Unexpected Card dependencies; refusing cleanup: {card_usage}")
        if print_usage != expected_print_usage:
            raise AssertionError(f"Unexpected Print dependencies; refusing cleanup: {print_usage}")

        deleted_identifiers = session.execute(text(
            "DELETE FROM print_identifiers WHERE print_id = ANY(:print_ids)"
        ), {"print_ids": list(TARGET_PRINT_IDS)}).rowcount
        deleted_images = session.execute(text(
            "DELETE FROM print_images WHERE print_id = ANY(:print_ids)"
        ), {"print_ids": list(TARGET_PRINT_IDS)}).rowcount
        deleted_prints = session.execute(text(
            "DELETE FROM prints WHERE id = ANY(:print_ids)"
        ), {"print_ids": list(TARGET_PRINT_IDS)}).rowcount
        deleted_cards = session.execute(text(
            "DELETE FROM cards WHERE id=:card_id"
        ), {"card_id": TARGET_CARD_ID}).rowcount

        deleted = {
            "print_identifiers": int(deleted_identifiers or 0),
            "print_images": int(deleted_images or 0),
            "prints": int(deleted_prints or 0),
            "cards": int(deleted_cards or 0),
        }
        expected_deleted = {"print_identifiers": 3, "print_images": 3, "prints": 3, "cards": 1}
        if deleted != expected_deleted:
            raise AssertionError(f"Unexpected deletion counts; rolling back: {deleted}")

        after = _count_snapshot(session, game_id)
        expected_after = {
            "cards": 21065,
            "prints": 33757,
            "card_attributes": 21065,
            "print_attributes": 33757,
            "card_search_profiles": 21065,
            "print_search_profiles": 33757,
        }
        if after != expected_after:
            raise AssertionError(f"Post-cleanup certified projection mismatch; rolling back: {after}")

        remaining_targets = int(session.execute(text(
            """
            SELECT
              (SELECT COUNT(*) FROM cards WHERE id=:card_id)
              + (SELECT COUNT(*) FROM prints WHERE id = ANY(:print_ids))
              + (SELECT COUNT(*) FROM print_identifiers WHERE print_id = ANY(:print_ids))
              + (SELECT COUNT(*) FROM print_images WHERE print_id = ANY(:print_ids))
            """
        ), {"card_id": TARGET_CARD_ID, "print_ids": list(TARGET_PRINT_IDS)}).scalar_one())
        if remaining_targets != 0:
            raise AssertionError(f"Legacy target rows remain after cleanup: {remaining_targets}")

        session.commit()
        status = "pass"
        report = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "mode": "transactional_pokemon_legacy_scope_cleanup_v2",
            "status": status,
            "before": before,
            "legacy_card_signature": card_signature,
            "legacy_print_signatures": print_signatures,
            "canonical_replacements": canonical_replacements,
            "dependency_preflight": {"card_id": card_usage, "print_id": print_usage},
            "deleted": deleted,
            "after": after,
            "remaining_target_rows": remaining_targets,
            "certified_projection_row_writes": 0,
            "pricing_or_product_rows_touched": 0,
        }
        _write(report_path, report)
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
        return report
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report-path", type=Path, default=None)
    args = parser.parse_args()
    run(report_path=args.report_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
