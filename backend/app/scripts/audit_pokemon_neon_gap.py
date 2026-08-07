from __future__ import annotations

import json
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

import requests
from sqlalchemy import inspect, text

from app import db


TCGDEX_BASE = "https://api.tcgdex.net/v2/en"
TIMEOUT = 25
MAX_WORKERS = 8


def _request_json(session: requests.Session, path: str, attempts: int = 4):
    url = f"{TCGDEX_BASE}/{path.lstrip('/')}"
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            response = session.get(url, timeout=TIMEOUT)
            if response.status_code == 429:
                time.sleep(1.0 + attempt * 1.25)
                continue
            response.raise_for_status()
            return response.json()
        except Exception as exc:
            last_error = exc
            if attempt + 1 < attempts:
                time.sleep(0.5 * (2**attempt))
    raise RuntimeError(f"TCGdex request failed: {url}: {last_error}")


def _fetch_source_set(summary: dict) -> dict:
    set_id = str(summary.get("id") or "").strip()
    if not set_id:
        raise ValueError("TCGdex set summary without id")
    session = requests.Session()
    session.headers.update({"User-Agent": "dontripit-pokemon-gap-audit/2.0", "Accept": "application/json"})
    try:
        detail = _request_json(session, f"sets/{set_id}")
    finally:
        session.close()
    cards = detail.get("cards") if isinstance(detail, dict) else []
    cards = cards if isinstance(cards, list) else []
    return {
        "set_id": set_id,
        "set_name": str(detail.get("name") or summary.get("name") or "").strip(),
        "cards": [
            {
                "id": str(card.get("id") or "").strip(),
                "local_id": str(card.get("localId") or "").strip(),
                "name": str(card.get("name") or "").strip(),
                "image": card.get("image"),
            }
            for card in cards
            if isinstance(card, dict)
        ],
    }


def _load_tcgdex_source() -> tuple[list[dict], dict[str, dict]]:
    session = requests.Session()
    session.headers.update({"User-Agent": "dontripit-pokemon-gap-audit/2.0", "Accept": "application/json"})
    try:
        summaries = _request_json(session, "sets")
    finally:
        session.close()
    if not isinstance(summaries, list) or not summaries:
        raise AssertionError("TCGdex /sets returned no sets")

    sets: list[dict] = []
    errors: list[str] = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(_fetch_source_set, summary): summary for summary in summaries if isinstance(summary, dict)}
        for future in as_completed(futures):
            try:
                sets.append(future.result())
            except Exception as exc:
                summary = futures[future]
                errors.append(f"{summary.get('id')}: {exc}")
    if errors:
        raise AssertionError(f"TCGdex source requests failed for {len(errors)} sets: {errors[:5]}")

    sets.sort(key=lambda row: row["set_id"])
    cards: dict[str, dict] = {}
    duplicates: list[str] = []
    for set_row in sets:
        for card in set_row["cards"]:
            source_id = card["id"]
            if not source_id:
                continue
            if source_id in cards:
                duplicates.append(source_id)
            cards[source_id] = {**card, "set_id": set_row["set_id"], "set_name": set_row["set_name"]}
    if duplicates:
        raise AssertionError(f"TCGdex returned duplicate card IDs: {sorted(set(duplicates))[:20]}")
    return sets, cards


def _column_names(inspector, table: str) -> set[str]:
    return {str(column.get("name")) for column in inspector.get_columns(table)}


def _pokemon_game(session, tables: set[str]) -> dict | None:
    if "games" not in tables:
        return None
    return session.execute(text(
        "SELECT id, slug, name FROM games WHERE lower(slug) IN ('pokemon','pokémon') ORDER BY id LIMIT 1"
    )).mappings().first()


def _pokemon_print_filter(inspector, tables: set[str]) -> tuple[str, str]:
    """Return joins and predicate that scope a prints alias p to Pokémon."""
    if "prints" not in tables:
        raise AssertionError("prints table is missing")
    print_columns = _column_names(inspector, "prints")
    if "game_id" in print_columns:
        return "", "p.game_id = :game_id"
    if "card_id" in print_columns and "cards" in tables:
        card_columns = _column_names(inspector, "cards")
        if "game_id" in card_columns:
            return "JOIN cards c_scope ON c_scope.id = p.card_id", "c_scope.game_id = :game_id"
    raise AssertionError("Could not scope prints to Pokémon through game_id/card_id")


