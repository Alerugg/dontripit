from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

import psycopg2
from psycopg2.extras import Json, execute_values

from app.ingest.connectors.scryfall_mtg_v2 import ScryfallMtgV2Connector
from app.mtg_identity_v2 import card_identity_key, clean, finish_values, physical_print_key
from app.scripts.audit_mtg_multilingual_v1 import _all_cards_metadata, _iter_all_cards
from app.scripts.build_mtg_v2_snapshot import _image_rows, _is_paper, _print_attributes

LANGUAGES = ("es", "ja")
EXPECTED_FINAL = {"es": 89405, "ja": 104735}
EXPECTED_NEW = {"es": 88198, "ja": 103860}
BATCH_SIZE = 1000


def _normalize_url(value: str) -> str:
    if value.startswith("postgresql+psycopg2://"):
        return "postgresql://" + value[len("postgresql+psycopg2://"):]
    if value.startswith("postgres://"):
        return "postgresql://" + value[len("postgres://"):]
    return value


def _required_urls() -> tuple[str, str]:
    production = os.getenv("PRODUCTION_DATABASE_URL_UNPOOLED") or os.getenv("PRODUCTION_DATABASE_URL")
    target = os.getenv("EPHEMERAL_DATABASE_URL")
    if not production or not target:
        raise RuntimeError("PRODUCTION_DATABASE_URL[_UNPOOLED] and EPHEMERAL_DATABASE_URL are required")
    production, target = _normalize_url(production), _normalize_url(target)
    if production == target:
        raise RuntimeError("Safety guard: production and ephemeral URLs are identical")
    return production, target


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _table_exists(cur, table: str) -> bool:
    cur.execute("SELECT to_regclass(%s) IS NOT NULL", (f"public.{table}",))
    return bool(cur.fetchone()[0])


