from __future__ import annotations

import argparse
import json
import os
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import psycopg2

from app.ingest.connectors.scryfall_mtg_v2 import ScryfallMtgV2Connector
from app.mtg_identity_v2 import clean, finish_values, physical_print_key
from app.scripts.audit_mtg_multilingual_v1 import _all_cards_metadata, _iter_all_cards
from app.scripts.build_mtg_v2_snapshot import _image_rows, _is_paper, _print_attributes

LANGUAGES = ("es", "ja")
EXPECTED_EXISTING = {"es": 1207, "ja": 875}


def _normalize_url(value: str) -> str:
    if value.startswith("postgresql+psycopg2://"):
        return "postgresql://" + value[len("postgresql+psycopg2://"):]
    if value.startswith("postgres://"):
        return "postgresql://" + value[len("postgres://"):]
    return value


def _database_url() -> str:
    value = os.getenv("DATABASE_URL_UNPOOLED") or os.getenv("DATABASE_URL")
    if not value:
        raise RuntimeError("DATABASE_URL[_UNPOOLED] is required")
    return _normalize_url(value)


def _find_game(cur) -> tuple[int, str]:
    cur.execute(
        "SELECT id,slug FROM games WHERE slug IN ('mtg','magic-the-gathering','magic') "
        "ORDER BY CASE slug WHEN 'mtg' THEN 0 ELSE 1 END,id LIMIT 1"
    )
    row = cur.fetchone()
    if not row:
        raise RuntimeError("MTG game row missing")
    return int(row[0]), str(row[1])


def _diff_keys(actual: dict[str, Any] | None, expected: dict[str, Any]) -> list[str]:
    if actual is None:
        return ["<missing-row>"]
    return [key for key, value in expected.items() if actual.get(key) != value]


