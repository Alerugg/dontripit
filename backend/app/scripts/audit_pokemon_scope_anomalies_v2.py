from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import text

from app import db


_SAFE_IDENT = re.compile(r"^[a-z_][a-z0-9_]*$")
_LEGACY_SV_ID = re.compile(r"^sv(?P<set_no>\d+)-(?P<collector>\d+)$", re.IGNORECASE)


def _write(path: Path | None, payload: dict) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def _normalize_legacy_tcgdex_id(value: str | None) -> str | None:
    if not value:
        return None
    match = _LEGACY_SV_ID.fullmatch(value.strip())
    if not match:
        return None
    return f"sv{int(match.group('set_no')):02d}-{int(match.group('collector')):03d}"


def _foreign_key_usage(session, *, foreign_table: str, target_ids: list[int]) -> list[dict]:
    if not target_ids:
        return []
    relationships = session.execute(text(
        """
        SELECT
          tc.table_name,
          kcu.column_name,
          ccu.table_name AS foreign_table_name,
          ccu.column_name AS foreign_column_name,
          rc.delete_rule
        FROM information_schema.table_constraints tc
        JOIN information_schema.key_column_usage kcu
          ON tc.constraint_name=kcu.constraint_name
         AND tc.constraint_schema=kcu.constraint_schema
        JOIN information_schema.constraint_column_usage ccu
          ON ccu.constraint_name=tc.constraint_name
         AND ccu.constraint_schema=tc.constraint_schema
        JOIN information_schema.referential_constraints rc
          ON rc.constraint_name=tc.constraint_name
         AND rc.constraint_schema=tc.constraint_schema
        WHERE tc.constraint_type='FOREIGN KEY'
          AND tc.table_schema='public'
          AND ccu.table_schema='public'
          AND ccu.table_name=:foreign_table
          AND ccu.column_name='id'
        ORDER BY tc.table_name, kcu.column_name
        """
    ), {"foreign_table": foreign_table}).mappings().all()

    evidence: list[dict] = []
    for row in relationships:
        table_name = str(row["table_name"])
        column_name = str(row["column_name"])
        if not _SAFE_IDENT.match(table_name) or not _SAFE_IDENT.match(column_name):
            raise AssertionError(f"Unsafe metadata identifier: {table_name}.{column_name}")
        counts = session.execute(text(
            f'SELECT "{column_name}" AS target_id, COUNT(*)::int AS rows '
            f'FROM "{table_name}" WHERE "{column_name}" = ANY(:target_ids) '
            f'GROUP BY "{column_name}" ORDER BY "{column_name}"'
        ), {"target_ids": target_ids}).mappings().all()
        for count_row in counts:
            evidence.append({
                "target_table": foreign_table,
                "target_id": int(count_row["target_id"]),
                "referencing_table": table_name,
                "referencing_column": column_name,
                "rows": int(count_row["rows"]),
                "delete_rule": row["delete_rule"],
            })
    return evidence


