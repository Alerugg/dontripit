from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import psycopg2

from app.mtg_identity_v2 import card_identity_key, clean, finish_values, physical_print_key
from app.scripts import certify_mtg_multilingual_ephemeral_v1 as certification
from app.scripts.preflight_mtg_multilingual_production_v1 import EXPECTED_CARDS, EXPECTED_PRINTS, EXPECTED_REVISION, EXPECTED_SETS, _economics
from app.scripts.prepare_mtg_multilingual_certified_snapshot_v1 import DEFAULT_MANIFEST, load_manifest
from app.scripts.validate_mtg_multilingual_source_fidelity_v1 import validate_source_fidelity_cursor

BATCH_SIZE = 5000
PRODUCTION_CONFIRM = "APPLY_CERTIFIED_SCRYFALL_31891077971"
ADVISORY_LOCK_NAME = "dontripit:mtg-multilingual-production-v1"

_ORIGINAL_TABLE_EXISTS = certification._table_exists


def _normalize_url(value: str) -> str:
    if value.startswith("postgresql+psycopg2://"):
        return "postgresql://" + value[len("postgresql+psycopg2://"):]
    if value.startswith("postgres://"):
        return "postgresql://" + value[len("postgres://"):]
    return value


def _target_url() -> str:
    value = os.getenv("APPLY_DATABASE_URL") or os.getenv("DATABASE_URL_UNPOOLED") or os.getenv("DATABASE_URL")
    if not value:
        raise RuntimeError("APPLY_DATABASE_URL or DATABASE_URL[_UNPOOLED] is required")
    return _normalize_url(value)


def _writer_table_exists(cur, table: str) -> bool:
    # MTG exact Scryfall identity is prints.scryfall_id + prints.variant. The
    # shared historical print_identifiers uniqueness cannot represent finishes.
    if table == "print_identifiers":
        return False
    return _ORIGINAL_TABLE_EXISTS(cur, table)


def _count(cur, sql: str, params: tuple = ()) -> int:
    cur.execute(sql, params)
    row = cur.fetchone()
    return int(row[0] if row else 0)


def _language_counts(cur, game_id: int) -> dict[str, int]:
    cur.execute(
        """
        SELECT lower(coalesce(p.language,'')),count(*)
        FROM prints p JOIN cards c ON c.id=p.card_id
        WHERE c.game_id=%s GROUP BY 1 ORDER BY 1
        """,
        (game_id,),
    )
    return {str(lang): int(count) for lang, count in cur.fetchall()}


def _baseline_digests(cur, game_id: int, baseline_max_print_id: int) -> dict[str, Any]:
    return {
        "sets": certification._digest_query(
            cur, "SELECT * FROM sets WHERE game_id=%s ORDER BY id", (game_id,)
        ),
        "cards": certification._digest_query(
            cur, "SELECT * FROM cards WHERE game_id=%s ORDER BY id", (game_id,)
        ),
        "preexisting_prints": certification._digest_query(
            cur,
            """
            SELECT p.* FROM prints p JOIN cards c ON c.id=p.card_id
            WHERE c.game_id=%s AND p.id<=%s ORDER BY p.id
            """,
            (game_id, baseline_max_print_id),
        ),
        "preexisting_print_attributes": certification._digest_query(
            cur,
            """
            SELECT pa.* FROM print_attributes pa
            JOIN prints p ON p.id=pa.print_id JOIN cards c ON c.id=p.card_id
            WHERE c.game_id=%s AND p.id<=%s ORDER BY pa.print_id
            """,
            (game_id, baseline_max_print_id),
        ),
        "preexisting_print_images": certification._digest_query(
            cur,
            """
            SELECT i.* FROM print_images i
            JOIN prints p ON p.id=i.print_id JOIN cards c ON c.id=p.card_id
            WHERE c.game_id=%s AND p.id<=%s ORDER BY i.id
            """,
            (game_id, baseline_max_print_id),
        ),
        "preexisting_print_identifiers": certification._digest_query(
            cur,
            """
            SELECT pi.* FROM print_identifiers pi
            JOIN prints p ON p.id=pi.print_id JOIN cards c ON c.id=p.card_id
            WHERE c.game_id=%s AND p.id<=%s ORDER BY pi.id
            """,
            (game_id, baseline_max_print_id),
        ),
        "non_target_localizations": certification._digest_query(
            cur,
            """
            SELECT l.* FROM print_localizations l
            JOIN prints p ON p.id=l.print_id JOIN cards c ON c.id=p.card_id
            WHERE c.game_id=%s AND lower(coalesce(l.language,'')) NOT IN ('es','ja')
            ORDER BY l.id
            """,
            (game_id,),
        ),
        "economics": _economics(cur, game_id),
    }


