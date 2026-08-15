from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

import psycopg2

from app.mtg_identity_v2 import clean, finish_values, physical_print_key
from app.scripts.build_mtg_v2_snapshot import _image_rows, _is_paper, _print_attributes

LANGUAGES = ("es", "ja")
BATCH_SIZE = 5000


def _iter_snapshot(path: Path) -> Iterable[dict]:
    with path.open("r", encoding="utf-8") as handle:
        for line_number, raw in enumerate(handle, start=1):
            text = raw.strip()
            if not text:
                continue
            value = json.loads(text)
            if not isinstance(value, dict):
                raise RuntimeError(f"Snapshot row {line_number} is not an object")
            yield value


def _find_game(cur) -> int:
    cur.execute(
        "SELECT id FROM games WHERE slug IN ('mtg','magic-the-gathering','magic') "
        "ORDER BY CASE slug WHEN 'mtg' THEN 0 ELSE 1 END,id LIMIT 1"
    )
    row = cur.fetchone()
    if not row:
        raise RuntimeError("MTG game row missing")
    return int(row[0])


def _expected_item(raw: dict, finish: str) -> dict[str, Any]:
    lang = clean(raw.get("lang")).lower()
    sid = clean(raw.get("id")).lower()
    images = _image_rows(raw)
    return {
        "print_key": physical_print_key(raw, finish),
        "scryfall_id": sid,
        "variant": finish,
        "language": lang,
        "attributes": _print_attributes(raw, finish),
        "images": [
            {
                "url": str(image["url"]),
                "source": str(image.get("source") or "scryfall"),
                "is_primary": index == 0,
            }
            for index, image in enumerate(images)
        ],
        "localization": {
            "source": "scryfall",
            "external_id": sid,
            "card_name": clean(raw.get("printed_name")) or None,
            "details": {
                "printed_type_line": clean(raw.get("printed_type_line")) or None,
                "printed_text": clean(raw.get("printed_text")) or None,
                "scryfall_lang": lang,
            },
        },
    }


def _validate_batch(cur, game_id: int, batch: list[dict[str, Any]], counts: Counter, samples: list[dict[str, Any]]) -> None:
    keys = [str(item["print_key"]) for item in batch]
    expected_by_key = {str(item["print_key"]): item for item in batch}
    if len(expected_by_key) != len(batch):
        raise AssertionError("Duplicate exact Print key inside fidelity validation batch")

    cur.execute(
        """
        SELECT p.id,p.print_key,lower(coalesce(p.scryfall_id,'')),p.variant,
               lower(coalesce(p.language,'')),pa.attributes_json
        FROM prints p
        JOIN cards c ON c.id=p.card_id
        LEFT JOIN print_attributes pa ON pa.print_id=p.id
        WHERE c.game_id=%s AND p.print_key=ANY(%s)
        """,
        (game_id, keys),
    )
    actual_by_key: dict[str, dict[str, Any]] = {}
    for pid, pkey, sid, variant, lang, attributes in cur.fetchall():
        key = str(pkey or "")
        if key in actual_by_key:
            raise AssertionError(f"Multiple print/attribute rows resolve exact key {key}")
        actual_by_key[key] = {
            "print_id": int(pid),
            "scryfall_id": str(sid),
            "variant": str(variant),
            "language": str(lang),
            "attributes": dict(attributes) if isinstance(attributes, dict) else None,
        }

    missing_keys = sorted(set(expected_by_key) - set(actual_by_key))
    if missing_keys:
        raise AssertionError(f"Fidelity validation missing exact Print keys: {missing_keys[:10]}")

    ids = [int(actual_by_key[key]["print_id"]) for key in keys]
    cur.execute(
        """
        SELECT print_id,lower(language),coalesce(source,''),coalesce(external_id,''),card_name,details_json
        FROM print_localizations WHERE print_id=ANY(%s)
        """,
        (ids,),
    )
    localizations: dict[tuple[int, str], list[dict[str, Any]]] = defaultdict(list)
    for pid, lang, source, external_id, card_name, details in cur.fetchall():
        localizations[(int(pid), str(lang))].append(
            {
                "source": str(source),
                "external_id": str(external_id),
                "card_name": card_name,
                "details": dict(details) if isinstance(details, dict) else {},
            }
        )

    cur.execute(
        "SELECT print_id,url,coalesce(source,''),is_primary FROM print_images WHERE print_id=ANY(%s)",
        (ids,),
    )
    images: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for pid, url, source, is_primary in cur.fetchall():
        images[int(pid)].append(
            {"url": str(url), "source": str(source), "is_primary": bool(is_primary)}
        )

    for key, expected in expected_by_key.items():
        actual = actual_by_key[key]
        pid = int(actual["print_id"])
        lang = str(expected["language"])
        problems: list[str] = []

        if actual["scryfall_id"] != expected["scryfall_id"]:
            problems.append("scryfall_id")
        if actual["variant"] != expected["variant"]:
            problems.append("variant")
        if actual["language"] != lang:
            problems.append("language")

        attributes = actual["attributes"]
        if attributes is None:
            problems.append("print_attributes.row")
        else:
            for field, value in expected["attributes"].items():
                if attributes.get(field) != value:
                    problems.append(f"print_attributes.{field}")

        loc_rows = localizations.get((pid, lang), [])
        if len(loc_rows) != 1:
            problems.append(f"print_localizations.row_count={len(loc_rows)}")
        else:
            loc = loc_rows[0]
            expected_loc = expected["localization"]
            if loc["source"] != expected_loc["source"]:
                problems.append("print_localizations.source")
            if loc["external_id"].lower() != expected_loc["external_id"]:
                problems.append("print_localizations.external_id")
            if loc["card_name"] != expected_loc["card_name"]:
                problems.append("print_localizations.card_name")
            for field, value in expected_loc["details"].items():
                if loc["details"].get(field) != value:
                    problems.append(f"print_localizations.details.{field}")

        expected_images = {
            (row["url"], row["source"], bool(row["is_primary"])) for row in expected["images"]
        }
        actual_images = {
            (row["url"], row["source"], bool(row["is_primary"]))
            for row in images.get(pid, [])
            if row["source"].startswith("scryfall")
        }
        if actual_images != expected_images:
            problems.append("print_images.scryfall_exact_set")

        counts[f"checked_{lang}"] += 1
        if problems:
            counts[f"mismatch_{lang}"] += 1
            if len(samples) < 25:
                samples.append(
                    {
                        "print_id": pid,
                        "print_key": key,
                        "language": lang,
                        "problems": problems,
                        "expected_images": sorted(expected_images),
                        "actual_images": sorted(actual_images),
                    }
                )
        else:
            counts[f"exact_{lang}"] += 1