def run(*, report_path: Path | None = None) -> dict:
    """Explain Pokémon rows that exist outside the certified V2 projection.

    This audit is intentionally read-only. It does not infer that a row should
    be deleted merely because it is absent from enrichment/Search V2.
    """

    db.init_engine()
    with db.SessionLocal() as session:
        game_id = session.execute(text("SELECT id FROM games WHERE slug='pokemon'" )).scalar_one()

        cards = [dict(row) for row in session.execute(text(
            """
            SELECT
              c.id,
              c.name,
              c.card_key,
              c.tcgdex_id,
              c.created_at,
              (ca.card_id IS NOT NULL) AS has_card_attributes,
              (csp.card_id IS NOT NULL) AS has_search_profile,
              COUNT(p.id)::int AS print_count
            FROM cards c
            LEFT JOIN card_attributes ca ON ca.card_id=c.id
            LEFT JOIN card_search_profiles csp ON csp.card_id=c.id
            LEFT JOIN prints p ON p.card_id=c.id
            WHERE c.game_id=:game_id
              AND (ca.card_id IS NULL OR csp.card_id IS NULL)
            GROUP BY c.id, c.name, c.card_key, c.tcgdex_id, c.created_at,
                     ca.card_id, csp.card_id
            ORDER BY c.id
            """
        ), {"game_id": game_id}).mappings().all()]

        prints = [dict(row) for row in session.execute(text(
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
              p.collector_number,
              p.language,
              p.rarity,
              p.variant,
              p.is_foil,
              p.created_at,
              (pa.print_id IS NOT NULL) AS has_print_attributes,
              (psp.print_id IS NOT NULL) AS has_search_profile,
              (pi.id IS NOT NULL) AS has_image
            FROM prints p
            JOIN cards c ON c.id=p.card_id
            JOIN sets s ON s.id=p.set_id
            LEFT JOIN print_attributes pa ON pa.print_id=p.id
            LEFT JOIN print_search_profiles psp ON psp.print_id=p.id
            LEFT JOIN LATERAL (
              SELECT id FROM print_images x WHERE x.print_id=p.id LIMIT 1
            ) pi ON TRUE
            WHERE c.game_id=:game_id
              AND (pa.print_id IS NULL OR psp.print_id IS NULL)
            ORDER BY p.id
            """
        ), {"game_id": game_id}).mappings().all()]

        card_ids = [int(row["id"]) for row in cards]
        print_ids = [int(row["id"]) for row in prints]

        card_candidates = [dict(row) for row in session.execute(text(
            """
            WITH anomalous AS (
              SELECT c.id, c.name, c.tcgdex_id
              FROM cards c
              WHERE c.id = ANY(:card_ids)
            )
            SELECT DISTINCT
              a.id AS anomaly_card_id,
              c.id AS candidate_card_id,
              c.name AS candidate_name,
              c.card_key AS candidate_card_key,
              c.tcgdex_id AS candidate_tcgdex_id,
              (ca.card_id IS NOT NULL) AS has_card_attributes,
              (csp.card_id IS NOT NULL) AS has_search_profile,
              CASE
                WHEN c.tcgdex_id=a.tcgdex_id THEN 'same_tcgdex_id'
                WHEN lower(c.name)=lower(a.name) THEN 'same_name'
                ELSE 'other'
              END AS match_reason
            FROM anomalous a
            JOIN cards c
              ON c.game_id=:game_id
             AND c.id<>a.id
             AND (
               (a.tcgdex_id IS NOT NULL AND c.tcgdex_id=a.tcgdex_id)
               OR lower(c.name)=lower(a.name)
             )
            LEFT JOIN card_attributes ca ON ca.card_id=c.id
            LEFT JOIN card_search_profiles csp ON csp.card_id=c.id
            ORDER BY a.id, match_reason, c.id
            """
        ), {"game_id": game_id, "card_ids": card_ids or [-1]}).mappings().all()]

        print_candidates = [dict(row) for row in session.execute(text(
            """
            WITH anomalous AS (
              SELECT
                p.id,
                p.tcgdex_id,
                p.collector_number,
                s.code AS set_code
              FROM prints p
              JOIN sets s ON s.id=p.set_id
              WHERE p.id = ANY(:print_ids)
            )
            SELECT DISTINCT
              a.id AS anomaly_print_id,
              p.id AS candidate_print_id,
              c.id AS candidate_card_id,
              c.name AS candidate_card_name,
              c.tcgdex_id AS candidate_card_tcgdex_id,
              p.tcgdex_id AS candidate_print_tcgdex_id,
              p.print_key AS candidate_print_key,
              s.code AS candidate_set_code,
              s.name AS candidate_set_name,
              p.collector_number AS candidate_collector_number,
              p.variant AS candidate_variant,
              (pa.print_id IS NOT NULL) AS has_print_attributes,
              (psp.print_id IS NOT NULL) AS has_search_profile,
              CASE
                WHEN a.tcgdex_id IS NOT NULL AND p.tcgdex_id=a.tcgdex_id THEN 'same_print_tcgdex_id'
                WHEN a.tcgdex_id IS NOT NULL AND c.tcgdex_id=a.tcgdex_id THEN 'card_matches_legacy_print_tcgdex_id'
                WHEN lower(s.code)=lower(a.set_code)
                     AND ltrim(COALESCE(p.collector_number,''), '0')=ltrim(COALESCE(a.collector_number,''), '0')
                  THEN 'same_set_and_collector'
                ELSE 'other'
              END AS match_reason
            FROM anomalous a
            JOIN prints p ON p.id<>a.id
            JOIN cards c ON c.id=p.card_id AND c.game_id=:game_id
            JOIN sets s ON s.id=p.set_id
            LEFT JOIN print_attributes pa ON pa.print_id=p.id
            LEFT JOIN print_search_profiles psp ON psp.print_id=p.id
            WHERE
              (a.tcgdex_id IS NOT NULL AND (p.tcgdex_id=a.tcgdex_id OR c.tcgdex_id=a.tcgdex_id))
              OR (
                lower(s.code)=lower(a.set_code)
                AND ltrim(COALESCE(p.collector_number,''), '0')=ltrim(COALESCE(a.collector_number,''), '0')
              )
            ORDER BY a.id, match_reason, p.id
            """
        ), {"game_id": game_id, "print_ids": print_ids or [-1]}).mappings().all()]

        legacy_alias_requests: list[dict] = []
        for row in cards:
            normalized = _normalize_legacy_tcgdex_id(row.get("tcgdex_id"))
            if normalized:
                legacy_alias_requests.append({
                    "kind": "card",
                    "legacy_row_id": int(row["id"]),
                    "legacy_tcgdex_id": row.get("tcgdex_id"),
                    "normalized_tcgdex_id": normalized,
                })
        for row in prints:
            normalized = _normalize_legacy_tcgdex_id(row.get("print_tcgdex_id"))
            if normalized:
                legacy_alias_requests.append({
                    "kind": "print",
                    "legacy_row_id": int(row["id"]),
                    "legacy_tcgdex_id": row.get("print_tcgdex_id"),
                    "normalized_tcgdex_id": normalized,
                })
        normalized_ids = sorted({row["normalized_tcgdex_id"] for row in legacy_alias_requests})

        normalized_rows = [dict(row) for row in session.execute(text(
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
              p.language,
              p.rarity,
              p.variant,
              (ca.card_id IS NOT NULL) AS has_card_attributes,
              (pa.print_id IS NOT NULL) AS has_print_attributes,
              (csp.card_id IS NOT NULL) AS has_card_search_profile,
              (psp.print_id IS NOT NULL) AS has_print_search_profile
            FROM cards c
            LEFT JOIN prints p ON p.card_id=c.id
            LEFT JOIN sets s ON s.id=p.set_id
            LEFT JOIN card_attributes ca ON ca.card_id=c.id
            LEFT JOIN print_attributes pa ON pa.print_id=p.id
            LEFT JOIN card_search_profiles csp ON csp.card_id=c.id
            LEFT JOIN print_search_profiles psp ON psp.print_id=p.id
            WHERE c.game_id=:game_id
              AND (c.tcgdex_id = ANY(:normalized_ids) OR p.tcgdex_id = ANY(:normalized_ids))
            ORDER BY c.tcgdex_id, p.id
            """
        ), {"game_id": game_id, "normalized_ids": normalized_ids or ["__none__"]}).mappings().all()]

        totals = dict(session.execute(text(
            """
            SELECT
              (SELECT COUNT(*) FROM cards c WHERE c.game_id=:game_id) AS cards,
              (SELECT COUNT(*) FROM prints p JOIN cards c ON c.id=p.card_id WHERE c.game_id=:game_id) AS prints,
              (SELECT COUNT(*) FROM card_attributes ca JOIN cards c ON c.id=ca.card_id WHERE c.game_id=:game_id) AS card_attributes,
              (SELECT COUNT(*) FROM print_attributes pa JOIN prints p ON p.id=pa.print_id JOIN cards c ON c.id=p.card_id WHERE c.game_id=:game_id) AS print_attributes,
              (SELECT COUNT(*) FROM card_search_profiles csp WHERE csp.game_id=:game_id) AS card_search_profiles,
              (SELECT COUNT(*) FROM print_search_profiles psp WHERE psp.game_id=:game_id) AS print_search_profiles
            """
        ), {"game_id": game_id}).mappings().one())

        card_fk_usage = _foreign_key_usage(session, foreign_table="cards", target_ids=card_ids)
        print_fk_usage = _foreign_key_usage(session, foreign_table="prints", target_ids=print_ids)
        session.rollback()

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": "read_only_pokemon_scope_anomaly_audit_v2",
        "status": "pass",
        "totals": {key: int(value or 0) for key, value in totals.items()},
        "cards_outside_certified_projection": cards,
        "prints_outside_certified_projection": prints,
        "canonical_card_candidates": card_candidates,
        "canonical_print_candidates": print_candidates,
        "legacy_tcgdex_normalization": {
            "rule": "sv<set>-<collector> -> sv<set:02>-<collector:03>; evidence probe only, not deletion authority",
            "requests": legacy_alias_requests,
            "matched_canonical_rows": normalized_rows,
        },
        "foreign_key_usage": {
            "cards": card_fk_usage,
            "prints": print_fk_usage,
        },
        "counts": {
            "cards_outside_certified_projection": len(cards),
            "prints_outside_certified_projection": len(prints),
            "canonical_card_candidates": len(card_candidates),
            "canonical_print_candidates": len(print_candidates),
            "normalized_alias_requests": len(legacy_alias_requests),
            "normalized_alias_rows": len(normalized_rows),
            "card_fk_usage_rows": len(card_fk_usage),
            "print_fk_usage_rows": len(print_fk_usage),
        },
        "database_writes": 0,
        "interpretation": "Rows are reported for evidence only; absence from enrichment/Search V2 is not itself deletion authority.",
    }
    _write(report_path, report)
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report-path", type=Path, default=None)
    args = parser.parse_args()
    run(report_path=args.report_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