def _assert_baseline(cur, manifest: dict[str, Any]) -> tuple[int, int, dict[str, int], dict[str, Any]]:
    cur.execute("SELECT version_num FROM alembic_version")
    revision = str(cur.fetchone()[0])
    if revision != EXPECTED_REVISION:
        raise AssertionError(f"Unexpected Alembic revision: {revision} != {EXPECTED_REVISION}")

    game_id, game_slug = certification._find_game(cur)
    if game_slug != "mtg":
        raise AssertionError(f"Unexpected MTG game slug: {game_slug}")
    sets = _count(cur, "SELECT count(*) FROM sets WHERE game_id=%s", (game_id,))
    cards = _count(cur, "SELECT count(*) FROM cards WHERE game_id=%s", (game_id,))
    prints = _count(
        cur,
        "SELECT count(*) FROM prints p JOIN cards c ON c.id=p.card_id WHERE c.game_id=%s",
        (game_id,),
    )
    expected = manifest["production_baseline"]
    if sets != EXPECTED_SETS or sets != int(expected["mtg_sets"]):
        raise AssertionError(f"MTG Set baseline moved: {sets}")
    if cards != EXPECTED_CARDS or cards != int(expected["mtg_cards"]):
        raise AssertionError(f"MTG Card baseline moved: {cards}")
    if prints != EXPECTED_PRINTS or prints != int(expected["mtg_prints"]):
        raise AssertionError(f"MTG Print baseline moved: {prints}")

    languages = _language_counts(cur, game_id)
    for lang in ("en", "es", "ja"):
        wanted = int(expected[f"mtg_{lang}_prints"])
        if languages.get(lang, 0) != wanted:
            raise AssertionError(f"MTG {lang} baseline moved: {languages.get(lang, 0)} != {wanted}")

    target_localizations = _count(
        cur,
        """
        SELECT count(*) FROM print_localizations l
        JOIN prints p ON p.id=l.print_id JOIN cards c ON c.id=p.card_id
        WHERE c.game_id=%s AND lower(coalesce(p.language,'')) IN ('es','ja')
        """,
        (game_id,),
    )
    if target_localizations != int(expected["mtg_es_ja_localizations"]):
        raise AssertionError(f"MTG ES/JA localization baseline moved: {target_localizations}")

    missing_scryfall = _count(
        cur,
        """
        SELECT count(*) FROM prints p JOIN cards c ON c.id=p.card_id
        WHERE c.game_id=%s AND lower(coalesce(p.language,'')) IN ('es','ja') AND p.scryfall_id IS NULL
        """,
        (game_id,),
    )
    duplicate_identity = _count(
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
    auxiliary_scryfall = _count(
        cur,
        """
        SELECT count(*) FROM print_identifiers pi
        JOIN prints p ON p.id=pi.print_id JOIN cards c ON c.id=p.card_id
        WHERE c.game_id=%s AND pi.source='scryfall'
        """,
        (game_id,),
    )
    if missing_scryfall or duplicate_identity or auxiliary_scryfall:
        raise AssertionError(
            "MTG exact identity baseline failed: "
            f"missing={missing_scryfall} duplicate={duplicate_identity} auxiliary={auxiliary_scryfall}"
        )

    cur.execute(
        "SELECT COALESCE(MAX(p.id),0) FROM prints p JOIN cards c ON c.id=p.card_id WHERE c.game_id=%s",
        (game_id,),
    )
    baseline_max_print_id = int(cur.fetchone()[0] or 0)
    digests = _baseline_digests(cur, game_id, baseline_max_print_id)
    return game_id, baseline_max_print_id, languages, digests


def _build_and_apply(cur, snapshot: Path, game_id: int, source_version: str | None) -> dict[str, Any]:
    certification.BATCH_SIZE = BATCH_SIZE
    certification._table_exists = _writer_table_exists
    set_ids, oracle_ids, card_keys, print_map, natural_map = certification._load_catalog_maps(cur, game_id)
    counts = Counter()
    seen_natural: set[tuple] = set()
    batch: list[dict] = []

    for raw in certification._iter_snapshot(snapshot):
        if not certification._is_paper(raw):
            continue
        lang = clean(raw.get("lang")).lower()
        if lang not in certification.LANGUAGES:
            continue
        counts[f"source_objects_{lang}"] += 1
        set_code = clean(raw.get("set")).lower()
        set_id = set_ids.get(set_code)
        if set_id is None:
            raise RuntimeError(f"Missing canonical MTG Set: {set_code}")
        oracle_id = clean(raw.get("oracle_id")).lower()
        card_id = oracle_ids.get(oracle_id) if oracle_id else card_keys.get(card_identity_key(raw))
        if card_id is None:
            raise RuntimeError(f"Missing canonical MTG Card for Scryfall {clean(raw.get('id'))}")
        sid = clean(raw.get("id")).lower()
        collector = clean(raw.get("collector_number"))
        if not sid or not collector:
            raise RuntimeError("Scryfall multilingual paper object missing exact physical identity")
        images = certification._image_rows(raw)
        if len(images) > 1:
            counts[f"multi_face_objects_{lang}"] += 1
        for finish in finish_values(raw):
            pkey = physical_print_key(raw, finish)
            is_foil = finish != "nonfoil"
            natural = (set_id, collector, lang, is_foil, finish)
            if natural in seen_natural:
                raise RuntimeError(f"Duplicate source natural identity: {natural}")
            seen_natural.add(natural)
            existing_natural = natural_map.get(natural)
            if existing_natural and (existing_natural[1] != pkey or existing_natural[2] != sid):
                raise RuntimeError(f"Natural identity conflict for {pkey}: {existing_natural}")
            existing_exact = print_map.get(pkey)
            if existing_exact and (existing_exact[1] != natural or existing_exact[2] != sid):
                raise RuntimeError(f"Exact identity conflict for {pkey}: {existing_exact}")
            counts[f"source_prints_{lang}"] += 1
            counts[f"source_finish_{finish}"] += 1
            counts[f"expected_image_rows_{lang}"] += len(images)
            batch.append(
                {
                    "set_id": set_id,
                    "card_id": card_id,
                    "collector_number": collector,
                    "language": lang,
                    "rarity": clean(raw.get("rarity")) or None,
                    "is_foil": is_foil,
                    "variant": finish,
                    "print_key": pkey,
                    "scryfall_id": sid,
                    "natural": natural,
                    "attributes": certification._print_attributes(raw, finish),
                    "images": images,
                    "raw": raw,
                }
            )
            if len(batch) >= BATCH_SIZE:
                counts.update(certification._process_batch(cur, batch, print_map, natural_map, source_version))
                batch.clear()
    if batch:
        counts.update(certification._process_batch(cur, batch, print_map, natural_map, source_version))
    return dict(counts)


def _assert_precommit(
    cur,
    snapshot: Path,
    manifest: dict[str, Any],
    game_id: int,
    baseline_max_print_id: int,
    baseline_languages: dict[str, int],
    baseline_digests: dict[str, Any],
    write_counts: dict[str, Any],
) -> dict[str, Any]:
    final_expected = manifest["certified_final"]
    delta = manifest["certified_delta"]
    final_languages = _language_counts(cur, game_id)
    if final_languages.get("es", 0) != int(final_expected["es_prints"]):
        raise AssertionError(f"Final ES Print count mismatch: {final_languages.get('es', 0)}")
    if final_languages.get("ja", 0) != int(final_expected["ja_prints"]):
        raise AssertionError(f"Final JA Print count mismatch: {final_languages.get('ja', 0)}")
    for lang, count in baseline_languages.items():
        if lang not in ("es", "ja") and final_languages.get(lang, 0) != count:
            raise AssertionError(f"Non-target language {lang} Print count changed")

    required_writes = {
        "prints_created_es": int(delta["new_es_prints"]),
        "prints_created_ja": int(delta["new_ja_prints"]),
        "print_attributes_created": int(delta["new_print_attributes"]),
        "print_images_created": int(delta["new_print_images"]),
        "print_localizations_created": int(delta["new_print_localizations"]),
    }
    for key, expected in required_writes.items():
        if int(write_counts.get(key, 0)) != expected:
            raise AssertionError(f"Unexpected write count {key}: {write_counts.get(key, 0)} != {expected}")
    if int(write_counts.get("print_identifiers_created", 0)) != 0:
        raise AssertionError("Production writer created forbidden Scryfall print_identifiers")
    if int(write_counts.get("print_localizations_updated", 0)) != 0:
        raise AssertionError("Production writer unexpectedly updated pre-existing localizations")

    exact_keys = _count(
        cur,
        """
        SELECT count(*) FROM prints p JOIN cards c ON c.id=p.card_id
        WHERE c.game_id=%s AND lower(coalesce(p.language,'')) IN ('es','ja')
        """,
        (game_id,),
    )
    if exact_keys != int(final_expected["exact_keys"]):
        raise AssertionError(f"Final target exact-key population mismatch: {exact_keys}")

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
    missing_scryfall_ids = _count(
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
    if natural_duplicates or duplicate_scryfall_finish or missing_scryfall_ids or auxiliary_scryfall:
        raise AssertionError(
            "Precommit identity gates failed: "
            f"natural={natural_duplicates} scryfall_finish={duplicate_scryfall_finish} "
            f"missing_scryfall={missing_scryfall_ids} auxiliary={auxiliary_scryfall}"
        )

    fidelity = validate_source_fidelity_cursor(cur, snapshot)
    for lang in ("es", "ja"):
        expected = int(final_expected[f"{lang}_prints"])
        if int(fidelity["counts"].get(f"checked_{lang}", 0)) != expected:
            raise AssertionError(f"Fidelity checked count mismatch for {lang}")
        if int(fidelity["counts"].get(f"exact_{lang}", 0)) != expected:
            raise AssertionError(f"Fidelity exact count mismatch for {lang}")
        if int(fidelity["counts"].get(f"mismatch_{lang}", 0)) != 0:
            raise AssertionError(f"Fidelity mismatch for {lang}")

    post_digests = _baseline_digests(cur, game_id, baseline_max_print_id)
    for key in (
        "sets",
        "cards",
        "preexisting_prints",
        "preexisting_print_attributes",
        "preexisting_print_images",
        "preexisting_print_identifiers",
        "non_target_localizations",
        "economics",
    ):
        if post_digests[key] != baseline_digests[key]:
            raise AssertionError(f"Precommit protected digest changed: {key}")

    total_prints = _count(
        cur,
        "SELECT count(*) FROM prints p JOIN cards c ON c.id=p.card_id WHERE c.game_id=%s",
        (game_id,),
    )
    expected_total = int(manifest["production_baseline"]["mtg_prints"]) + int(delta["new_prints_total"])
    if total_prints != expected_total:
        raise AssertionError(f"Final total MTG Print count mismatch: {total_prints} != {expected_total}")

    target_localizations = _count(
        cur,
        """
        SELECT count(*) FROM print_localizations l
        JOIN prints p ON p.id=l.print_id JOIN cards c ON c.id=p.card_id
        WHERE c.game_id=%s AND lower(coalesce(p.language,'')) IN ('es','ja')
        """,
        (game_id,),
    )
    if target_localizations != int(final_expected["exact_keys"]):
        raise AssertionError(f"Final MTG ES/JA localization count mismatch: {target_localizations}")

    return {
        "status": "pass",
        "final_languages": {"es": final_languages.get("es", 0), "ja": final_languages.get("ja", 0)},
        "total_mtg_prints": total_prints,
        "natural_duplicates": 0,
        "missing_scryfall_ids": 0,
        "duplicate_scryfall_finish_identities": 0,
        "auxiliary_scryfall_print_identifiers": 0,
        "target_localizations": target_localizations,
        "protected_digests_unchanged": True,
        "source_fidelity": fidelity,
    }


def apply(snapshot: Path, output: Path, manifest_path: Path, mode: str, confirm: str | None) -> dict[str, Any]:
    manifest = load_manifest(manifest_path)
    snapshot_sha = certification._sha256(snapshot)
    if snapshot_sha != manifest["normalized_snapshot_sha256"]:
        raise AssertionError("Apply snapshot does not match certified manifest SHA256")

    target_url = _target_url()
    host = (urlparse(target_url).hostname or "").lower()
    is_local = host in {"127.0.0.1", "localhost", "postgres"}
    if mode == "ephemeral-certification":
        if not is_local:
            raise RuntimeError(f"Ephemeral writer certification requires local PostgreSQL, got {host}")
    elif mode == "production":
        if is_local:
            raise RuntimeError("Production writer refuses a local PostgreSQL target")
        if confirm != PRODUCTION_CONFIRM:
            raise RuntimeError("Production confirmation phrase missing or incorrect")
    else:
        raise RuntimeError(f"Unsupported apply mode: {mode}")

    conn = psycopg2.connect(
        target_url,
        connect_timeout=30,
        application_name=f"dontripit_mtg_multilingual_atomic_{mode}",
    )
    conn.autocommit = False
    committed = False
    try:
        with conn.cursor() as cur:
            cur.execute("SET LOCAL statement_timeout='60min'")
            cur.execute("SET LOCAL lock_timeout='30s'")
            cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (ADVISORY_LOCK_NAME,))
            cur.execute("SHOW transaction_read_only")
            if str(cur.fetchone()[0]).lower() == "on":
                raise RuntimeError("Apply transaction unexpectedly read-only")

            game_id, baseline_max_print_id, baseline_languages, baseline_digests = _assert_baseline(cur, manifest)
            write_counts = _build_and_apply(
                cur,
                snapshot,
                game_id,
                str(manifest["scryfall_bulk_updated_at"]),
            )
            precommit = _assert_precommit(
                cur,
                snapshot,
                manifest,
                game_id,
                baseline_max_print_id,
                baseline_languages,
                baseline_digests,
                write_counts,
            )

            report = {
                "status": "pass",
                "mode": mode,
                "atomic_transaction": True,
                "committed": False,
                "manifest": {
                    "certification_run_id": manifest["certification_run_id"],
                    "certification_commit": manifest["certification_commit"],
                    "scryfall_bulk_updated_at": manifest["scryfall_bulk_updated_at"],
                    "snapshot_sha256": snapshot_sha,
                },
                "baseline": {
                    "max_print_id": baseline_max_print_id,
                    "languages": baseline_languages,
                    "protected_digests": baseline_digests,
                },
                "writes": write_counts,
                "precommit": precommit,
            }
            # Materialize evidence before COMMIT; an exception here still rolls
            # back the database. The final committed flag is written after the
            # successful COMMIT below.
            output.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
            conn.commit()
            committed = True
            report["committed"] = True
            output.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
            print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
            return report
    except Exception:
        if not committed:
            conn.rollback()
        raise
    finally:
        certification._table_exists = _ORIGINAL_TABLE_EXISTS
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Atomic certified MTG ES/JA production writer")
    parser.add_argument("--snapshot", required=True)
    parser.add_argument("--output", default="/tmp/mtg-multilingual-production-apply.json")
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    parser.add_argument("--mode", choices=("ephemeral-certification", "production"), required=True)
    parser.add_argument("--confirm")
    args = parser.parse_args()
    apply(Path(args.snapshot), Path(args.output), Path(args.manifest), args.mode, args.confirm)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
