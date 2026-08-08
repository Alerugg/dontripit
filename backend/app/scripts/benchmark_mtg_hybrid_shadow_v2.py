from __future__ import annotations

import argparse
import csv
import json
import os
import tempfile
import time
from collections import Counter
from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path

import psycopg2

from app.ingest.connectors.scryfall_mtg_v2 import ScryfallMtgV2Connector
from app.scripts.benchmark_mtg_shadow_storage_v2 import (
    FINISH_CODES,
    _card_search_text,
    _clean,
    _copy,
    _finish_values,
    _identity_key,
    _is_paper,
    _iter_bulk_rows,
    _legalities,
    _mana_cost,
    _median_ms,
    _norm,
    _oracle_text,
    _primary_image,
    _print_search_text,
    _relation_sizes,
    _type_line,
    _write,
    _writer,
)


SCHEMA = "mtg_hybrid"


def _create_schema(cur) -> None:
    cur.execute(f'DROP SCHEMA IF EXISTS "{SCHEMA}" CASCADE')
    cur.execute(f'CREATE SCHEMA "{SCHEMA}"')
    cur.execute(f"""
        CREATE TABLE {SCHEMA}.sets (
          id INTEGER PRIMARY KEY,
          code TEXT NOT NULL UNIQUE,
          name TEXT NOT NULL,
          set_type TEXT NOT NULL,
          released_at TEXT NOT NULL
        );
        CREATE TABLE {SCHEMA}.cards (
          id BIGINT PRIMARY KEY,
          identity_key TEXT NOT NULL UNIQUE,
          oracle_id TEXT NOT NULL,
          name TEXT NOT NULL,
          layout TEXT NOT NULL,
          type_line TEXT NOT NULL,
          mana_cost TEXT NOT NULL,
          mana_value DOUBLE PRECISION NOT NULL,
          color_identity TEXT NOT NULL,
          oracle_text TEXT NOT NULL,
          keywords TEXT NOT NULL,
          legalities JSONB NOT NULL,
          power TEXT NOT NULL,
          toughness TEXT NOT NULL,
          loyalty TEXT NOT NULL
        );
        CREATE UNIQUE INDEX {SCHEMA}_cards_oracle_uq ON {SCHEMA}.cards(oracle_id) WHERE oracle_id <> '';

        CREATE TABLE {SCHEMA}.source_prints (
          id BIGINT PRIMARY KEY,
          scryfall_id TEXT NOT NULL UNIQUE,
          card_id BIGINT NOT NULL REFERENCES {SCHEMA}.cards(id),
          set_id INTEGER NOT NULL REFERENCES {SCHEMA}.sets(id),
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
        CREATE INDEX {SCHEMA}_source_prints_card ON {SCHEMA}.source_prints(card_id);
        CREATE INDEX {SCHEMA}_source_prints_set ON {SCHEMA}.source_prints(set_id);
        CREATE INDEX {SCHEMA}_source_prints_natural ON {SCHEMA}.source_prints(set_id, collector_number, lang);

        CREATE TABLE {SCHEMA}.prints (
          id BIGINT PRIMARY KEY,
          source_print_id BIGINT NOT NULL REFERENCES {SCHEMA}.source_prints(id) ON DELETE CASCADE,
          card_id BIGINT NOT NULL REFERENCES {SCHEMA}.cards(id),
          set_id INTEGER NOT NULL REFERENCES {SCHEMA}.sets(id),
          collector_number TEXT NOT NULL,
          lang TEXT NOT NULL,
          rarity TEXT NOT NULL,
          finish_code SMALLINT NOT NULL,
          is_foil BOOLEAN NOT NULL,
          variant TEXT NOT NULL,
          print_key TEXT NOT NULL UNIQUE,
          UNIQUE(source_print_id, finish_code),
          UNIQUE(set_id, collector_number, lang, finish_code)
        );
        CREATE INDEX {SCHEMA}_prints_card ON {SCHEMA}.prints(card_id);
        CREATE INDEX {SCHEMA}_prints_set ON {SCHEMA}.prints(set_id);
        CREATE INDEX {SCHEMA}_prints_language ON {SCHEMA}.prints(lang);
        CREATE INDEX {SCHEMA}_prints_rarity ON {SCHEMA}.prints(rarity);
        CREATE INDEX {SCHEMA}_prints_finish ON {SCHEMA}.prints(finish_code, id);

        CREATE TABLE {SCHEMA}.card_search (
          card_id BIGINT PRIMARY KEY REFERENCES {SCHEMA}.cards(id) ON DELETE CASCADE,
          normalized_name TEXT NOT NULL,
          search_text TEXT NOT NULL
        );
        CREATE INDEX {SCHEMA}_card_search_name_trgm ON {SCHEMA}.card_search USING gin (normalized_name gin_trgm_ops);
        CREATE INDEX {SCHEMA}_card_search_text_trgm ON {SCHEMA}.card_search USING gin (search_text gin_trgm_ops);

        CREATE TABLE {SCHEMA}.print_search (
          source_print_id BIGINT PRIMARY KEY REFERENCES {SCHEMA}.source_prints(id) ON DELETE CASCADE,
          card_id BIGINT NOT NULL,
          normalized_name TEXT NOT NULL,
          set_code TEXT NOT NULL,
          collector_number TEXT NOT NULL,
          lang TEXT NOT NULL,
          rarity TEXT NOT NULL,
          search_text TEXT NOT NULL
        );
        CREATE INDEX {SCHEMA}_print_search_name_trgm ON {SCHEMA}.print_search USING gin (normalized_name gin_trgm_ops);
        CREATE INDEX {SCHEMA}_print_search_text_trgm ON {SCHEMA}.print_search USING gin (search_text gin_trgm_ops);
        CREATE INDEX {SCHEMA}_print_search_set ON {SCHEMA}.print_search(set_code);
        CREATE INDEX {SCHEMA}_print_search_collector ON {SCHEMA}.print_search(collector_number);
        CREATE INDEX {SCHEMA}_print_search_lang ON {SCHEMA}.print_search(lang);
        CREATE INDEX {SCHEMA}_print_search_rarity ON {SCHEMA}.print_search(rarity);
    """)