def _columns(cur, table: str) -> list[str]:
    cur.execute(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema='public' AND table_name=%s AND is_generated='NEVER'
        ORDER BY ordinal_position
        """,
        (table,),
    )
    return [str(row[0]) for row in cur.fetchall()]


def _reset_sequence(cur, table: str) -> None:
    cur.execute("SELECT pg_get_serial_sequence(%s, 'id')", (table,))
    row = cur.fetchone()
    sequence = row[0] if row else None
    if not sequence:
        return
    cur.execute(f'SELECT COALESCE(MAX(id), 0) FROM "{table}"')
    maximum = int(cur.fetchone()[0] or 0)
    if maximum:
        cur.execute("SELECT setval(%s, %s, true)", (sequence, maximum))


def _copy_filtered(src, dst, table: str, where_sql: str = "TRUE", params: tuple = ()) -> dict[str, Any]:
    if not _table_exists(src, table) or not _table_exists(dst, table):
        return {"table": table, "rows": 0, "skipped": True}
    source_columns = _columns(src, table)
    target_columns = set(_columns(dst, table))
    columns = [column for column in source_columns if column in target_columns]
    if not columns:
        raise RuntimeError(f"No common columns for {table}")
    quoted = ",".join(f'"{column}"' for column in columns)
    where = src.mogrify(where_sql, params).decode("utf-8") if params else where_sql
    src.execute(f'SELECT COUNT(*) FROM "{table}" WHERE {where}')
    count = int(src.fetchone()[0])
    fd, tmp_name = tempfile.mkstemp(prefix=f"dontripit-{table}-", suffix=".csv")
    os.close(fd)
    path = Path(tmp_name)
    try:
        with path.open("w", encoding="utf-8", newline="") as out:
            src.copy_expert(
                f'COPY (SELECT {quoted} FROM "{table}" WHERE {where} ORDER BY id) TO STDOUT WITH (FORMAT CSV)',
                out,
            )
        digest = _sha256(path)
        if count:
            with path.open("r", encoding="utf-8", newline="") as inp:
                dst.copy_expert(
                    f'COPY "{table}" ({quoted}) FROM STDIN WITH (FORMAT CSV)',
                    inp,
                )
        return {"table": table, "rows": count, "sha256": digest, "columns": columns}
    finally:
        path.unlink(missing_ok=True)


def _digest_query(cur, sql: str, params: tuple = ()) -> dict[str, Any]:
    cur.execute(sql, params)
    digest = hashlib.sha256()
    count = 0
    while True:
        rows = cur.fetchmany(5000)
        if not rows:
            break
        for row in rows:
            digest.update(json.dumps(row, ensure_ascii=False, default=str, separators=(",", ":")).encode("utf-8"))
            digest.update(b"\n")
            count += 1
    return {"rows": count, "sha256": digest.hexdigest()}


def _find_game(cur) -> tuple[int, str]:
    cur.execute(
        "SELECT id,slug FROM games WHERE slug IN ('mtg','magic-the-gathering','magic') ORDER BY CASE slug WHEN 'mtg' THEN 0 ELSE 1 END,id LIMIT 1"
    )
    row = cur.fetchone()
    if not row:
        raise RuntimeError("MTG game row missing")
    return int(row[0]), str(row[1])


def _state(cur, game_id: int) -> dict[str, Any]:
    cur.execute("SELECT pg_database_size(current_database())")
    db_bytes = int(cur.fetchone()[0])
    cur.execute(
        """
        SELECT lower(coalesce(p.language,'')), count(*)
        FROM prints p JOIN cards c ON c.id=p.card_id
        WHERE c.game_id=%s GROUP BY lower(coalesce(p.language,'')) ORDER BY 1
        """,
        (game_id,),
    )
    languages = {str(lang): int(count) for lang, count in cur.fetchall()}
    en_digest = _digest_query(
        cur,
        """
        SELECT p.id,p.set_id,p.card_id,p.collector_number,p.language,p.rarity,p.is_foil,p.variant,p.print_key,p.scryfall_id
        FROM prints p JOIN cards c ON c.id=p.card_id
        WHERE c.game_id=%s AND lower(coalesce(p.language,''))='en' ORDER BY p.id
        """,
        (game_id,),
    )
    cur.execute("SELECT count(*) FROM sets WHERE game_id=%s", (game_id,))
    sets = int(cur.fetchone()[0])
    cur.execute("SELECT count(*) FROM cards WHERE game_id=%s", (game_id,))
    cards = int(cur.fetchone()[0])
    cur.execute("SELECT count(*) FROM prints p JOIN cards c ON c.id=p.card_id WHERE c.game_id=%s", (game_id,))
    prints = int(cur.fetchone()[0])
    economics: dict[str, Any] = {}
    if _table_exists(cur, "prices"):
        economics["prices"] = _digest_query(cur, "SELECT * FROM prices WHERE game_id=%s ORDER BY id", (game_id,))
    if _table_exists(cur, "price_snapshots"):
        economics["price_snapshots"] = _digest_query(
            cur,
            """
            SELECT * FROM price_snapshots WHERE
              (entity_type='card' AND entity_id IN (SELECT id FROM cards WHERE game_id=%s)) OR
              (entity_type='print' AND entity_id IN (SELECT p.id FROM prints p JOIN cards c ON c.id=p.card_id WHERE c.game_id=%s))
            ORDER BY id
            """,
            (game_id, game_id),
        )
    if _table_exists(cur, "price_daily_ohlc"):
        economics["price_daily_ohlc"] = _digest_query(
            cur,
            """
            SELECT * FROM price_daily_ohlc WHERE
              (entity_type='card' AND entity_id IN (SELECT id FROM cards WHERE game_id=%s)) OR
              (entity_type='print' AND entity_id IN (SELECT p.id FROM prints p JOIN cards c ON c.id=p.card_id WHERE c.game_id=%s))
            ORDER BY id
            """,
            (game_id, game_id),
        )
    return {
        "database_bytes": db_bytes,
        "sets": sets,
        "cards": cards,
        "prints": prints,
        "languages": languages,
        "en_digest": en_digest,
        "economics": economics,
    }


def seed_ephemeral(production_url: str, target_url: str) -> dict[str, Any]:
    source = psycopg2.connect(production_url, connect_timeout=30, application_name="dontripit_mtg_multilingual_seed_readonly")
    target = psycopg2.connect(target_url, connect_timeout=30, application_name="dontripit_mtg_multilingual_ephemeral")
    source.set_session(readonly=True, autocommit=False)
    target.set_session(readonly=False, autocommit=False)
    copied: list[dict[str, Any]] = []
    try:
        with source.cursor() as src, target.cursor() as dst:
            src.execute("SHOW transaction_read_only")
            if str(src.fetchone()[0]).lower() != "on":
                raise RuntimeError("Production read-only guard failed")
            game_id, slug = _find_game(src)
            dst.execute("SELECT count(*) FROM games")
            if int(dst.fetchone()[0]) != 0:
                raise RuntimeError("Ephemeral target already has catalog data")

            copied.append(_copy_filtered(src, dst, "games", "id=%s", (game_id,)))
            copied.append(_copy_filtered(src, dst, "price_sources"))
            copied.append(_copy_filtered(src, dst, "sets", "game_id=%s", (game_id,)))
            copied.append(_copy_filtered(src, dst, "cards", "game_id=%s", (game_id,)))
            copied.append(_copy_filtered(src, dst, "card_attributes", "card_id IN (SELECT id FROM cards WHERE game_id=%s)", (game_id,)))
            copied.append(_copy_filtered(src, dst, "card_identifiers", "card_id IN (SELECT id FROM cards WHERE game_id=%s)", (game_id,)))
            copied.append(_copy_filtered(src, dst, "prints", "card_id IN (SELECT id FROM cards WHERE game_id=%s)", (game_id,)))
            for table in ("print_attributes", "print_identifiers", "print_images", "print_localizations"):
                copied.append(
                    _copy_filtered(
                        src,
                        dst,
                        table,
                        "print_id IN (SELECT p.id FROM prints p JOIN cards c ON c.id=p.card_id WHERE c.game_id=%s)",
                        (game_id,),
                    )
                )
            copied.append(_copy_filtered(src, dst, "prices", "game_id=%s", (game_id,)))
            for table in ("price_snapshots", "price_daily_ohlc"):
                copied.append(
                    _copy_filtered(
                        src,
                        dst,
                        table,
                        "(entity_type='card' AND entity_id IN (SELECT id FROM cards WHERE game_id=%s)) OR (entity_type='print' AND entity_id IN (SELECT p.id FROM prints p JOIN cards c ON c.id=p.card_id WHERE c.game_id=%s))",
                        (game_id, game_id),
                    )
                )
            for table in ("games", "price_sources", "sets", "cards", "card_attributes", "card_identifiers", "prints", "print_attributes", "print_identifiers", "print_images", "print_localizations", "prices", "price_snapshots", "price_daily_ohlc"):
                if _table_exists(dst, table):
                    _reset_sequence(dst, table)
            target.commit()
            baseline = _state(dst, game_id)
            baseline.update({"game_id": game_id, "game_slug": slug, "production_transaction_read_only": True, "copied": copied})
            source.rollback()
            return baseline
    except Exception:
        target.rollback()
        raise
    finally:
        source.close()
        target.close()


def capture_snapshot(path: Path, meta_path: Path) -> dict[str, Any]:
    connector = ScryfallMtgV2Connector()
    metadata = _all_cards_metadata(connector)
    url = connector._bulk_download_url(metadata)
    if not url:
        raise RuntimeError("Scryfall all_cards download URL missing")
    counts = Counter()
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for card in _iter_all_cards(connector, url):
            counts["all_objects"] += 1
            handle.write(json.dumps(card, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
            if _is_paper(card):
                counts["paper_objects"] += 1
                lang = clean(card.get("lang")).lower()
                if lang in LANGUAGES:
                    counts[f"paper_{lang}_objects"] += 1
                    counts[f"paper_{lang}_prints"] += len(finish_values(card))
    report = {
        "bulk_type": clean(metadata.get("type")) or "all_cards",
        "bulk_updated_at": metadata.get("updated_at"),
        "snapshot_sha256": _sha256(path),
        "normalized_jsonl": True,
        "counts": dict(counts),
    }
    meta_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    return report


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


def _load_catalog_maps(cur, game_id: int):
    cur.execute("SELECT id,lower(code) FROM sets WHERE game_id=%s", (game_id,))
    set_ids = {str(code): int(id_) for id_, code in cur.fetchall()}
    cur.execute("SELECT id,lower(coalesce(oracle_id,'')),coalesce(card_key,'') FROM cards WHERE game_id=%s", (game_id,))
    oracle_ids: dict[str, int] = {}
    card_keys: dict[str, int] = {}
    for id_, oracle_id, card_key in cur.fetchall():
        if oracle_id:
            oracle_ids[str(oracle_id)] = int(id_)
        if card_key:
            card_keys[str(card_key)] = int(id_)
    cur.execute(
        """
        SELECT p.id,p.print_key,p.set_id,p.collector_number,lower(coalesce(p.language,'')),p.is_foil,p.variant,lower(coalesce(p.scryfall_id,''))
        FROM prints p JOIN cards c ON c.id=p.card_id WHERE c.game_id=%s
        """,
        (game_id,),
    )
    print_map: dict[str, tuple[int, tuple, str]] = {}
    natural_map: dict[tuple, tuple[int, str, str]] = {}
    for pid, pkey, set_id, collector, lang, is_foil, variant, sid in cur.fetchall():
        natural = (int(set_id), str(collector), str(lang), bool(is_foil), str(variant))
        if pkey:
            print_map[str(pkey)] = (int(pid), natural, str(sid))
        natural_map[natural] = (int(pid), str(pkey or ""), str(sid))
    return set_ids, oracle_ids, card_keys, print_map, natural_map


def _process_batch(cur, batch: list[dict], print_map: dict, natural_map: dict, source_version: str | None) -> Counter:
    counts = Counter()
    new_print_rows = []
    for item in batch:
        pkey = item["print_key"]
        if pkey not in print_map:
            new_print_rows.append(
                (
                    item["set_id"], item["card_id"], item["collector_number"], item["language"], item["rarity"],
                    item["is_foil"], item["variant"], pkey, item["scryfall_id"],
                )
            )
    if new_print_rows:
        execute_values(
            cur,
            """
            INSERT INTO prints (set_id,card_id,collector_number,language,rarity,is_foil,variant,print_key,scryfall_id)
            VALUES %s ON CONFLICT (print_key) DO NOTHING
            """,
            new_print_rows,
            page_size=BATCH_SIZE,
        )

    keys = [item["print_key"] for item in batch]
    cur.execute(
        "SELECT id,print_key,set_id,collector_number,lower(coalesce(language,'')),is_foil,variant,lower(coalesce(scryfall_id,'')) FROM prints WHERE print_key = ANY(%s)",
        (keys,),
    )
    resolved = {str(row[1]): row for row in cur.fetchall()}
    if len(resolved) != len(set(keys)):
        missing = sorted(set(keys) - set(resolved))[:10]
        raise RuntimeError(f"Missing inserted/resolved Print keys: {missing}")
    for item in batch:
        row = resolved[item["print_key"]]
        pid = int(row[0])
        natural = (int(row[2]), str(row[3]), str(row[4]), bool(row[5]), str(row[6]))
        if natural != item["natural"] or str(row[7]) != item["scryfall_id"]:
            raise RuntimeError(f"Exact Print identity mismatch after insert: {item['print_key']}")
        if item["print_key"] not in print_map:
            counts[f"prints_created_{item['language']}"] += 1
        print_map[item["print_key"]] = (pid, natural, item["scryfall_id"])
        natural_map[natural] = (pid, item["print_key"], item["scryfall_id"])
        item["print_id"] = pid

    ids = [int(item["print_id"]) for item in batch]

    if _table_exists(cur, "print_attributes"):
        cur.execute("SELECT print_id FROM print_attributes WHERE print_id=ANY(%s)", (ids,))
        existing = {int(row[0]) for row in cur.fetchall()}
        rows = [
            (item["print_id"], Json(item["attributes"]), "scryfall", source_version)
            for item in batch if int(item["print_id"]) not in existing
        ]
        if rows:
            execute_values(cur, "INSERT INTO print_attributes (print_id,attributes_json,source,source_version) VALUES %s", rows, page_size=BATCH_SIZE)
            counts["print_attributes_created"] += len(rows)

    if _table_exists(cur, "print_identifiers"):
        cur.execute("SELECT print_id,external_id FROM print_identifiers WHERE print_id=ANY(%s) AND source='scryfall'", (ids,))
        existing = {int(pid): str(ext) for pid, ext in cur.fetchall()}
        rows = []
        for item in batch:
            pid = int(item["print_id"])
            current = existing.get(pid)
            if current is not None and current.lower() != item["scryfall_id"]:
                raise RuntimeError(f"Scryfall identifier conflict for print {pid}: {current} != {item['scryfall_id']}")
            if current is None:
                rows.append((pid, "scryfall", item["scryfall_id"]))
        if rows:
            execute_values(cur, "INSERT INTO print_identifiers (print_id,source,external_id) VALUES %s", rows, page_size=BATCH_SIZE)
            counts["print_identifiers_created"] += len(rows)

    if _table_exists(cur, "print_images"):
        cur.execute("SELECT print_id,url,coalesce(source,'') FROM print_images WHERE print_id=ANY(%s)", (ids,))
        existing = {(int(pid), str(url), str(source)) for pid, url, source in cur.fetchall()}
        rows = []
        for item in batch:
            pid = int(item["print_id"])
            for index, image in enumerate(item["images"]):
                key = (pid, str(image["url"]), str(image.get("source") or "scryfall"))
                if key not in existing:
                    rows.append((pid, key[1], index == 0, key[2]))
                    existing.add(key)
        if rows:
            execute_values(cur, "INSERT INTO print_images (print_id,url,is_primary,source) VALUES %s", rows, page_size=BATCH_SIZE)
            counts["print_images_created"] += len(rows)

    if _table_exists(cur, "print_localizations"):
        cur.execute(
            "SELECT id,print_id,language,source,external_id,card_name,details_json FROM print_localizations WHERE print_id=ANY(%s)",
            (ids,),
        )
        existing = {(int(row[1]), str(row[2])): row for row in cur.fetchall()}
        inserts = []
        updates = []
        for item in batch:
            pid = int(item["print_id"])
            key = (pid, item["language"])
            expected_name = clean(item["raw"].get("printed_name")) or None
            expected_details = {
                "printed_type_line": clean(item["raw"].get("printed_type_line")) or None,
                "printed_text": clean(item["raw"].get("printed_text")) or None,
                "scryfall_lang": item["language"],
            }
            row = existing.get(key)
            if row is None:
                inserts.append((pid, item["language"], "scryfall", item["scryfall_id"], expected_name, None, Json(expected_details)))
                continue
            loc_id, _, _, _, external_id, card_name, details = row
            if external_id and str(external_id).lower() != item["scryfall_id"]:
                raise RuntimeError(f"Localization external_id conflict for print {pid}")
            if card_name and expected_name and str(card_name) != expected_name:
                raise RuntimeError(f"Localization printed_name conflict for print {pid}")
            merged = dict(details or {}) if isinstance(details, dict) else {}
            changed = False
            for field, expected in expected_details.items():
                current = merged.get(field)
                if current is not None and expected is not None and current != expected:
                    raise RuntimeError(f"Localization {field} conflict for print {pid}")
                if field not in merged or (merged.get(field) is None and expected is not None):
                    merged[field] = expected
                    changed = True
            new_external = external_id
            new_name = card_name
            if not external_id:
                new_external = item["scryfall_id"]
                changed = True
            if not card_name and expected_name:
                new_name = expected_name
                changed = True
            if changed:
                updates.append((new_external, new_name, Json(merged), int(loc_id)))
        if inserts:
            execute_values(
                cur,
                "INSERT INTO print_localizations (print_id,language,source,external_id,card_name,set_name,details_json) VALUES %s",
                inserts,
                page_size=BATCH_SIZE,
            )
            counts["print_localizations_created"] += len(inserts)
        if updates:
            cur.executemany(
                "UPDATE print_localizations SET external_id=%s,card_name=%s,details_json=%s,updated_at=now() WHERE id=%s",
                updates,
            )
            counts["print_localizations_updated"] += len(updates)
    return counts


def apply_pass(target_url: str, snapshot: Path, source_version: str | None) -> dict[str, Any]:
    conn = psycopg2.connect(target_url, connect_timeout=30, application_name="dontripit_mtg_multilingual_ephemeral_apply")
    conn.autocommit = False
    counts = Counter()
    seen_natural: set[tuple] = set()
    try:
        with conn.cursor() as cur:
            cur.execute("SET LOCAL statement_timeout='60min'")
            game_id, _ = _find_game(cur)
            set_ids, oracle_ids, card_keys, print_map, natural_map = _load_catalog_maps(cur, game_id)
            batch: list[dict] = []
            for raw in _iter_snapshot(snapshot):
                if not _is_paper(raw):
                    continue
                lang = clean(raw.get("lang")).lower()
                if lang not in LANGUAGES:
                    continue
                counts[f"source_objects_{lang}"] += 1
                set_code = clean(raw.get("set")).lower()
                set_id = set_ids.get(set_code)
                if set_id is None:
                    raise RuntimeError(f"Missing canonical MTG Set: {set_code}")
                oracle_id = clean(raw.get("oracle_id")).lower()
                if oracle_id:
                    card_id = oracle_ids.get(oracle_id)
                else:
                    card_id = card_keys.get(card_identity_key(raw))
                if card_id is None:
                    raise RuntimeError(f"Missing canonical MTG Card for Scryfall {clean(raw.get('id'))}")
                sid = clean(raw.get("id")).lower()
                collector = clean(raw.get("collector_number"))
                if not sid or not collector:
                    raise RuntimeError("Scryfall multilingual paper object missing exact physical identity")
                images = _image_rows(raw)
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
                            "attributes": _print_attributes(raw, finish),
                            "images": images,
                            "raw": raw,
                        }
                    )
                    if len(batch) >= BATCH_SIZE:
                        counts.update(_process_batch(cur, batch, print_map, natural_map, source_version))
                        batch.clear()
            if batch:
                counts.update(_process_batch(cur, batch, print_map, natural_map, source_version))
            conn.commit()
            return dict(counts)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def validate_final(target_url: str, snapshot: Path, baseline: dict[str, Any]) -> dict[str, Any]:
    expected_keys: set[str] = set()
    expected_images: set[tuple[str, str, str]] = set()
    source_counts = Counter()
    for raw in _iter_snapshot(snapshot):
        if not _is_paper(raw):
            continue
        lang = clean(raw.get("lang")).lower()
        if lang not in LANGUAGES:
            continue
        images = _image_rows(raw)
        for finish in finish_values(raw):
            pkey = physical_print_key(raw, finish)
            expected_keys.add(pkey)
            source_counts[lang] += 1
            for image in images:
                expected_images.add((pkey, str(image["url"]), str(image.get("source") or "scryfall")))

    conn = psycopg2.connect(target_url, connect_timeout=30, application_name="dontripit_mtg_multilingual_ephemeral_validate")
    conn.set_session(readonly=True, autocommit=False)
    try:
        with conn.cursor() as cur:
            game_id, _ = _find_game(cur)
            final = _state(cur, game_id)
            actual_final = {lang: int(final["languages"].get(lang, 0)) for lang in LANGUAGES}
            if actual_final != EXPECTED_FINAL:
                raise AssertionError(f"Final multilingual MTG counts mismatch: {actual_final} != {EXPECTED_FINAL}")
            if final["sets"] != baseline["sets"] or final["cards"] != baseline["cards"]:
                raise AssertionError("Canonical MTG Set/Card counts changed")
            if final["en_digest"] != baseline["en_digest"]:
                raise AssertionError("English MTG Print baseline changed")
            if final["economics"] != baseline["economics"]:
                raise AssertionError("MTG prices/economics changed during multilingual backfill")

            cur.execute(
                """
                SELECT p.print_key,p.id FROM prints p JOIN cards c ON c.id=p.card_id
                WHERE c.game_id=%s AND lower(coalesce(p.language,'')) IN ('es','ja')
                """,
                (game_id,),
            )
            actual_keys = {str(pkey): int(pid) for pkey, pid in cur.fetchall() if pkey}
            missing_keys = expected_keys - set(actual_keys)
            if missing_keys:
                raise AssertionError(f"Missing exact Scryfall physical Print keys: {sorted(missing_keys)[:10]}")

            cur.execute(
                """
                SELECT count(*) FROM (
                    SELECT p.set_id,p.collector_number,lower(coalesce(p.language,'')),p.is_foil,p.variant,count(*)
                    FROM prints p JOIN cards c ON c.id=p.card_id
                    WHERE c.game_id=%s AND lower(coalesce(p.language,'')) IN ('es','ja')
                    GROUP BY p.set_id,p.collector_number,lower(coalesce(p.language,'')),p.is_foil,p.variant HAVING count(*)>1
                ) d
                """,
                (game_id,),
            )
            natural_duplicates = int(cur.fetchone()[0])
            if natural_duplicates:
                raise AssertionError(f"Natural multilingual MTG duplicates: {natural_duplicates}")

            cur.execute(
                """
                SELECT count(*) FROM prints p JOIN cards c ON c.id=p.card_id
                LEFT JOIN print_localizations l ON l.print_id=p.id AND lower(l.language)=lower(p.language)
                WHERE c.game_id=%s AND lower(coalesce(p.language,'')) IN ('es','ja') AND l.id IS NULL
                """,
                (game_id,),
            )
            missing_localizations = int(cur.fetchone()[0])
            if missing_localizations:
                raise AssertionError(f"MTG multilingual Prints missing localization rows: {missing_localizations}")

            cur.execute(
                """
                SELECT p.print_key,i.url,coalesce(i.source,'')
                FROM print_images i JOIN prints p ON p.id=i.print_id JOIN cards c ON c.id=p.card_id
                WHERE c.game_id=%s AND lower(coalesce(p.language,'')) IN ('es','ja') AND i.source LIKE 'scryfall%%'
                """,
                (game_id,),
            )
            actual_images = {(str(pkey), str(url), str(source)) for pkey, url, source in cur.fetchall() if pkey}
            missing_images = expected_images - actual_images
            if missing_images:
                raise AssertionError(f"Missing exact Scryfall image rows: {list(missing_images)[:5]}")

            return {
                "status": "pass",
                "source_prints": dict(source_counts),
                "expected_exact_keys": len(expected_keys),
                "exact_keys_present": len(expected_keys),
                "natural_duplicates": natural_duplicates,
                "missing_localizations": missing_localizations,
                "expected_scryfall_image_relations": len(expected_images),
                "missing_scryfall_image_relations": 0,
                "sets_unchanged": True,
                "cards_unchanged": True,
                "english_prints_unchanged": True,
                "prices_unchanged": True,
                "database_bytes_before": baseline["database_bytes"],
                "database_bytes_after": final["database_bytes"],
                "database_bytes_delta": final["database_bytes"] - baseline["database_bytes"],
                "final_language_counts": actual_final,
            }
    finally:
        conn.rollback()
        conn.close()


def run(output: Path, snapshot: Path, snapshot_meta: Path) -> dict[str, Any]:
    production_url, target_url = _required_urls()
    report: dict[str, Any] = {"mode": "ephemeral-only-certification", "production_writes": 0}
    report["baseline"] = seed_ephemeral(production_url, target_url)
    report["snapshot"] = capture_snapshot(snapshot, snapshot_meta)
    source_version = report["snapshot"].get("bulk_updated_at")
    pass1 = apply_pass(target_url, snapshot, source_version)
    report["pass1"] = pass1
    created1 = {lang: int(pass1.get(f"prints_created_{lang}", 0)) for lang in LANGUAGES}
    if created1 != EXPECTED_NEW:
        raise AssertionError(f"Pass 1 exact create delta changed: {created1} != {EXPECTED_NEW}")
    pass2 = apply_pass(target_url, snapshot, source_version)
    report["pass2"] = pass2
    write_keys = [key for key in pass2 if key.startswith("prints_created_") or key.endswith("_created") or key.endswith("_updated")]
    nonzero_pass2 = {key: int(pass2[key]) for key in write_keys if int(pass2[key]) != 0}
    if nonzero_pass2:
        raise AssertionError(f"Pass 2 is not idempotent: {nonzero_pass2}")
    report["validation"] = validate_final(target_url, snapshot, report["baseline"])
    report["status"] = "pass"
    report["pass2_zero_writes"] = True
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Certify additive MTG ES/JA backfill on ephemeral PostgreSQL only")
    parser.add_argument("--output", default="/tmp/mtg-multilingual-ephemeral-certification.json")
    parser.add_argument("--snapshot", default="/tmp/scryfall-all-cards-sealed.jsonl")
    parser.add_argument("--snapshot-meta", default="/tmp/scryfall-all-cards-sealed-meta.json")
    args = parser.parse_args()
    run(Path(args.output), Path(args.snapshot), Path(args.snapshot_meta))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