def validate_source_fidelity_cursor(cur, snapshot: Path) -> dict[str, Any]:
    """Validate exact source fidelity using the caller's transaction/cursor.

    This variant is required by the production writer so uncommitted inserts can
    be certified before COMMIT. It performs reads only and never commits,
    rolls back, or changes transaction boundaries.
    """
    game_id = _find_game(cur)
    counts = Counter()
    samples: list[dict[str, Any]] = []
    batch: list[dict[str, Any]] = []
    for raw in _iter_snapshot(snapshot):
        if not _is_paper(raw):
            continue
        lang = clean(raw.get("lang")).lower()
        if lang not in LANGUAGES:
            continue
        for finish in finish_values(raw):
            batch.append(_expected_item(raw, finish))
            if len(batch) >= BATCH_SIZE:
                _validate_batch(cur, game_id, batch, counts, samples)
                batch.clear()
    if batch:
        _validate_batch(cur, game_id, batch, counts, samples)

    total_mismatches = sum(int(counts.get(f"mismatch_{lang}", 0)) for lang in LANGUAGES)
    report = {
        "status": "pass" if total_mismatches == 0 else "fail",
        "counts": dict(counts),
        "mismatch_samples": samples,
        "exact_fields": [
            "prints.scryfall_id",
            "prints.variant",
            "prints.language",
            "print_attributes.expected_scryfall_fields",
            "print_localizations.source",
            "print_localizations.external_id",
            "print_localizations.card_name",
            "print_localizations.details.printed_type_line",
            "print_localizations.details.printed_text",
            "print_localizations.details.scryfall_lang",
            "print_images.scryfall_url_source_primary_set",
        ],
    }
    if total_mismatches:
        raise AssertionError(
            f"MTG multilingual source fidelity mismatches={total_mismatches}: {samples[:5]}"
        )
    return report


def validate_source_fidelity(target_url: str, snapshot: Path) -> dict[str, Any]:
    conn = psycopg2.connect(
        target_url,
        connect_timeout=30,
        application_name="dontripit_mtg_multilingual_source_fidelity",
    )
    conn.set_session(readonly=True, autocommit=False)
    try:
        with conn.cursor() as cur:
            report = validate_source_fidelity_cursor(cur, snapshot)
            conn.rollback()
            return report
    finally:
        conn.close()