def _collect_direct_tcgdex_ids(session, inspector, tables: set[str], game_id: int) -> tuple[list[dict], list[str]]:
    rows: list[dict] = []
    evidence: list[str] = []
    if "prints" not in tables:
        return rows, evidence
    joins, predicate = _pokemon_print_filter(inspector, tables)
    columns = _column_names(inspector, "prints")
    candidates = sorted([name for name in columns if "tcgdex" in name.lower()])
    for column in candidates:
        evidence.append(f"prints.{column}")
        query = text(
            f"SELECT p.id AS print_id, CAST(p.{column} AS text) AS external_id "
            f"FROM prints p {joins} WHERE {predicate} AND p.{column} IS NOT NULL AND CAST(p.{column} AS text) <> ''"
        )
        for row in session.execute(query, {"game_id": game_id}).mappings().all():
            rows.append({"print_id": int(row["print_id"]), "external_id": str(row["external_id"]).strip(), "via": f"prints.{column}"})
    return rows, evidence


def _collect_identifier_tcgdex_ids(session, inspector, tables: set[str], game_id: int) -> tuple[list[dict], dict]:
    table = next((name for name in ("print_identifiers", "print_identifier", "identifiers") if name in tables), None)
    if not table:
        return [], {"table": None}
    columns = _column_names(inspector, table)
    if "print_id" not in columns:
        return [], {"table": table, "reason": "no_print_id"}

    source_columns = [
        name for name in sorted(columns)
        if any(token in name.lower() for token in ("source", "provider", "namespace", "scheme", "kind", "type"))
    ]
    value_columns = [
        name for name in sorted(columns)
        if name not in {"id", "print_id"}
        and any(token in name.lower() for token in ("value", "external", "identifier", "source_id"))
    ]
    if not source_columns or not value_columns:
        return [], {"table": table, "source_columns": source_columns, "value_columns": value_columns, "reason": "unresolved_columns"}

    joins, predicate = _pokemon_print_filter(inspector, tables)
    result: list[dict] = []
    matched_pairs: list[dict] = []
    for source_column in source_columns:
        for value_column in value_columns:
            try:
                query = text(
                    f"SELECT i.print_id, CAST(i.{value_column} AS text) AS external_id "
                    f"FROM {table} i JOIN prints p ON p.id=i.print_id {joins} "
                    f"WHERE {predicate} AND lower(CAST(i.{source_column} AS text)) LIKE '%tcgdex%' "
                    f"AND i.{value_column} IS NOT NULL AND CAST(i.{value_column} AS text) <> ''"
                )
                pair_rows = session.execute(query, {"game_id": game_id}).mappings().all()
            except Exception:
                continue
            if pair_rows:
                matched_pairs.append({"source_column": source_column, "value_column": value_column, "rows": len(pair_rows)})
                for row in pair_rows:
                    result.append({
                        "print_id": int(row["print_id"]),
                        "external_id": str(row["external_id"]).strip(),
                        "via": f"{table}.{source_column}+{value_column}",
                    })
    return result, {
        "table": table,
        "source_columns": source_columns,
        "value_columns": value_columns,
        "matched_pairs": matched_pairs,
    }


