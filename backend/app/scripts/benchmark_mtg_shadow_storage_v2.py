from __future__ import annotations

import argparse
import csv
import gzip
import io
import json
import os
import re
import statistics
import tempfile
import time
import unicodedata
from collections import Counter
from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path

import psycopg2
import requests

from app.ingest.connectors.scryfall_mtg_v2 import ScryfallMtgV2Connector


FINISH_CODES = {"nonfoil": 1, "foil": 2, "etched": 3}
SCHEMAS = ("mtg_dup", "mtg_child")


def _write(path: Path | None, payload: dict) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def _clean(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _norm(value: object) -> str:
    text = unicodedata.normalize("NFKD", _clean(value))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", text.casefold()).strip()


def _identity_key(card: dict) -> str:
    oracle = _clean(card.get("oracle_id"))
    if oracle:
        return f"oracle:{oracle}"
    return f"fallback:{_norm(card.get('name'))}|{_norm(card.get('layout'))}"


def _finish_values(card: dict) -> tuple[str, ...]:
    values = {str(v or "").strip().lower() for v in (card.get("finishes") or []) if str(v or "").strip()}
    if not values:
        if card.get("nonfoil"):
            values.add("nonfoil")
        if card.get("foil"):
            values.add("foil")
    unknown = values - set(FINISH_CODES)
    if unknown:
        raise AssertionError(f"Unknown Scryfall finish values: {sorted(unknown)}")
    if not values:
        raise AssertionError(f"Paper Scryfall row has no finish evidence: {card.get('id')}")
    return tuple(sorted(values))


def _is_paper(card: dict) -> bool:
    games = card.get("games")
    return not isinstance(games, list) or "paper" in {str(v or "").strip().lower() for v in games}


def _primary_image(card: dict) -> str:
    image_uris = card.get("image_uris") or {}
    if isinstance(image_uris, dict):
        for key in ("normal", "large", "png", "small"):
            value = _clean(image_uris.get(key))
            if value:
                return value
    for face in card.get("card_faces") or []:
        if not isinstance(face, dict):
            continue
        image_uris = face.get("image_uris") or {}
        if not isinstance(image_uris, dict):
            continue
        for key in ("normal", "large", "png", "small"):
            value = _clean(image_uris.get(key))
            if value:
                return value
    return ""


def _oracle_text(card: dict) -> str:
    direct = _clean(card.get("oracle_text"))
    if direct:
        return direct
    return " // ".join(
        value
        for value in (_clean(face.get("oracle_text")) for face in card.get("card_faces") or [] if isinstance(face, dict))
        if value
    )


def _type_line(card: dict) -> str:
    direct = _clean(card.get("type_line"))
    if direct:
        return direct
    return " // ".join(
        value
        for value in (_clean(face.get("type_line")) for face in card.get("card_faces") or [] if isinstance(face, dict))
        if value
    )


def _mana_cost(card: dict) -> str:
    direct = _clean(card.get("mana_cost"))
    if direct:
        return direct
    return " // ".join(
        value
        for value in (_clean(face.get("mana_cost")) for face in card.get("card_faces") or [] if isinstance(face, dict))
        if value
    )


def _legalities(card: dict) -> str:
    payload = card.get("legalities") or {}
    if not isinstance(payload, dict):
        payload = {}
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _card_search_text(card: dict) -> str:
    parts = [
        _clean(card.get("name")),
        _type_line(card),
        _oracle_text(card),
        " ".join(_clean(v) for v in card.get("keywords") or [] if _clean(v)),
        _mana_cost(card),
        "".join(_clean(v) for v in card.get("color_identity") or [] if _clean(v)),
    ]
    return _norm(" ".join(part for part in parts if part))


def _print_search_text(card: dict, *, finish: str | None = None) -> str:
    frame_effects = " ".join(_clean(v) for v in card.get("frame_effects") or [] if _clean(v))
    parts = [
        _clean(card.get("name")),
        _clean(card.get("set")),
        _clean(card.get("set_name")),
        _clean(card.get("collector_number")),
        _clean(card.get("rarity")),
        _clean(card.get("lang")),
        _clean(card.get("artist")),
        _clean(card.get("frame")),
        frame_effects,
        _clean(card.get("border_color")),
        finish or "",
    ]
    return _norm(" ".join(part for part in parts if part))


def _iter_bulk_rows(connector: ScryfallMtgV2Connector, url: str):
    headers = {
        "User-Agent": connector._SCRYFALL_HEADERS["User-Agent"],
        "Accept": "application/gzip,application/jsonl,application/x-ndjson,*/*;q=0.8",
    }
    with requests.get(url, headers=headers, stream=True, timeout=240) as response:
        response.raise_for_status()
        response.raw.decode_content = False
        is_gzip = url.lower().endswith(".gz") or "gzip" in str(response.headers.get("Content-Type") or "").lower()
        if is_gzip:
            with gzip.GzipFile(fileobj=response.raw, mode="rb") as compressed:
                with io.TextIOWrapper(compressed, encoding="utf-8") as stream:
                    for line in stream:
                        line = line.strip()
                        if line:
                            yield json.loads(line)
        else:
            for raw in response.iter_lines(decode_unicode=True):
                line = str(raw or "").strip()
                if line:
                    yield json.loads(line)


def _writer(stack: ExitStack, root: Path, name: str):
    handle = stack.enter_context((root / name).open("w+", encoding="utf-8", newline=""))
    writer = csv.writer(handle, delimiter="\t", quotechar='"', quoting=csv.QUOTE_ALL, lineterminator="\n")
    return handle, writer


def _copy(cur, schema: str, table: str, columns: list[str], handle) -> None:
    handle.flush()
    handle.seek(0)
    column_sql = ", ".join(f'"{column}"' for column in columns)
    cur.copy_expert(
        f'COPY "{schema}"."{table}" ({column_sql}) FROM STDIN WITH (FORMAT CSV, DELIMITER E\'\\t\', QUOTE \'"\')',
        handle,
    )


def _create_schema(cur, schema: str, *, child_model: bool) -> None:
    cur.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
    cur.execute(f'CREATE SCHEMA "{schema}"')
    cur.execute(f"""
        CREATE TABLE "{schema}".sets (
          id INTEGER PRIMARY KEY,
          code TEXT NOT NULL UNIQUE,
          name TEXT NOT NULL,
          set_type TEXT NOT NULL,
          released_at TEXT NOT NULL
        );
        CREATE TABLE "{schema}".cards (
          id BIGINT PRIMARY KEY,
          identity_key TEXT NOT NULL UNIQUE,
          oracle_id TEXT NOT NULL,
          name TEXT NOT NULL,
          layout TEXT NOT NULL,
          type_line TEXT NOT NULL,
          mana_cost TEXT NOT NULL,
          mana_value NUMERIC(8,2) NOT NULL,
          color_identity TEXT NOT NULL,
          oracle_text TEXT NOT NULL,
          keywords TEXT NOT NULL,
          legalities JSONB NOT NULL,
          power TEXT NOT NULL,
          toughness TEXT NOT NULL,
          loyalty TEXT NOT NULL
        );
        CREATE UNIQUE INDEX "{schema}_cards_oracle_uq"
          ON "{schema}".cards(oracle_id) WHERE oracle_id <> '';
    """)
    if child_model:
        cur.execute(f"""
            CREATE TABLE "{schema}".source_prints (
              id BIGINT PRIMARY KEY,
              scryfall_id TEXT NOT NULL UNIQUE,
              card_id BIGINT NOT NULL REFERENCES "{schema}".cards(id),
              set_id INTEGER NOT NULL REFERENCES "{schema}".sets(id),
              collector_number TEXT NOT NULL,
              lang TEXT NOT NULL,
              rarity TEXT NOT NULL,
              released_at TEXT NOT NULL,
              artist TEXT NOT NULL,
              illustration_id TEXT NOT NULL,
              frame TEXT NOT NULL,
              border_color TEXT NOT NULL,
              promo BOOLEAN NOT NULL,
              image_url TEXT NOT NULL
            );
            CREATE INDEX "{schema}_source_prints_card" ON "{schema}".source_prints(card_id);
            CREATE INDEX "{schema}_source_prints_set" ON "{schema}".source_prints(set_id);
            CREATE INDEX "{schema}_source_prints_natural" ON "{schema}".source_prints(set_id, collector_number, lang);
            CREATE TABLE "{schema}".finish_variants (
              id BIGINT PRIMARY KEY,
              source_print_id BIGINT NOT NULL REFERENCES "{schema}".source_prints(id) ON DELETE CASCADE,
              finish_code SMALLINT NOT NULL,
              UNIQUE(source_print_id, finish_code)
            );
            CREATE INDEX "{schema}_finish_variants_finish" ON "{schema}".finish_variants(finish_code, source_print_id);
        """)
    else:
        cur.execute(f"""
            CREATE TABLE "{schema}".prints (
              id BIGINT PRIMARY KEY,
              scryfall_id TEXT NOT NULL,
              finish_code SMALLINT NOT NULL,
              card_id BIGINT NOT NULL REFERENCES "{schema}".cards(id),
              set_id INTEGER NOT NULL REFERENCES "{schema}".sets(id),
              collector_number TEXT NOT NULL,
              lang TEXT NOT NULL,
              rarity TEXT NOT NULL,
              released_at TEXT NOT NULL,
              artist TEXT NOT NULL,
              illustration_id TEXT NOT NULL,
              frame TEXT NOT NULL,
              border_color TEXT NOT NULL,
              promo BOOLEAN NOT NULL,
              image_url TEXT NOT NULL,
              UNIQUE(scryfall_id, finish_code),
              UNIQUE(set_id, collector_number, lang, finish_code)
            );
            CREATE INDEX "{schema}_prints_source" ON "{schema}".prints(scryfall_id);
            CREATE INDEX "{schema}_prints_card" ON "{schema}".prints(card_id);
            CREATE INDEX "{schema}_prints_set" ON "{schema}".prints(set_id);
            CREATE INDEX "{schema}_prints_finish" ON "{schema}".prints(finish_code, id);
        """)

    cur.execute(f"""
        CREATE TABLE "{schema}".card_search (
          card_id BIGINT PRIMARY KEY REFERENCES "{schema}".cards(id) ON DELETE CASCADE,
          normalized_name TEXT NOT NULL,
          search_text TEXT NOT NULL
        );
        CREATE INDEX "{schema}_card_search_name_trgm"
          ON "{schema}".card_search USING gin (normalized_name gin_trgm_ops);
        CREATE INDEX "{schema}_card_search_text_trgm"
          ON "{schema}".card_search USING gin (search_text gin_trgm_ops);
    """)

    if child_model:
        cur.execute(f"""
            CREATE TABLE "{schema}".print_search (
              source_print_id BIGINT PRIMARY KEY REFERENCES "{schema}".source_prints(id) ON DELETE CASCADE,
              card_id BIGINT NOT NULL,
              normalized_name TEXT NOT NULL,
              set_code TEXT NOT NULL,
              collector_number TEXT NOT NULL,
              lang TEXT NOT NULL,
              rarity TEXT NOT NULL,
              search_text TEXT NOT NULL
            );
            CREATE INDEX "{schema}_print_search_name_trgm"
              ON "{schema}".print_search USING gin (normalized_name gin_trgm_ops);
            CREATE INDEX "{schema}_print_search_text_trgm"
              ON "{schema}".print_search USING gin (search_text gin_trgm_ops);
            CREATE INDEX "{schema}_print_search_set" ON "{schema}".print_search(set_code);
            CREATE INDEX "{schema}_print_search_collector" ON "{schema}".print_search(collector_number);
            CREATE INDEX "{schema}_print_search_lang" ON "{schema}".print_search(lang);
            CREATE INDEX "{schema}_print_search_rarity" ON "{schema}".print_search(rarity);
        """)
    else:
        cur.execute(f"""
            CREATE TABLE "{schema}".print_search (
              exact_print_id BIGINT PRIMARY KEY REFERENCES "{schema}".prints(id) ON DELETE CASCADE,
              card_id BIGINT NOT NULL,
              normalized_name TEXT NOT NULL,
              set_code TEXT NOT NULL,
              collector_number TEXT NOT NULL,
              lang TEXT NOT NULL,
              rarity TEXT NOT NULL,
              finish_code SMALLINT NOT NULL,
              search_text TEXT NOT NULL
            );
            CREATE INDEX "{schema}_print_search_name_trgm"
              ON "{schema}".print_search USING gin (normalized_name gin_trgm_ops);
            CREATE INDEX "{schema}_print_search_text_trgm"
              ON "{schema}".print_search USING gin (search_text gin_trgm_ops);
            CREATE INDEX "{schema}_print_search_set" ON "{schema}".print_search(set_code);
            CREATE INDEX "{schema}_print_search_collector" ON "{schema}".print_search(collector_number);
            CREATE INDEX "{schema}_print_search_lang" ON "{schema}".print_search(lang);
            CREATE INDEX "{schema}_print_search_rarity" ON "{schema}".print_search(rarity);
            CREATE INDEX "{schema}_print_search_finish" ON "{schema}".print_search(finish_code);
        """)


def _relation_sizes(cur, schema: str) -> dict:
    cur.execute(
        """
        SELECT
          c.relname,
          pg_relation_size(c.oid)::bigint AS heap_bytes,
          pg_indexes_size(c.oid)::bigint AS index_bytes,
          pg_total_relation_size(c.oid)::bigint AS total_bytes,
          COALESCE(c.reltuples,0)::bigint AS estimated_rows
        FROM pg_class c
        JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname=%s AND c.relkind='r'
        ORDER BY pg_total_relation_size(c.oid) DESC, c.relname
        """,
        (schema,),
    )
    tables = []
    for name, heap, indexes, total, rows in cur.fetchall():
        tables.append({
            "table": name,
            "heap_bytes": int(heap),
            "index_bytes": int(indexes),
            "total_bytes": int(total),
            "heap_mib": round(int(heap) / 1024 / 1024, 2),
            "index_mib": round(int(indexes) / 1024 / 1024, 2),
            "total_mib": round(int(total) / 1024 / 1024, 2),
            "estimated_rows": int(rows),
        })
    cur.execute(
        """
        SELECT
          i.relname AS index_name,
          t.relname AS table_name,
          pg_relation_size(i.oid)::bigint AS bytes
        FROM pg_class i
        JOIN pg_index x ON x.indexrelid=i.oid
        JOIN pg_class t ON t.oid=x.indrelid
        JOIN pg_namespace n ON n.oid=t.relnamespace
        WHERE n.nspname=%s
        ORDER BY pg_relation_size(i.oid) DESC, i.relname
        """,
        (schema,),
    )
    indexes = [
        {"index": name, "table": table, "bytes": int(size), "mib": round(int(size) / 1024 / 1024, 3)}
        for name, table, size in cur.fetchall()
    ]
    total_bytes = sum(row["total_bytes"] for row in tables)
    return {
        "tables": tables,
        "indexes": indexes,
        "total_bytes": total_bytes,
        "total_mib": round(total_bytes / 1024 / 1024, 2),
        "heap_mib": round(sum(row["heap_bytes"] for row in tables) / 1024 / 1024, 2),
        "index_mib": round(sum(row["index_bytes"] for row in tables) / 1024 / 1024, 2),
    }


def _median_ms(cur, sql: str, params: tuple, *, repeats: int = 7) -> float:
    cur.execute(sql, params)
    cur.fetchall()
    samples = []
    for _ in range(repeats):
        started = time.perf_counter()
        cur.execute(sql, params)
        cur.fetchall()
        samples.append((time.perf_counter() - started) * 1000.0)
    return round(statistics.median(samples), 2)


def _benchmarks(cur, *, sample: dict) -> dict:
    name_query = f"%{sample['normalized_name']}%"
    source_id = sample["scryfall_id"]
    set_code = sample["set_code"]
    collector = sample["collector_number"]
    finish_code = sample["finish_code"]

    return {
        "duplicated_finish_model": {
            "name_search_ms": _median_ms(cur, "SELECT exact_print_id FROM mtg_dup.print_search WHERE normalized_name ILIKE %s LIMIT 20", (name_query,)),
            "exact_collector_ms": _median_ms(cur, "SELECT exact_print_id FROM mtg_dup.print_search WHERE set_code=%s AND collector_number=%s LIMIT 20", (set_code, collector)),
            "name_plus_finish_ms": _median_ms(cur, "SELECT exact_print_id FROM mtg_dup.print_search WHERE normalized_name ILIKE %s AND finish_code=%s LIMIT 20", (name_query, finish_code)),
            "source_id_ms": _median_ms(cur, "SELECT id FROM mtg_dup.prints WHERE scryfall_id=%s LIMIT 20", (source_id,)),
        },
        "source_print_child_finish_model": {
            "name_search_ms": _median_ms(cur, "SELECT source_print_id FROM mtg_child.print_search WHERE normalized_name ILIKE %s LIMIT 20", (name_query,)),
            "exact_collector_ms": _median_ms(cur, "SELECT source_print_id FROM mtg_child.print_search WHERE set_code=%s AND collector_number=%s LIMIT 20", (set_code, collector)),
            "name_plus_finish_ms": _median_ms(cur, "SELECT s.source_print_id FROM mtg_child.print_search s JOIN mtg_child.finish_variants f ON f.source_print_id=s.source_print_id WHERE s.normalized_name ILIKE %s AND f.finish_code=%s LIMIT 20", (name_query, finish_code)),
            "source_id_ms": _median_ms(cur, "SELECT id FROM mtg_child.source_prints WHERE scryfall_id=%s LIMIT 20", (source_id,)),
        },
    }


def run(*, database_url: str, report_path: Path | None = None) -> dict:
    connector = ScryfallMtgV2Connector()
    metadata = connector._bulk_metadata()
    download_url = connector._bulk_download_url(metadata)
    if not download_url:
        raise AssertionError("Scryfall default_cards bulk URL unavailable")

    started = time.perf_counter()
    counts = Counter()
    set_ids: dict[str, int] = {}
    card_ids: dict[str, int] = {}
    seen_scryfall_ids: set[str] = set()
    sample: dict | None = None

    with tempfile.TemporaryDirectory(prefix="mtg-shadow-v2-") as temp_dir, ExitStack() as stack:
        root = Path(temp_dir)
        sets_f, sets_w = _writer(stack, root, "sets.tsv")
        cards_f, cards_w = _writer(stack, root, "cards.tsv")
        card_search_f, card_search_w = _writer(stack, root, "card_search.tsv")
        dup_prints_f, dup_prints_w = _writer(stack, root, "dup_prints.tsv")
        dup_search_f, dup_search_w = _writer(stack, root, "dup_search.tsv")
        source_prints_f, source_prints_w = _writer(stack, root, "source_prints.tsv")
        finish_f, finish_w = _writer(stack, root, "finish_variants.tsv")
        source_search_f, source_search_w = _writer(stack, root, "source_search.tsv")

        next_set_id = 1
        next_card_id = 1
        next_source_print_id = 1
        next_exact_print_id = 1
        next_finish_id = 1

        for card in _iter_bulk_rows(connector, download_url):
            if not isinstance(card, dict) or not _is_paper(card):
                continue
            counts["paper_source_prints"] += 1
            scryfall_id = _clean(card.get("id"))
            if not scryfall_id:
                raise AssertionError("Paper Scryfall row missing id")
            if scryfall_id in seen_scryfall_ids:
                raise AssertionError(f"Duplicate paper Scryfall id: {scryfall_id}")
            seen_scryfall_ids.add(scryfall_id)

            set_code = _clean(card.get("set")).lower()
            if not set_code:
                raise AssertionError(f"Scryfall row missing set code: {scryfall_id}")
            set_id = set_ids.get(set_code)
            if set_id is None:
                set_id = next_set_id
                next_set_id += 1
                set_ids[set_code] = set_id
                sets_w.writerow([
                    set_id,
                    set_code,
                    _clean(card.get("set_name")) or set_code.upper(),
                    _clean(card.get("set_type")),
                    _clean(card.get("released_at")),
                ])

            identity = _identity_key(card)
            card_id = card_ids.get(identity)
            if card_id is None:
                card_id = next_card_id
                next_card_id += 1
                card_ids[identity] = card_id
                normalized_name = _norm(card.get("name"))
                card_search_text = _card_search_text(card)
                cards_w.writerow([
                    card_id,
                    identity,
                    _clean(card.get("oracle_id")),
                    _clean(card.get("name")),
                    _clean(card.get("layout")),
                    _type_line(card),
                    _mana_cost(card),
                    float(card.get("cmc") or 0),
                    "".join(_clean(v) for v in card.get("color_identity") or [] if _clean(v)),
                    _oracle_text(card),
                    "|".join(_clean(v) for v in card.get("keywords") or [] if _clean(v)),
                    _legalities(card),
                    _clean(card.get("power")),
                    _clean(card.get("toughness")),
                    _clean(card.get("loyalty")),
                ])
                card_search_w.writerow([card_id, normalized_name, card_search_text])

            source_print_id = next_source_print_id
            next_source_print_id += 1
            normalized_name = _norm(card.get("name"))
            source_search_text = _print_search_text(card)
            image_url = _primary_image(card)
            source_prints_w.writerow([
                source_print_id,
                scryfall_id,
                card_id,
                set_id,
                _clean(card.get("collector_number")),
                _clean(card.get("lang")).lower(),
                _clean(card.get("rarity")).lower(),
                _clean(card.get("released_at")),
                _clean(card.get("artist")),
                _clean(card.get("illustration_id")),
                _clean(card.get("frame")),
                _clean(card.get("border_color")),
                bool(card.get("promo")),
                image_url,
            ])
            source_search_w.writerow([
                source_print_id,
                card_id,
                normalized_name,
                set_code,
                _clean(card.get("collector_number")),
                _clean(card.get("lang")).lower(),
                _clean(card.get("rarity")).lower(),
                source_search_text,
            ])

            finishes = _finish_values(card)
            if len(finishes) > 1:
                counts["multi_finish_source_prints"] += 1
            for finish in finishes:
                finish_code = FINISH_CODES[finish]
                counts[f"finish_{finish}"] += 1
                counts["exact_finish_variants"] += 1

                finish_w.writerow([next_finish_id, source_print_id, finish_code])
                next_finish_id += 1

                exact_id = next_exact_print_id
                next_exact_print_id += 1
                dup_prints_w.writerow([
                    exact_id,
                    scryfall_id,
                    finish_code,
                    card_id,
                    set_id,
                    _clean(card.get("collector_number")),
                    _clean(card.get("lang")).lower(),
                    _clean(card.get("rarity")).lower(),
                    _clean(card.get("released_at")),
                    _clean(card.get("artist")),
                    _clean(card.get("illustration_id")),
                    _clean(card.get("frame")),
                    _clean(card.get("border_color")),
                    bool(card.get("promo")),
                    image_url,
                ])
                dup_search_w.writerow([
                    exact_id,
                    card_id,
                    normalized_name,
                    set_code,
                    _clean(card.get("collector_number")),
                    _clean(card.get("lang")).lower(),
                    _clean(card.get("rarity")).lower(),
                    finish_code,
                    _print_search_text(card, finish=finish),
                ])
                if sample is None and len(finishes) > 1 and normalized_name:
                    sample = {
                        "normalized_name": normalized_name,
                        "scryfall_id": scryfall_id,
                        "set_code": set_code,
                        "collector_number": _clean(card.get("collector_number")),
                        "finish_code": finish_code,
                    }

        if sample is None:
            raise AssertionError("Could not select MTG benchmark sample")

        expected = {
            "sets": len(set_ids),
            "cards": len(card_ids),
            "source_prints": int(counts["paper_source_prints"]),
            "exact_finish_variants": int(counts["exact_finish_variants"]),
        }

        connection = psycopg2.connect(database_url)
        connection.autocommit = True
        try:
            cur = connection.cursor()
            cur.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
            _create_schema(cur, "mtg_dup", child_model=False)
            _create_schema(cur, "mtg_child", child_model=True)

            # Both candidate schemas deliberately receive identical Card/Set data.
            for schema in SCHEMAS:
                _copy(cur, schema, "sets", ["id", "code", "name", "set_type", "released_at"], sets_f)
                _copy(cur, schema, "cards", [
                    "id", "identity_key", "oracle_id", "name", "layout", "type_line", "mana_cost", "mana_value",
                    "color_identity", "oracle_text", "keywords", "legalities", "power", "toughness", "loyalty",
                ], cards_f)
                _copy(cur, schema, "card_search", ["card_id", "normalized_name", "search_text"], card_search_f)

            _copy(cur, "mtg_dup", "prints", [
                "id", "scryfall_id", "finish_code", "card_id", "set_id", "collector_number", "lang", "rarity",
                "released_at", "artist", "illustration_id", "frame", "border_color", "promo", "image_url",
            ], dup_prints_f)
            _copy(cur, "mtg_dup", "print_search", [
                "exact_print_id", "card_id", "normalized_name", "set_code", "collector_number", "lang", "rarity",
                "finish_code", "search_text",
            ], dup_search_f)

            _copy(cur, "mtg_child", "source_prints", [
                "id", "scryfall_id", "card_id", "set_id", "collector_number", "lang", "rarity", "released_at",
                "artist", "illustration_id", "frame", "border_color", "promo", "image_url",
            ], source_prints_f)
            _copy(cur, "mtg_child", "finish_variants", ["id", "source_print_id", "finish_code"], finish_f)
            _copy(cur, "mtg_child", "print_search", [
                "source_print_id", "card_id", "normalized_name", "set_code", "collector_number", "lang", "rarity",
                "search_text",
            ], source_search_f)

            for schema in SCHEMAS:
                for table in ("sets", "cards", "card_search", "print_search"):
                    cur.execute(f'ANALYZE "{schema}"."{table}"')
            cur.execute('ANALYZE mtg_dup.prints')
            cur.execute('ANALYZE mtg_child.source_prints')
            cur.execute('ANALYZE mtg_child.finish_variants')

            cur.execute("SELECT COUNT(*) FROM mtg_dup.sets")
            if int(cur.fetchone()[0]) != expected["sets"]:
                raise AssertionError("Duplicated model Set count mismatch")
            cur.execute("SELECT COUNT(*) FROM mtg_dup.cards")
            if int(cur.fetchone()[0]) != expected["cards"]:
                raise AssertionError("Duplicated model Card count mismatch")
            cur.execute("SELECT COUNT(*) FROM mtg_dup.prints")
            if int(cur.fetchone()[0]) != expected["exact_finish_variants"]:
                raise AssertionError("Duplicated model exact Print count mismatch")
            cur.execute("SELECT COUNT(*) FROM mtg_child.source_prints")
            if int(cur.fetchone()[0]) != expected["source_prints"]:
                raise AssertionError("Child model SourcePrint count mismatch")
            cur.execute("SELECT COUNT(*) FROM mtg_child.finish_variants")
            if int(cur.fetchone()[0]) != expected["exact_finish_variants"]:
                raise AssertionError("Child model FinishVariant count mismatch")

            sizes = {schema: _relation_sizes(cur, schema) for schema in SCHEMAS}
            benchmarks = _benchmarks(cur, sample=sample)
            cur.close()
        finally:
            connection.close()

    child_total = sizes["mtg_child"]["total_bytes"]
    dup_total = sizes["mtg_dup"]["total_bytes"]
    saved = dup_total - child_total
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": "ephemeral_postgresql_mtg_full_source_shadow_storage_benchmark",
        "status": "pass",
        "source": {
            "type": metadata.get("type"),
            "updated_at": metadata.get("updated_at"),
            "download_contract": "Scryfall default_cards JSONL/GZIP",
        },
        "counts": {
            **expected,
            "multi_finish_source_prints": int(counts["multi_finish_source_prints"]),
            "finish_nonfoil": int(counts["finish_nonfoil"]),
            "finish_foil": int(counts["finish_foil"]),
            "finish_etched": int(counts["finish_etched"]),
        },
        "models": {
            "mtg_dup": {
                "description": "one exact Print row and one Print Search row per Scryfall ID + finish",
                **sizes["mtg_dup"],
            },
            "mtg_child": {
                "description": "one SourcePrint/Search row per Scryfall object plus a lightweight exact FinishVariant child",
                **sizes["mtg_child"],
            },
        },
        "comparison": {
            "child_model_saved_bytes": saved,
            "child_model_saved_mib": round(saved / 1024 / 1024, 2),
            "child_model_percent_smaller": round(100.0 * saved / dup_total, 2) if dup_total else 0.0,
        },
        "benchmarks_median_ms": benchmarks,
        "benchmark_sample": sample,
        "elapsed_seconds": round(time.perf_counter() - started, 2),
        "neon_writes": 0,
        "decision_rule": "Prefer the child-finish model only if identity remains exact, representative finish/search queries stay within product latency goals, and measured storage materially improves over duplicated exact Prints.",
    }
    _write(report_path, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url", default=os.getenv("SHADOW_DATABASE_URL"))
    parser.add_argument("--report-path", type=Path, default=None)
    args = parser.parse_args()
    if not args.database_url:
        raise SystemExit("SHADOW_DATABASE_URL/--database-url is required")
    run(database_url=args.database_url, report_path=args.report_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
