from __future__ import annotations

import argparse
import csv
import io
import json
import os
import tempfile
import time
import unicodedata
import re
from collections import Counter
from contextlib import ExitStack
from pathlib import Path

import psycopg2

from app.ingest.connectors.scryfall_mtg_v2 import ScryfallMtgV2Connector
from app.mtg_identity_v2 import card_identity_key, finish_values
from app.scripts.benchmark_mtg_shadow_storage_v2 import _iter_bulk_rows


SOURCE = "scryfall/default_cards"
SOURCE_VERSION = "shadow-current"
NULL = r"\N"


def _clean(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _norm(value: object) -> str:
    text = unicodedata.normalize("NFKD", _clean(value))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", text.casefold()).strip()


def _null(value: object) -> str:
    cleaned = _clean(value)
    return cleaned if cleaned else NULL


def _is_paper(card: dict) -> bool:
    games = card.get("games")
    return not isinstance(games, list) or "paper" in {str(v or "").strip().lower() for v in games}


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


def _image(card: dict) -> str | None:
    image_uris = card.get("image_uris") or {}
    if isinstance(image_uris, dict):
        for key in ("normal", "large", "png", "small"):
            if _clean(image_uris.get(key)):
                return _clean(image_uris.get(key))
    for face in card.get("card_faces") or []:
        if not isinstance(face, dict):
            continue
        uris = face.get("image_uris") or {}
        if isinstance(uris, dict):
            for key in ("normal", "large", "png", "small"):
                if _clean(uris.get(key)):
                    return _clean(uris.get(key))
    return None


def _card_attrs(card: dict) -> dict:
    return {
        "layout": _clean(card.get("layout")),
        "type_line": _type_line(card),
        "mana_cost": _mana_cost(card),
        "mana_value": float(card.get("cmc") or 0),
        "colors": card.get("colors") or [],
        "color_identity": card.get("color_identity") or [],
        "oracle_text": _oracle_text(card),
        "keywords": card.get("keywords") or [],
        "legalities": card.get("legalities") or {},
        "power": card.get("power"),
        "toughness": card.get("toughness"),
        "loyalty": card.get("loyalty"),
        "defense": card.get("defense"),
    }


def _source_attrs(card: dict) -> dict:
    return {
        "set_type": _clean(card.get("set_type")),
        "frame_effects": card.get("frame_effects") or [],
        "full_art": bool(card.get("full_art")),
        "textless": bool(card.get("textless")),
        "booster": bool(card.get("booster")),
        "variation": bool(card.get("variation")),
        "security_stamp": card.get("security_stamp"),
    }


def _card_search_text(card: dict) -> str:
    return _norm(" ".join(filter(None, [
        _clean(card.get("name")),
        _type_line(card),
        _oracle_text(card),
        _mana_cost(card),
        " ".join(_clean(v) for v in card.get("keywords") or [] if _clean(v)),
        "".join(_clean(v) for v in card.get("color_identity") or [] if _clean(v)),
    ])))


def _source_search_text(card: dict) -> str:
    return _norm(" ".join(filter(None, [
        _clean(card.get("name")),
        _clean(card.get("set")),
        _clean(card.get("set_name")),
        _clean(card.get("collector_number")),
        _clean(card.get("lang")),
        _clean(card.get("rarity")),
        _clean(card.get("artist")),
        _clean(card.get("frame")),
        " ".join(_clean(v) for v in card.get("frame_effects") or [] if _clean(v)),
        _clean(card.get("border_color")),
    ])))


def _writer(stack: ExitStack, root: Path, name: str):
    handle = stack.enter_context((root / name).open("w+", encoding="utf-8", newline=""))
    writer = csv.writer(
        handle,
        delimiter="\t",
        quotechar='"',
        quoting=csv.QUOTE_MINIMAL,
        escapechar="\\",
        lineterminator="\n",
    )
    return handle, writer


def _copy(cur, table: str, columns: list[str], handle) -> None:
    handle.flush()
    handle.seek(0)
    columns_sql = ", ".join(f'"{col}"' for col in columns)
    cur.copy_expert(
        f'COPY public."{table}" ({columns_sql}) FROM STDIN '
        f"WITH (FORMAT CSV, DELIMITER E'\\t', QUOTE '\"', ESCAPE '\\', NULL '{NULL}')",
        handle,
    )


def _candidate_ddl(cur) -> None:
    cur.execute(
        """
        CREATE TABLE mtg_source_snapshots (
          id SERIAL PRIMARY KEY,
          source VARCHAR(100) NOT NULL,
          external_id VARCHAR(255) NOT NULL,
          source_updated_at TIMESTAMPTZ,
          metadata_json JSONB,
          captured_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          UNIQUE(source, external_id)
        );
        CREATE TABLE mtg_catalog_state (
          game_id INTEGER PRIMARY KEY REFERENCES games(id) ON DELETE CASCADE,
          active_snapshot_id INTEGER REFERENCES mtg_source_snapshots(id),
          updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        CREATE TABLE mtg_source_prints (
          id INTEGER PRIMARY KEY,
          scryfall_id VARCHAR(64) NOT NULL UNIQUE,
          card_id INTEGER NOT NULL REFERENCES cards(id) ON DELETE CASCADE,
          set_id INTEGER NOT NULL REFERENCES sets(id) ON DELETE CASCADE,
          collector_number VARCHAR(50) NOT NULL,
          language VARCHAR(16) NOT NULL,
          rarity VARCHAR(100),
          release_date DATE,
          artist VARCHAR(255),
          illustration_id VARCHAR(64),
          frame VARCHAR(32),
          border_color VARCHAR(32),
          promo BOOLEAN NOT NULL DEFAULT false,
          image_url TEXT,
          attributes_json JSONB NOT NULL DEFAULT '{}'::jsonb
        );
        CREATE INDEX ix_mtg_source_prints_card ON mtg_source_prints(card_id);
        CREATE INDEX ix_mtg_source_prints_set ON mtg_source_prints(set_id);
        CREATE INDEX ix_mtg_source_prints_collector ON mtg_source_prints(collector_number);
        CREATE INDEX ix_mtg_source_prints_language ON mtg_source_prints(language);
        CREATE INDEX ix_mtg_source_prints_rarity ON mtg_source_prints(rarity) WHERE rarity IS NOT NULL;
        CREATE INDEX ix_mtg_source_prints_release_date ON mtg_source_prints(release_date) WHERE release_date IS NOT NULL;
        CREATE INDEX ix_mtg_source_prints_natural ON mtg_source_prints(set_id, collector_number, language);
        CREATE INDEX ix_mtg_source_prints_attrs_gin ON mtg_source_prints USING gin(attributes_json);

        ALTER TABLE prints ADD COLUMN mtg_source_print_id INTEGER REFERENCES mtg_source_prints(id) ON DELETE CASCADE;
        CREATE INDEX ix_prints_mtg_source ON prints(mtg_source_print_id) WHERE mtg_source_print_id IS NOT NULL;
        CREATE UNIQUE INDEX uq_prints_mtg_source_variant
          ON prints(mtg_source_print_id, variant) WHERE mtg_source_print_id IS NOT NULL;

        CREATE TABLE mtg_source_print_search_profiles (
          source_print_id INTEGER PRIMARY KEY REFERENCES mtg_source_prints(id) ON DELETE CASCADE,
          card_id INTEGER NOT NULL REFERENCES cards(id) ON DELETE CASCADE,
          normalized_name TEXT NOT NULL,
          search_text TEXT NOT NULL,
          updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        CREATE INDEX ix_mtg_source_print_search_name_trgm
          ON mtg_source_print_search_profiles USING gin(normalized_name gin_trgm_ops);
        CREATE INDEX ix_mtg_source_print_search_text_trgm
          ON mtg_source_print_search_profiles USING gin(search_text gin_trgm_ops);
        """
    )

    # Same semantics, fewer NULL index entries for source-specific IDs.
    unique_replacements = [
        ("sets", "uq_sets_game_tcgdex", "uq_sets_game_tcgdex_partial", "game_id, tcgdex_id", "tcgdex_id IS NOT NULL"),
        ("sets", "uq_sets_game_yugioh", "uq_sets_game_yugioh_partial", "game_id, yugioh_id", "yugioh_id IS NOT NULL"),
        ("sets", "uq_sets_game_riftbound", "uq_sets_game_riftbound_partial", "game_id, riftbound_id", "riftbound_id IS NOT NULL"),
        ("cards", "uq_cards_game_tcgdex", "uq_cards_game_tcgdex_partial", "game_id, tcgdex_id", "tcgdex_id IS NOT NULL"),
        ("cards", "uq_cards_game_yugoprodeck", "uq_cards_game_yugoprodeck_partial", "game_id, yugoprodeck_id", "yugoprodeck_id IS NOT NULL"),
        ("cards", "uq_cards_game_riftbound", "uq_cards_game_riftbound_partial", "game_id, riftbound_id", "riftbound_id IS NOT NULL"),
        ("prints", "uq_prints_tcgdex_id", "uq_prints_tcgdex_id_partial", "tcgdex_id", "tcgdex_id IS NOT NULL"),
        ("prints", "uq_prints_yugioh_id", "uq_prints_yugioh_id_partial", "yugioh_id", "yugioh_id IS NOT NULL"),
        ("prints", "uq_prints_riftbound_id", "uq_prints_riftbound_id_partial", "riftbound_id", "riftbound_id IS NOT NULL"),
        ("prints", "uq_prints_print_key", "uq_prints_print_key_partial", "print_key", "print_key IS NOT NULL"),
    ]
    for table, constraint, index_name, columns, predicate in unique_replacements:
        cur.execute(f'ALTER TABLE {table} DROP CONSTRAINT {constraint}')
        cur.execute(f'CREATE UNIQUE INDEX {index_name} ON {table}({columns}) WHERE {predicate}')

    single_indexes = [
        ("sets", "ix_sets_tcgdex_id", "tcgdex_id"),
        ("sets", "ix_sets_yugioh_id", "yugioh_id"),
        ("sets", "ix_sets_riftbound_id", "riftbound_id"),
        ("cards", "ix_cards_tcgdex_id", "tcgdex_id"),
        ("cards", "ix_cards_yugoprodeck_id", "yugoprodeck_id"),
        ("cards", "ix_cards_riftbound_id", "riftbound_id"),
        ("prints", "ix_prints_tcgdex_id", "tcgdex_id"),
        ("prints", "ix_prints_yugioh_id", "yugioh_id"),
        ("prints", "ix_prints_riftbound_id", "riftbound_id"),
    ]
    for table, old_name, column in single_indexes:
        cur.execute(f'DROP INDEX IF EXISTS {old_name}')
        cur.execute(f'CREATE INDEX {old_name}_partial ON {table}({column}) WHERE {column} IS NOT NULL')
    cur.execute("DROP INDEX IF EXISTS ix_prints_print_key")


def _sizes(cur, tables: list[str]) -> tuple[dict, list[dict]]:
    table_sizes = {}
    for table in tables:
        cur.execute(
            """
            SELECT pg_relation_size(c.oid)::bigint,
                   pg_indexes_size(c.oid)::bigint,
                   pg_total_relation_size(c.oid)::bigint,
                   COALESCE(c.reltuples,0)::bigint
            FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname='public' AND c.relname=%s AND c.relkind='r'
            """,
            (table,),
        )
        row = cur.fetchone()
        if row:
            heap, idx, total, rows = map(int, row)
            table_sizes[table] = {
                "heap_bytes": heap,
                "index_bytes": idx,
                "total_bytes": total,
                "total_mib": round(total / 1024 / 1024, 2),
                "estimated_rows": rows,
            }
    cur.execute(
        """
        SELECT t.relname, i.relname, pg_relation_size(i.oid)::bigint
        FROM pg_class i
        JOIN pg_index x ON x.indexrelid=i.oid
        JOIN pg_class t ON t.oid=x.indrelid
        JOIN pg_namespace n ON n.oid=t.relnamespace
        WHERE n.nspname='public' AND t.relname = ANY(%s)
        ORDER BY pg_relation_size(i.oid) DESC, t.relname, i.relname
        """,
        (tables,),
    )
    indexes = [
        {"table": table, "index": name, "bytes": int(size), "mib": round(int(size)/1024/1024, 3)}
        for table, name, size in cur.fetchall()
    ]
    return table_sizes, indexes


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
    seen_scryfall: set[str] = set()

    with tempfile.TemporaryDirectory(prefix="mtg-prod-schema-v3-") as temp_dir, ExitStack() as stack:
        root = Path(temp_dir)
        sets_f, sets_w = _writer(stack, root, "sets.tsv")
        cards_f, cards_w = _writer(stack, root, "cards.tsv")
        attrs_f, attrs_w = _writer(stack, root, "card_attrs.tsv")
        card_search_f, card_search_w = _writer(stack, root, "card_search.tsv")
        source_f, source_w = _writer(stack, root, "source_prints.tsv")
        source_search_f, source_search_w = _writer(stack, root, "source_search.tsv")
        prints_f, prints_w = _writer(stack, root, "prints.tsv")

        next_set_id = next_card_id = next_source_id = next_print_id = 1
        for card in _iter_bulk_rows(connector, download_url):
            if not isinstance(card, dict) or not _is_paper(card):
                continue
            sid = _clean(card.get("id"))
            if not sid or sid in seen_scryfall:
                raise AssertionError(f"Missing/duplicate Scryfall id: {sid}")
            seen_scryfall.add(sid)
            counts["source_prints"] += 1

            set_code = _clean(card.get("set")).lower()
            set_id = set_ids.get(set_code)
            if set_id is None:
                set_id = next_set_id; next_set_id += 1; set_ids[set_code] = set_id
                sets_w.writerow([
                    set_id, 1, set_code, NULL, NULL, NULL,
                    _clean(card.get("set_name")) or set_code.upper(), _null(card.get("released_at")),
                ])

            identity = card_identity_key(card)
            card_id = card_ids.get(identity)
            if card_id is None:
                card_id = next_card_id; next_card_id += 1; card_ids[identity] = card_id
                attrs = _card_attrs(card)
                name = _clean(card.get("name"))
                cards_w.writerow([
                    card_id, 1, name, identity, _null(card.get("oracle_id")), NULL, NULL, NULL,
                ])
                attrs_w.writerow([
                    card_id, json.dumps(attrs, ensure_ascii=False, separators=(",", ":")), SOURCE, SOURCE_VERSION,
                ])
                card_search_w.writerow([
                    card_id, 1, _norm(name), "[]", "[]",
                    json.dumps(attrs, ensure_ascii=False, separators=(",", ":")), _card_search_text(card),
                ])

            source_id = next_source_id; next_source_id += 1
            source_w.writerow([
                source_id, sid, card_id, set_id, _clean(card.get("collector_number")),
                _clean(card.get("lang")).lower(), _null(card.get("rarity")), _null(card.get("released_at")),
                _null(card.get("artist")), _null(card.get("illustration_id")), _null(card.get("frame")),
                _null(card.get("border_color")), bool(card.get("promo")), _null(_image(card)),
                json.dumps(_source_attrs(card), ensure_ascii=False, separators=(",", ":")),
            ])
            source_search_w.writerow([source_id, card_id, _norm(card.get("name")), _source_search_text(card)])

            finishes = finish_values(card)
            if len(finishes) > 1:
                counts["multi_finish_source_prints"] += 1
            for finish in finishes:
                counts[f"finish_{finish}"] += 1
                counts["exact_prints"] += 1
                print_id = next_print_id; next_print_id += 1
                prints_w.writerow([
                    print_id, set_id, card_id, _clean(card.get("collector_number")),
                    _clean(card.get("lang")).lower(), _null(card.get("rarity")), finish == "foil", finish,
                    NULL, NULL, NULL, NULL, NULL, source_id,
                ])

        conn = psycopg2.connect(database_url); conn.autocommit = True
        try:
            cur = conn.cursor()
            cur.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
            _candidate_ddl(cur)
            cur.execute("INSERT INTO games(id,slug,name) VALUES (1,'mtg','Magic: The Gathering')")
            external_id = _clean(metadata.get("updated_at")) or _clean(metadata.get("id")) or "current"
            cur.execute(
                "INSERT INTO mtg_source_snapshots(source,external_id,source_updated_at,metadata_json) VALUES (%s,%s,%s,%s::jsonb) RETURNING id",
                (SOURCE, external_id, metadata.get("updated_at"), json.dumps({k: metadata.get(k) for k in ("id","type","name","updated_at","size","compressed_size") if metadata.get(k) is not None})),
            )
            snapshot_id = int(cur.fetchone()[0])
            cur.execute("INSERT INTO mtg_catalog_state(game_id,active_snapshot_id) VALUES (1,%s)", (snapshot_id,))

            _copy(cur, "sets", ["id","game_id","code","tcgdex_id","yugioh_id","riftbound_id","name","release_date"], sets_f)
            _copy(cur, "cards", ["id","game_id","name","card_key","oracle_id","tcgdex_id","yugoprodeck_id","riftbound_id"], cards_f)
            _copy(cur, "card_attributes", ["card_id","attributes_json","source","source_version"], attrs_f)
            _copy(cur, "card_search_profiles", ["card_id","game_id","normalized_name","aliases_json","keywords_json","attributes_json","search_text"], card_search_f)
            _copy(cur, "mtg_source_prints", ["id","scryfall_id","card_id","set_id","collector_number","language","rarity","release_date","artist","illustration_id","frame","border_color","promo","image_url","attributes_json"], source_f)
            _copy(cur, "mtg_source_print_search_profiles", ["source_print_id","card_id","normalized_name","search_text"], source_search_f)
            _copy(cur, "prints", ["id","set_id","card_id","collector_number","language","rarity","is_foil","variant","print_key","scryfall_id","tcgdex_id","yugioh_id","riftbound_id","mtg_source_print_id"], prints_f)

            tracked = ["sets","cards","card_attributes","card_search_profiles","prints","mtg_source_snapshots","mtg_catalog_state","mtg_source_prints","mtg_source_print_search_profiles"]
            for table in tracked:
                cur.execute(f"ANALYZE {table}")

            expected = {"sets": len(set_ids), "cards": len(card_ids), "source_prints": counts["source_prints"], "exact_prints": counts["exact_prints"]}
            queries = {
                "sets": "SELECT COUNT(*) FROM sets WHERE game_id=1",
                "cards": "SELECT COUNT(*) FROM cards WHERE game_id=1",
                "source_prints": "SELECT COUNT(*) FROM mtg_source_prints",
                "exact_prints": "SELECT COUNT(*) FROM prints WHERE mtg_source_print_id IS NOT NULL",
            }
            for key, sql in queries.items():
                cur.execute(sql); actual = int(cur.fetchone()[0])
                if actual != int(expected[key]):
                    raise AssertionError(f"{key}: {actual} != {expected[key]}")
            cur.execute("SELECT COUNT(*) FROM (SELECT mtg_source_print_id,variant FROM prints WHERE mtg_source_print_id IS NOT NULL GROUP BY 1,2 HAVING COUNT(*)>1) q")
            if int(cur.fetchone()[0]):
                raise AssertionError("MTG source+finish collision in production schema")
            cur.execute("SELECT COUNT(*) FROM source_records")
            source_records = int(cur.fetchone()[0])
            if source_records != 0:
                raise AssertionError("Per-card SourceRecords appeared in dedicated MTG shadow path")

            sizes, indexes = _sizes(cur, tracked)
            cur.execute("SELECT pg_database_size(current_database())")
            db_bytes = int(cur.fetchone()[0])
            cur.close()
        finally:
            conn.close()

    tracked_total = sum(v["total_bytes"] for v in sizes.values())
    report = {
        "status": "pass",
        "mode": "ephemeral_actual_shared_schema_mtg_model_d_shadow_v3",
        "source_updated_at": metadata.get("updated_at"),
        "counts": {
            "sets": len(set_ids), "cards": len(card_ids), "source_prints": int(counts["source_prints"]),
            "exact_prints": int(counts["exact_prints"]), "multi_finish_source_prints": int(counts["multi_finish_source_prints"]),
            "finish_nonfoil": int(counts["finish_nonfoil"]), "finish_foil": int(counts["finish_foil"]),
            "finish_etched": int(counts["finish_etched"]), "source_records": 0,
        },
        "tracked_relation_sizes": sizes,
        "tracked_indexes": indexes,
        "tracked_total_mib": round(tracked_total/1024/1024, 2),
        "whole_shadow_database_mib": round(db_bytes/1024/1024, 2),
        "elapsed_seconds": round(time.perf_counter()-started, 2),
        "candidate": {
            "source_print": "mtg_source_prints",
            "exact_print": "shared prints.mtg_source_print_id + variant=finish",
            "search": "shared card_search_profiles + mtg_source_print_search_profiles",
            "provenance": "snapshot-level, zero per-card SourceRecord rows",
            "external_indexes": "partial non-null source-specific indexes",
        },
        "neon_writes": 0,
    }
    if report_path:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url", default=os.getenv("SHADOW_DATABASE_URL"))
    parser.add_argument("--report-path", type=Path, default=None)
    args = parser.parse_args()
    if not args.database_url:
        raise SystemExit("SHADOW_DATABASE_URL/--database-url required")
    run(database_url=args.database_url, report_path=args.report_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