def run() -> dict:
    source_sets, source_cards = _load_tcgdex_source()

    db.init_engine()
    inspector = inspect(db.engine)
    tables = set(inspector.get_table_names())

    with db.SessionLocal() as session:
        game = _pokemon_game(session, tables)
        if not game:
            raise AssertionError("Pokémon game row is missing from Neon")
        game_id = int(game["id"])
        joins, predicate = _pokemon_print_filter(inspector, tables)

        direct_rows, direct_evidence = _collect_direct_tcgdex_ids(session, inspector, tables, game_id)
        identifier_rows, identifier_evidence = _collect_identifier_tcgdex_ids(session, inspector, tables, game_id)
        all_rows = direct_rows + identifier_rows

        ids_by_print: dict[int, set[str]] = defaultdict(set)
        vias_by_print: dict[int, set[str]] = defaultdict(set)
        for row in all_rows:
            external_id = row["external_id"]
            if not external_id:
                continue
            ids_by_print[row["print_id"]].add(external_id)
            vias_by_print[row["print_id"]].add(row["via"])

        db_external_ids: list[str] = []
        for values in ids_by_print.values():
            db_external_ids.extend(values)
        external_counts = Counter(db_external_ids)
        db_external_set = set(db_external_ids)
        source_external_set = set(source_cards)

        missing_ids = sorted(source_external_set - db_external_set)
        extra_ids = sorted(db_external_set - source_external_set)
        duplicate_db_ids = sorted([value for value, count in external_counts.items() if count > 1])

        print_count = int(session.execute(text(
            f"SELECT COUNT(*) FROM prints p {joins} WHERE {predicate}"
        ), {"game_id": game_id}).scalar_one())
        print_without_tcgdex = max(0, print_count - len(ids_by_print))

        missing_collector = 0
        if "collector_number" in _column_names(inspector, "prints"):
            missing_collector = int(session.execute(text(
                f"SELECT COUNT(*) FROM prints p {joins} WHERE {predicate} "
                "AND (p.collector_number IS NULL OR trim(CAST(p.collector_number AS text))='')"
            ), {"game_id": game_id}).scalar_one())

        image_covered_prints = 0
        if "print_images" in tables:
            image_covered_prints = int(session.execute(text(
                f"SELECT COUNT(DISTINCT p.id) FROM prints p {joins} "
                "JOIN print_images pi ON pi.print_id=p.id WHERE " + predicate
            ), {"game_id": game_id}).scalar_one())

        source_set_ids = {row["set_id"] for row in source_sets}
        source_set_expected = Counter(card["set_id"] for card in source_cards.values())
        matched_source_ids = source_external_set & db_external_set
        source_set_matched = Counter(source_cards[source_id]["set_id"] for source_id in matched_source_ids)
        missing_sets = sorted([set_id for set_id in source_set_ids if source_set_matched[set_id] == 0])
        partial_sets = [
            {
                "set_id": set_id,
                "set_name": next((row["set_name"] for row in source_sets if row["set_id"] == set_id), None),
                "source_cards": source_set_expected[set_id],
                "matched_cards": source_set_matched[set_id],
                "missing_cards": source_set_expected[set_id] - source_set_matched[set_id],
            }
            for set_id in sorted(source_set_ids)
            if 0 < source_set_matched[set_id] < source_set_expected[set_id]
        ]
        partial_sets.sort(key=lambda row: (-row["missing_cards"], row["set_id"]))

        report = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "mode": "read_only",
            "game": dict(game),
            "source": {
                "provider": "TCGdex REST v2 / en",
                "sets": len(source_sets),
                "cards": len(source_cards),
            },
            "neon": {
                "prints": print_count,
                "prints_with_tcgdex_identity": len(ids_by_print),
                "prints_without_tcgdex_identity": print_without_tcgdex,
                "prints_with_images": image_covered_prints,
                "prints_without_images": max(0, print_count - image_covered_prints),
                "prints_missing_collector_number": missing_collector,
                "identity_paths": {
                    "direct_columns": direct_evidence,
                    "identifier_table": identifier_evidence,
                },
            },
            "gap": {
                "matched_source_cards": len(matched_source_ids),
                "missing_source_cards": len(missing_ids),
                "extra_db_external_ids": len(extra_ids),
                "duplicate_db_external_ids": len(duplicate_db_ids),
                "source_sets_with_zero_matches": len(missing_sets),
                "source_sets_partial": len(partial_sets),
            },
            "missing_set_ids": missing_sets[:200],
            "partial_sets": partial_sets[:200],
            "missing_source_card_samples": [
                {"id": source_id, **source_cards[source_id]}
                for source_id in missing_ids[:100]
            ],
            "extra_db_id_samples": extra_ids[:100],
            "duplicate_db_id_samples": duplicate_db_ids[:100],
            "status": "pass",
        }

    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return report


def main() -> int:
    run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
