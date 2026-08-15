from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

import psycopg2

from app.scripts import certify_mtg_multilingual_ephemeral_v1 as certification
from app.scripts.apply_mtg_multilingual_production_v1 import _baseline_digests, _language_counts
from app.scripts.prepare_mtg_multilingual_certified_snapshot_v1 import DEFAULT_MANIFEST, load_manifest
from app.scripts.validate_mtg_multilingual_source_fidelity_v1 import validate_source_fidelity_cursor


def _normalize_url(value: str) -> str:
    if value.startswith("postgresql+psycopg2://"):
        return "postgresql://" + value[len("postgresql+psycopg2://"):]
    if value.startswith("postgres://"):
        return "postgresql://" + value[len("postgres://"):]
    return value


def _database_url() -> str:
    value = os.getenv("POSTFLIGHT_DATABASE_URL") or os.getenv("DATABASE_URL_UNPOOLED") or os.getenv("DATABASE_URL")
    if not value:
        raise RuntimeError("POSTFLIGHT_DATABASE_URL or DATABASE_URL[_UNPOOLED] is required")
    return _normalize_url(value)


def _count(cur, sql: str, params: tuple = ()) -> int:
    cur.execute(sql, params)
    row = cur.fetchone()
    return int(row[0] if row else 0)


def run(snapshot: Path, apply_report_path: Path, output: Path, manifest_path: Path = DEFAULT_MANIFEST) -> dict[str, Any]:
    manifest = load_manifest(manifest_path)
    apply_report = json.loads(apply_report_path.read_text(encoding="utf-8"))
    if apply_report.get("status") != "pass" or apply_report.get("committed") is not True:
        raise AssertionError("Apply evidence is not a committed PASS")
    if apply_report.get("manifest", {}).get("snapshot_sha256") != manifest["normalized_snapshot_sha256"]:
        raise AssertionError("Apply evidence snapshot differs from certified manifest")
    if certification._sha256(snapshot) != manifest["normalized_snapshot_sha256"]:
        raise AssertionError("Postflight snapshot differs from certified manifest")

    conn = psycopg2.connect(
        _database_url(),
        connect_timeout=30,
        application_name="dontripit_mtg_multilingual_independent_postflight",
    )
    conn.set_session(readonly=True, autocommit=False)
    try:
        with conn.cursor() as cur:
            cur.execute("SHOW transaction_read_only")
            read_only = str(cur.fetchone()[0]).lower() == "on"
            if not read_only:
                raise RuntimeError("Independent postflight is not read-only")

            game_id, game_slug = certification._find_game(cur)
            if game_slug != "mtg":
                raise AssertionError(f"Unexpected MTG game slug: {game_slug}")
            baseline_max_print_id = int(apply_report["baseline"]["max_print_id"])
            protected_before = apply_report["baseline"]["protected_digests"]
            protected_after = _baseline_digests(cur, game_id, baseline_max_print_id)
            if protected_after != protected_before:
                changed = [key for key in protected_before if protected_before.get(key) != protected_after.get(key)]
                raise AssertionError(f"Protected postflight digests changed: {changed}")

            final = manifest["certified_final"]
            delta = manifest["certified_delta"]
            languages = _language_counts(cur, game_id)
            if languages.get("es", 0) != int(final["es_prints"]):
                raise AssertionError("Postflight ES count mismatch")
            if languages.get("ja", 0) != int(final["ja_prints"]):
                raise AssertionError("Postflight JA count mismatch")
            for lang, count in apply_report["baseline"]["languages"].items():
                if lang not in ("es", "ja") and languages.get(lang, 0) != int(count):
                    raise AssertionError(f"Postflight non-target language changed: {lang}")

            total_prints = _count(
                cur,
                "SELECT count(*) FROM prints p JOIN cards c ON c.id=p.card_id WHERE c.game_id=%s",
                (game_id,),
            )
            expected_total = int(manifest["production_baseline"]["mtg_prints"]) + int(delta["new_prints_total"])
            if total_prints != expected_total:
                raise AssertionError(f"Postflight total MTG Print mismatch: {total_prints}")

            target_localizations = _count(
                cur,
                """
                SELECT count(*) FROM print_localizations l
                JOIN prints p ON p.id=l.print_id JOIN cards c ON c.id=p.card_id
                WHERE c.game_id=%s AND lower(coalesce(p.language,'')) IN ('es','ja')
                """,
                (game_id,),
            )
            natural_duplicates = _count(
                cur,
                """
                SELECT count(*) FROM (
                  SELECT p.set_id,p.collector_number,lower(coalesce(p.language,'')),p.is_foil,p.variant,count(*)
                  FROM prints p JOIN cards c ON c.id=p.card_id
                  WHERE c.game_id=%s AND lower(coalesce(p.language,'')) IN ('es','ja')
                  GROUP BY p.set_id,p.collector_number,lower(coalesce(p.language,'')),p.is_foil,p.variant
                  HAVING count(*)>1
                ) d
                """,
                (game_id,),
            )
            duplicate_scryfall_finish = _count(
                cur,
                """
                SELECT count(*) FROM (
                  SELECT p.scryfall_id,p.variant,count(*)
                  FROM prints p JOIN cards c ON c.id=p.card_id
                  WHERE c.game_id=%s AND lower(coalesce(p.language,'')) IN ('es','ja')
                  GROUP BY p.scryfall_id,p.variant HAVING count(*)>1
                ) d
                """,
                (game_id,),
            )
            missing_scryfall = _count(
                cur,
                """
                SELECT count(*) FROM prints p JOIN cards c ON c.id=p.card_id
                WHERE c.game_id=%s AND lower(coalesce(p.language,'')) IN ('es','ja') AND p.scryfall_id IS NULL
                """,
                (game_id,),
            )
            auxiliary_scryfall = _count(
                cur,
                """
                SELECT count(*) FROM print_identifiers pi
                JOIN prints p ON p.id=pi.print_id JOIN cards c ON c.id=p.card_id
                WHERE c.game_id=%s AND pi.source='scryfall'
                """,
                (game_id,),
            )
            if target_localizations != int(final["exact_keys"]):
                raise AssertionError(f"Postflight localization mismatch: {target_localizations}")
            if natural_duplicates or duplicate_scryfall_finish or missing_scryfall or auxiliary_scryfall:
                raise AssertionError(
                    "Postflight identity failure: "
                    f"natural={natural_duplicates} scryfall_finish={duplicate_scryfall_finish} "
                    f"missing={missing_scryfall} auxiliary={auxiliary_scryfall}"
                )

            fidelity = validate_source_fidelity_cursor(cur, snapshot)
            for lang in ("es", "ja"):
                expected = int(final[f"{lang}_prints"])
                if int(fidelity["counts"].get(f"exact_{lang}", 0)) != expected:
                    raise AssertionError(f"Postflight source fidelity failed for {lang}")
                if int(fidelity["counts"].get(f"mismatch_{lang}", 0)) != 0:
                    raise AssertionError(f"Postflight source mismatch for {lang}")

            report = {
                "status": "pass",
                "mode": "independent-read-only-postflight",
                "transaction_read_only": True,
                "database_writes": 0,
                "manifest": {
                    "certification_run_id": manifest["certification_run_id"],
                    "snapshot_sha256": manifest["normalized_snapshot_sha256"],
                },
                "final": {
                    "languages": {"es": languages.get("es", 0), "ja": languages.get("ja", 0)},
                    "total_mtg_prints": total_prints,
                    "target_localizations": target_localizations,
                    "natural_duplicates": 0,
                    "duplicate_scryfall_finish_identities": 0,
                    "missing_scryfall_ids": 0,
                    "auxiliary_scryfall_print_identifiers": 0,
                    "protected_digests_unchanged": True,
                    "source_fidelity": fidelity,
                },
            }
            output.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
            print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
            conn.rollback()
            return report
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Independent read-only MTG ES/JA production postflight")
    parser.add_argument("--snapshot", required=True)
    parser.add_argument("--apply-report", required=True)
    parser.add_argument("--output", default="/tmp/mtg-multilingual-production-postflight.json")
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    args = parser.parse_args()
    run(Path(args.snapshot), Path(args.apply_report), Path(args.output), Path(args.manifest))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
