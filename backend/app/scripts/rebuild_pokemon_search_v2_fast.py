from __future__ import annotations

import argparse
import csv
import io
import json
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import text

from app import db
from app.search_v2.facets import facets_for_game
from app.search_v2.normalization import build_search_text, compact_search_text, normalize_search_text
from app.search_v2.pokemon_indexer import LANGUAGE_LABELS, _card_attrs, _print_attrs


SOURCE = "tcgdex/cards-database"
SOURCE_VERSION = "771a8381c57c73182b9776657a15cd1166c66d36"
VARIANT_SOURCE = "tcgdex-variant-v2"
EXPECTED_CARDS = 21065
EXPECTED_VARIANT_PRINTS = 27241
EXPECTED_ADDITIONAL_PRINTS = 12692
EXPECTED_PRINTS = EXPECTED_CARDS + EXPECTED_ADDITIONAL_PRINTS
EXPECTED_FACETS = 23
STALE_SOURCE_IDS = ("sv1-1", "sv1-62")


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def _copy_buffer(rows: list[tuple]) -> io.StringIO:
    buffer = io.StringIO()
    writer = csv.writer(
        buffer,
        delimiter="\t",
        quotechar='"',
        quoting=csv.QUOTE_ALL,
        lineterminator="\n",
    )
    writer.writerows(rows)
    buffer.seek(0)
    return buffer