def _benchmarks(cur, sample: dict) -> dict:
    name_query = f"%{sample['normalized_name']}%"
    return {
        "name_search_ms": _median_ms(
            cur,
            f"SELECT source_print_id FROM {SCHEMA}.print_search WHERE normalized_name ILIKE %s LIMIT 20",
            (name_query,),
        ),
        "exact_collector_ms": _median_ms(
            cur,
            f"SELECT source_print_id FROM {SCHEMA}.print_search WHERE set_code=%s AND collector_number=%s LIMIT 20",
            (sample["set_code"], sample["collector_number"]),
        ),
        "name_plus_finish_exact_print_ms": _median_ms(
            cur,
            f"""
            SELECT p.id
            FROM {SCHEMA}.print_search s
            JOIN {SCHEMA}.prints p ON p.source_print_id=s.source_print_id
            WHERE s.normalized_name ILIKE %s AND p.finish_code=%s
            LIMIT 20
            """,
            (name_query, sample["finish_code"]),
        ),
        "source_id_to_exact_prints_ms": _median_ms(
            cur,
            f"""
            SELECT p.id
            FROM {SCHEMA}.source_prints sp
            JOIN {SCHEMA}.prints p ON p.source_print_id=sp.id
            WHERE sp.scryfall_id=%s
            ORDER BY p.finish_code
            """,
            (sample["scryfall_id"],),
        ),
        "print_key_lookup_ms": _median_ms(
            cur,
            f"SELECT id FROM {SCHEMA}.prints WHERE print_key=%s",
            (sample["print_key"],),
        ),
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

    with tempfile.TemporaryDirectory(prefix="mtg-hybrid-shadow-") as temp_dir, ExitStack() as stack:
        root = Path(temp_dir)
        sets_f, sets_w = _writer(stack, root, "sets.tsv")
        cards_f, cards_w = _writer(stack, root, "cards.tsv")
        card_search_f, card_search_w = _writer(stack, root, "card_search.tsv")
        source_f, source_w = _writer(stack, root, "source_prints.tsv")
        prints_f, prints_w = _writer(stack, root, "prints.tsv")
        search_f, search_w = _writer(stack, root, "print_search.tsv")

        next_set_id = 1
        next_card_id = 1
        next_source_id = 1
        next_print_id = 1

        for card in _iter_bulk_rows(connector, download_url):
            if not isinstance(card, dict) or not _is_paper(card):
                continue
            counts["source_prints"] += 1
            scryfall_id = _clean(card.get("id"))
            if not scryfall_id or scryfall_id in seen_scryfall_ids:
                raise AssertionError(f"Missing/duplicate Scryfall id: {scryfall_id}")
            seen_scryfall_ids.add(scryfall_id)

            set_code = _clean(card.get("set")).lower()
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
                card_search_w.writerow([card_id, normalized_name, _card_search_text(card)])

            source_id = next_source_id
            next_source_id += 1
            source_w.writerow([
                source_id,
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
                _primary_image(card),
            ])
            normalized_name = _norm(card.get("name"))
            search_w.writerow([
                source_id,
                card_id,
                normalized_name,
                set_code,
                _clean(card.get("collector_number")),
                _clean(card.get("lang")).lower(),
                _clean(card.get("rarity")).lower(),
                _print_search_text(card),
            ])

            finishes = _finish_values(card)
            if len(finishes) > 1:
                counts["multi_finish_source_prints"] += 1
            for finish in finishes:
                finish_code = FINISH_CODES[finish]
                print_id = next_print_id
                next_print_id += 1
                print_key = f"mtg|scryfall:{scryfall_id}|finish:{finish}"
                prints_w.writerow([
                    print_id,
                    source_id,
                    card_id,
                    set_id,
                    _clean(card.get("collector_number")),
                    _clean(card.get("lang")).lower(),
                    _clean(card.get("rarity")).lower(),
                    finish_code,
                    finish == "foil",
                    finish,
                    print_key,
                ])
                counts["exact_prints"] += 1
                counts[f"finish_{finish}"] += 1
                if sample is None and len(finishes) > 1 and normalized_name:
                    sample = {
                        "normalized_name": normalized_name,
                        "scryfall_id": scryfall_id,
                        "set_code": set_code,
                        "collector_number": _clean(card.get("collector_number")),
                        "finish_code": finish_code,
                        "print_key": print_key,
                    }

        if sample is None:
            raise AssertionError("No multi-finish sample found")

        expected = {
            "sets": len(set_ids),
            "cards": len(card_ids),
            "source_prints": int(counts["source_prints"]),
            "exact_prints": int(counts["exact_prints"]),
        }

        conn = psycopg2.connect(database_url)
        conn.autocommit = True
        try:
            cur = conn.cursor()
            cur.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
            _create_schema(cur)
            _copy(cur, SCHEMA, "sets", ["id", "code", "name", "set_type", "released_at"], sets_f)
            _copy(cur, SCHEMA, "cards", [
                "id", "identity_key", "oracle_id", "name", "layout", "type_line", "mana_cost", "mana_value",
                "color_identity", "oracle_text", "keywords", "legalities", "power", "toughness", "loyalty",
            ], cards_f)
            _copy(cur, SCHEMA, "card_search", ["card_id", "normalized_name", "search_text"], card_search_f)
            _copy(cur, SCHEMA, "source_prints", [
                "id", "scryfall_id", "card_id", "set_id", "collector_number", "lang", "rarity", "released_at",
                "artist", "illustration_id", "frame", "border_color", "promo", "image_url",
            ], source_f)
            _copy(cur, SCHEMA, "prints", [
                "id", "source_print_id", "card_id", "set_id", "collector_number", "lang", "rarity", "finish_code",
                "is_foil", "variant", "print_key",
            ], prints_f)
            _copy(cur, SCHEMA, "print_search", [
                "source_print_id", "card_id", "normalized_name", "set_code", "collector_number", "lang", "rarity", "search_text",
            ], search_f)

            for table in ("sets", "cards", "card_search", "source_prints", "prints", "print_search"):
                cur.execute(f"ANALYZE {SCHEMA}.{table}")

            for table, expected_count in (
                ("sets", expected["sets"]),
                ("cards", expected["cards"]),
                ("source_prints", expected["source_prints"]),
                ("prints", expected["exact_prints"]),
                ("print_search", expected["source_prints"]),
            ):
                cur.execute(f"SELECT COUNT(*) FROM {SCHEMA}.{table}")
                actual = int(cur.fetchone()[0])
                if actual != expected_count:
                    raise AssertionError(f"{table} count mismatch: {actual} != {expected_count}")

            sizes = _relation_sizes(cur, SCHEMA)
            benchmarks = _benchmarks(cur, sample)
            cur.close()
        finally:
            conn.close()

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": "ephemeral_postgresql_mtg_hybrid_exact_print_shadow_benchmark",
        "status": "pass",
        "source": {"type": metadata.get("type"), "updated_at": metadata.get("updated_at")},
        "counts": {
            **expected,
            "multi_finish_source_prints": int(counts["multi_finish_source_prints"]),
            "finish_nonfoil": int(counts["finish_nonfoil"]),
            "finish_foil": int(counts["finish_foil"]),
            "finish_etched": int(counts["finish_etched"]),
        },
        "model": {
            "description": "SourcePrint stores source metadata once; exact shared-style Print rows remain one per Scryfall ID + finish; Print Search is stored once per SourcePrint.",
            **sizes,
        },
        "benchmarks_median_ms": benchmarks,
        "benchmark_sample": sample,
        "elapsed_seconds": round(time.perf_counter() - started, 2),
        "neon_writes": 0,
        "contract": {
            "exact_market_entity": "Print row",
            "source_provenance_entity": "SourcePrint row",
            "search_projection_entity": "SourcePrint row expanded to exact Prints by finish",
            "fmv_portfolio_compatibility": "preserves exact print_id as the physical/market entity",
        },
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