def run(output: Path) -> dict[str, Any]:
    conn = psycopg2.connect(
        _database_url(),
        connect_timeout=30,
        application_name="dontripit_mtg_multilingual_fidelity_readonly",
    )
    conn.set_session(readonly=True, autocommit=False)
    try:
        with conn.cursor() as cur:
            cur.execute("SHOW transaction_read_only")
            transaction_read_only = str(cur.fetchone()[0]).lower() == "on"
            if not transaction_read_only:
                raise RuntimeError("Read-only production guard failed")

            game_id, game_slug = _find_game(cur)
            cur.execute(
                """
                SELECT p.id,p.print_key,lower(coalesce(p.scryfall_id,'')),p.variant,
                       lower(coalesce(p.language,'')),pa.attributes_json
                FROM prints p
                JOIN cards c ON c.id=p.card_id
                LEFT JOIN print_attributes pa ON pa.print_id=p.id
                WHERE c.game_id=%s AND lower(coalesce(p.language,'')) IN ('es','ja')
                ORDER BY p.id
                """,
                (game_id,),
            )
            existing: dict[str, dict[str, Any]] = {}
            pid_to_key: dict[int, str] = {}
            counts = Counter()
            for pid, pkey, sid, variant, lang, attributes in cur.fetchall():
                key = str(pkey or "")
                if not key:
                    raise AssertionError(f"Existing MTG {lang} print {pid} has no print_key")
                if key in existing:
                    raise AssertionError(f"Duplicate existing print_key: {key}")
                existing[key] = {
                    "print_id": int(pid),
                    "scryfall_id": str(sid),
                    "variant": str(variant),
                    "language": str(lang),
                    "attributes": dict(attributes) if isinstance(attributes, dict) else None,
                }
                pid_to_key[int(pid)] = key
                counts[f"existing_{lang}"] += 1

            actual_existing = {lang: int(counts.get(f"existing_{lang}", 0)) for lang in LANGUAGES}
            if actual_existing != EXPECTED_EXISTING:
                raise AssertionError(f"Existing production ES/JA counts changed: {actual_existing} != {EXPECTED_EXISTING}")

            ids = list(pid_to_key)
            actual_images: dict[int, set[tuple[str, str]]] = defaultdict(set)
            if ids:
                cur.execute(
                    "SELECT print_id,url,coalesce(source,'') FROM print_images WHERE print_id=ANY(%s)",
                    (ids,),
                )
                for pid, url, source in cur.fetchall():
                    actual_images[int(pid)].add((str(url), str(source)))

            actual_localizations: dict[tuple[int, str], list[dict[str, Any]]] = defaultdict(list)
            if ids:
                cur.execute(
                    """
                    SELECT print_id,lower(language),coalesce(source,''),coalesce(external_id,''),
                           card_name,details_json
                    FROM print_localizations WHERE print_id=ANY(%s)
                    """,
                    (ids,),
                )
                for pid, lang, source, external_id, card_name, details in cur.fetchall():
                    actual_localizations[(int(pid), str(lang))].append(
                        {
                            "source": str(source),
                            "external_id": str(external_id),
                            "card_name": card_name,
                            "details": dict(details) if isinstance(details, dict) else {},
                        }
                    )

            connector = ScryfallMtgV2Connector()
            metadata = _all_cards_metadata(connector)
            url = connector._bulk_download_url(metadata)
            if not url:
                raise RuntimeError("Scryfall all_cards download URL missing")

            matched: set[str] = set()
            attribute_mismatches: list[dict[str, Any]] = []
            image_mismatches: list[dict[str, Any]] = []
            localization_mismatches: list[dict[str, Any]] = []

            for raw in _iter_all_cards(connector, url):
                if not _is_paper(raw):
                    continue
                lang = clean(raw.get("lang")).lower()
                if lang not in LANGUAGES:
                    continue
                sid = clean(raw.get("id")).lower()
                expected_images = {
                    (str(row["url"]), str(row.get("source") or "scryfall")) for row in _image_rows(raw)
                }
                expected_name = clean(raw.get("printed_name")) or None
                expected_details = {
                    "printed_type_line": clean(raw.get("printed_type_line")) or None,
                    "printed_text": clean(raw.get("printed_text")) or None,
                    "scryfall_lang": lang,
                }
                for finish in finish_values(raw):
                    pkey = physical_print_key(raw, finish)
                    row = existing.get(pkey)
                    if row is None:
                        continue
                    matched.add(pkey)
                    pid = int(row["print_id"])
                    if row["scryfall_id"] != sid or row["variant"] != finish or row["language"] != lang:
                        raise AssertionError(
                            f"Existing exact identity differs from Scryfall for {pkey}: "
                            f"db={(row['scryfall_id'], row['variant'], row['language'])} "
                            f"source={(sid, finish, lang)}"
                        )

                    expected_attributes = _print_attributes(raw, finish)
                    diff = _diff_keys(row["attributes"], expected_attributes)
                    if diff:
                        counts[f"attribute_mismatch_{lang}"] += 1
                        if len(attribute_mismatches) < 25:
                            attribute_mismatches.append(
                                {"print_id": pid, "print_key": pkey, "language": lang, "fields": diff}
                            )
                    else:
                        counts[f"attribute_exact_{lang}"] += 1

                    actual_scryfall_images = {
                        item for item in actual_images.get(pid, set()) if item[1].startswith("scryfall")
                    }
                    missing_images = sorted(expected_images - actual_scryfall_images)
                    extra_images = sorted(actual_scryfall_images - expected_images)
                    if missing_images or extra_images:
                        counts[f"image_mismatch_{lang}"] += 1
                        if len(image_mismatches) < 25:
                            image_mismatches.append(
                                {
                                    "print_id": pid,
                                    "print_key": pkey,
                                    "language": lang,
                                    "missing": missing_images,
                                    "extra": extra_images,
                                }
                            )
                    else:
                        counts[f"images_exact_{lang}"] += 1

                    loc_rows = actual_localizations.get((pid, lang), [])
                    loc_diff: list[str] = []
                    if len(loc_rows) != 1:
                        loc_diff.append(f"row_count={len(loc_rows)}")
                    else:
                        loc = loc_rows[0]
                        if loc["source"] != "scryfall":
                            loc_diff.append("source")
                        if loc["external_id"].lower() != sid:
                            loc_diff.append("external_id")
                        if loc["card_name"] != expected_name:
                            loc_diff.append("card_name")
                        for key, value in expected_details.items():
                            if loc["details"].get(key) != value:
                                loc_diff.append(f"details.{key}")
                    if loc_diff:
                        counts[f"localization_mismatch_{lang}"] += 1
                        if len(localization_mismatches) < 25:
                            localization_mismatches.append(
                                {"print_id": pid, "print_key": pkey, "language": lang, "fields": loc_diff}
                            )
                    else:
                        counts[f"localization_exact_{lang}"] += 1

            unmatched = sorted(set(existing) - matched)
            if unmatched:
                raise AssertionError(f"Existing MTG ES/JA prints absent from current Scryfall all_cards: {unmatched[:20]}")

            report = {
                "status": "pass",
                "mode": "strict-read-only-existing-mtg-multilingual-fidelity-audit",
                "database_writes": 0,
                "transaction_read_only": transaction_read_only,
                "game": {"id": game_id, "slug": game_slug},
                "scryfall": {
                    "bulk_type": clean(metadata.get("type")) or "all_cards",
                    "bulk_updated_at": metadata.get("updated_at"),
                },
                "existing_prints": len(existing),
                "source_matched_existing_prints": len(matched),
                "counts": dict(counts),
                "attribute_mismatch_samples": attribute_mismatches,
                "image_mismatch_samples": image_mismatches,
                "localization_mismatch_samples": localization_mismatches,
            }
            output.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
            print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
            conn.rollback()
            return report
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit existing MTG ES/JA physical fidelity against Scryfall read-only")
    parser.add_argument("--output", default="/tmp/mtg-multilingual-existing-fidelity.json")
    args = parser.parse_args()
    run(Path(args.output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