def _write_json(path: Path | None, payload: dict) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def run(*, report_path: Path | None = None) -> dict:
    db.init_engine()
    with db.SessionLocal() as session:
        game_id = int(session.execute(text("SELECT id FROM games WHERE slug='pokemon' LIMIT 1")).scalar_one())
        card_rows = [dict(row) for row in session.execute(text(
            """
            SELECT c.id, c.name, c.card_key, c.tcgdex_id, ca.attributes_json
            FROM cards c
            JOIN card_attributes ca ON ca.card_id=c.id
            WHERE c.game_id=:game
              AND ca.source=:source
              AND ca.source_version=:version
            ORDER BY c.id
            """
        ), {"game": game_id, "source": SOURCE, "version": SOURCE_VERSION}).mappings().all()]
        if len(card_rows) != EXPECTED_CARDS:
            raise AssertionError(f"Certified Pokémon card count moved: {len(card_rows)} != {EXPECTED_CARDS}")

        canonical_card_ids = [int(row["id"]) for row in card_rows]
        print_rows = [dict(row) for row in session.execute(text(
            """
            SELECT
              p.id, p.card_id, p.set_id, p.collector_number, p.language,
              p.rarity, p.variant, p.is_foil, p.print_key, p.tcgdex_id,
              pa.attributes_json AS print_attributes,
              s.code AS set_code, s.name AS set_name, s.release_date
            FROM prints p
            JOIN sets s ON s.id=p.set_id
            JOIN print_attributes pa ON pa.print_id=p.id
            WHERE p.card_id = ANY(:card_ids)
              AND pa.source=:source
              AND pa.source_version=:version
            ORDER BY p.id
            """
        ), {"card_ids": canonical_card_ids, "source": SOURCE, "version": SOURCE_VERSION}).mappings().all()]

        variant_identifier_count = int(session.execute(text(
            """
            SELECT COUNT(*)
            FROM print_identifiers pi
            JOIN prints p ON p.id=pi.print_id
            WHERE pi.source=:variant_source
              AND p.card_id = ANY(:card_ids)
            """
        ), {"variant_source": VARIANT_SOURCE, "card_ids": canonical_card_ids}).scalar_one())
        additional_print_count = int(session.execute(text(
            """
            SELECT COUNT(*)
            FROM prints p
            WHERE p.card_id = ANY(:card_ids)
              AND p.tcgdex_id IS NULL
              AND p.variant LIKE 'v2-%'
            """
        ), {"card_ids": canonical_card_ids}).scalar_one())
        stale_with_attrs = int(session.execute(text(
            """
            SELECT COUNT(*)
            FROM cards c JOIN card_attributes ca ON ca.card_id=c.id
            WHERE c.game_id=:game AND c.tcgdex_id = ANY(:stale)
            """
        ), {"game": game_id, "stale": list(STALE_SOURCE_IDS)}).scalar_one())
        session.rollback()

    if len(print_rows) != EXPECTED_PRINTS:
        raise AssertionError(f"Certified Pokémon exact Print count moved: {len(print_rows)} != {EXPECTED_PRINTS}")
    if variant_identifier_count != EXPECTED_VARIANT_PRINTS:
        raise AssertionError(
            f"Variant expansion is not certified: identifiers={variant_identifier_count} != {EXPECTED_VARIANT_PRINTS}"
        )
    if additional_print_count != EXPECTED_ADDITIONAL_PRINTS:
        raise AssertionError(
            f"Additional variant Prints moved: {additional_print_count} != {EXPECTED_ADDITIONAL_PRINTS}"
        )
    if stale_with_attrs:
        raise AssertionError(f"{stale_with_attrs} stale Pokémon identities unexpectedly gained canonical attributes")

    card_by_id = {int(row["id"]): row for row in card_rows}
    prints_by_card: dict[int, list[dict]] = {}
    for row in print_rows:
        prints_by_card.setdefault(int(row["card_id"]), []).append(row)

    card_stage: list[tuple] = []
    for card in card_rows:
        card_id = int(card["id"])
        attrs = _card_attrs(card.get("attributes_json") or {})
        related = prints_by_card.get(card_id, [])
        collectors = [str(row.get("collector_number") or "") for row in related if row.get("collector_number")]
        sets = [str(row.get("set_code") or "") for row in related if row.get("set_code")]
        rarities = [str(row.get("rarity") or "") for row in related if row.get("rarity")]
        aliases = [compact_search_text(value) for value in collectors + sets if compact_search_text(value)]
        card_stage.append((
            card_id,
            game_id,
            normalize_search_text(card["name"]),
            _json(aliases),
            _json([]),
            _json(attrs),
            build_search_text(card["name"], collectors, sets, rarities, attrs),
        ))

    print_stage: list[tuple] = []
    physical_finish_count = 0
    for row in print_rows:
        card = card_by_id[int(row["card_id"])]
        card_attrs = _card_attrs(card.get("attributes_json") or {})
        attrs = _print_attrs(row.get("print_attributes") or {}, card_attrs=card_attrs, rarity=row.get("rarity"))
        language = str(row.get("language") or "").strip().lower() or None
        exact_variant = str(row.get("variant") or "default").strip().lower() or "default"
        finish = str(attrs.get("finish") or "").strip().lower()
        family = finish or ("default" if exact_variant == "default" else "physical")
        if finish:
            physical_finish_count += 1
        collector = str(row.get("collector_number") or "").strip()
        set_code = str(row.get("set_code") or "").strip()
        aliases = [
            value for value in (
                compact_search_text(collector),
                compact_search_text(set_code),
                compact_search_text(card.get("tcgdex_id")),
            ) if value
        ]
        release_names = [value for value in (attrs.get("series"),) if value]
        search_text = build_search_text(
            card["name"], aliases, set_code, row.get("set_name"), collector,
            row.get("rarity"), exact_variant, family, language,
            LANGUAGE_LABELS.get(language or "", ""), attrs,
        )
        print_stage.append((
            int(row["id"]),
            int(row["card_id"]),
            game_id,
            normalize_search_text(card["name"]),
            normalize_search_text(set_code).replace(" ", "-") or None,
            normalize_search_text(collector).replace(" ", "-") or None,
            language,
            row.get("rarity"),
            exact_variant,
            family,
            _json(release_names),
            _json(aliases),
            _json([]),
            _json(attrs),
            search_text,
        ))

    if len(card_stage) != EXPECTED_CARDS or len(print_stage) != EXPECTED_PRINTS:
        raise AssertionError("Search staging counts do not match certified canonical scope")
    if physical_finish_count != EXPECTED_VARIANT_PRINTS:
        raise AssertionError(
            f"Physical finish coverage moved: {physical_finish_count} != {EXPECTED_VARIANT_PRINTS}"
        )

    facet_rows = facets_for_game("pokemon")
    if len(facet_rows) != EXPECTED_FACETS:
        raise AssertionError(f"Pokémon facet contract moved: {len(facet_rows)} != {EXPECTED_FACETS}")

    raw = db.engine.raw_connection()
    try:
        raw.autocommit = False
        cur = raw.cursor()
        cur.execute(
            """
            CREATE TEMP TABLE pokemon_card_search_stage (
              card_id BIGINT NOT NULL,
              game_id BIGINT NOT NULL,
              normalized_name TEXT NOT NULL,
              aliases_json JSONB,
              keywords_json JSONB,
              attributes_json JSONB,
              search_text TEXT NOT NULL
            ) ON COMMIT DROP;
            CREATE TEMP TABLE pokemon_print_search_stage (
              print_id BIGINT NOT NULL,
              card_id BIGINT NOT NULL,
              game_id BIGINT NOT NULL,
              normalized_name TEXT NOT NULL,
              normalized_set_code TEXT,
              normalized_collector_number TEXT,
              language TEXT,
              rarity TEXT,
              exact_variant TEXT,
              variant_family TEXT,
              release_names_json JSONB,
              aliases_json JSONB,
              keywords_json JSONB,
              attributes_json JSONB,
              search_text TEXT NOT NULL
            ) ON COMMIT DROP
            """
        )
        cur.copy_expert(
            """
            COPY pokemon_card_search_stage (
              card_id, game_id, normalized_name, aliases_json, keywords_json,
              attributes_json, search_text
            ) FROM STDIN WITH (FORMAT CSV, DELIMITER E'\\t', QUOTE '"', ESCAPE '"')
            """,
            _copy_buffer(card_stage),
        )
        cur.copy_expert(
            """
            COPY pokemon_print_search_stage (
              print_id, card_id, game_id, normalized_name, normalized_set_code,
              normalized_collector_number, language, rarity, exact_variant,
              variant_family, release_names_json, aliases_json, keywords_json,
              attributes_json, search_text
            ) FROM STDIN WITH (FORMAT CSV, DELIMITER E'\\t', QUOTE '"', ESCAPE '"')
            """,
            _copy_buffer(print_stage),
        )

        cur.execute("SELECT COUNT(*) FROM pokemon_card_search_stage")
        if int(cur.fetchone()[0]) != EXPECTED_CARDS:
            raise AssertionError("Card search COPY staging count mismatch")
        cur.execute("SELECT COUNT(*) FROM pokemon_print_search_stage")
        if int(cur.fetchone()[0]) != EXPECTED_PRINTS:
            raise AssertionError("Print search COPY staging count mismatch")

        cur.execute("DELETE FROM card_search_profiles WHERE game_id=%s", (game_id,))
        cur.execute("DELETE FROM print_search_profiles WHERE game_id=%s", (game_id,))
        cur.execute("DELETE FROM facet_definitions WHERE game_id=%s", (game_id,))

        cur.execute(
            """
            INSERT INTO card_search_profiles (
              card_id, game_id, normalized_name, aliases_json, keywords_json,
              attributes_json, search_text, updated_at
            )
            SELECT card_id, game_id, normalized_name, aliases_json, keywords_json,
                   attributes_json, search_text, now()
            FROM pokemon_card_search_stage
            """
        )
        cur.execute(
            """
            INSERT INTO print_search_profiles (
              print_id, card_id, game_id, normalized_name, normalized_set_code,
              normalized_collector_number, language, rarity, exact_variant,
              variant_family, release_names_json, aliases_json, keywords_json,
              attributes_json, search_text, updated_at
            )
            SELECT print_id, card_id, game_id, normalized_name, normalized_set_code,
                   normalized_collector_number, language, rarity, exact_variant,
                   variant_family, release_names_json, aliases_json, keywords_json,
                   attributes_json, search_text, now()
            FROM pokemon_print_search_stage
            """
        )

        for definition in facet_rows:
            row = dict(definition)
            cur.execute(
                """
                INSERT INTO facet_definitions (
                  game_id, scope, key, label, value_type, ui_type, group_name,
                  source_path, multi_value, filterable, sortable, searchable,
                  quick_filter, display_order, options_json, active, created_at, updated_at
                ) VALUES (
                  %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,now(),now()
                )
                """,
                (
                    game_id,
                    row["scope"], row["key"], row["label"], row["value_type"],
                    row["ui_type"], row.get("group_name"), row["source_path"],
                    bool(row.get("multi_value", False)), bool(row.get("filterable", True)),
                    bool(row.get("sortable", False)), bool(row.get("searchable", False)),
                    bool(row.get("quick_filter", False)), int(row.get("display_order", 0)),
                    _json(row.get("options_json")) if row.get("options_json") is not None else "null",
                    bool(row.get("active", True)),
                ),
            )

        cur.execute(
            """
            SELECT
              (SELECT COUNT(*) FROM card_search_profiles WHERE game_id=%s),
              (SELECT COUNT(*) FROM print_search_profiles WHERE game_id=%s),
              (SELECT COUNT(*) FROM facet_definitions WHERE game_id=%s),
              (SELECT COUNT(*) FROM print_search_profiles WHERE game_id=%s AND attributes_json->>'finish' IS NOT NULL),
              (SELECT COUNT(*)
                 FROM card_search_profiles csp
                 JOIN cards c ON c.id=csp.card_id
                WHERE csp.game_id=%s AND c.tcgdex_id = ANY(%s))
            """,
            (game_id, game_id, game_id, game_id, game_id, list(STALE_SOURCE_IDS)),
        )
        cards_after, prints_after, facets_after, finishes_after, stale_after = map(int, cur.fetchone())
        if cards_after != EXPECTED_CARDS:
            raise AssertionError(f"Card Search V2 postcondition failed: {cards_after}")
        if prints_after != EXPECTED_PRINTS:
            raise AssertionError(f"Print Search V2 postcondition failed: {prints_after}")
        if facets_after != EXPECTED_FACETS:
            raise AssertionError(f"Facet postcondition failed: {facets_after}")
        if finishes_after != EXPECTED_VARIANT_PRINTS:
            raise AssertionError(f"Physical finish Search V2 coverage failed: {finishes_after}")
        if stale_after:
            raise AssertionError(f"{stale_after} stale Pokémon IDs entered Search V2")
        raw.commit()
    except Exception:
        raw.rollback()
        raise
    finally:
        raw.close()

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": "postgres_copy_transactional_search_v2_rebuild",
        "game": "pokemon",
        "source": SOURCE,
        "source_version": SOURCE_VERSION,
        "card_profiles": EXPECTED_CARDS,
        "print_profiles": EXPECTED_PRINTS,
        "physical_variant_profiles": EXPECTED_VARIANT_PRINTS,
        "additional_variant_profiles": EXPECTED_ADDITIONAL_PRINTS,
        "facets": EXPECTED_FACETS,
        "stale_source_ids_excluded": list(STALE_SOURCE_IDS),
        "status": "pass",
    }
    _write_json(report_path, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report-path", type=Path)
    args = parser.parse_args()
    run(report_path=args.report_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
