from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import date, datetime
from pathlib import Path
from typing import Any

import psycopg2
import requests


LANGUAGES = ("es", "ja")
TCGDEX_BASE = "https://api.tcgdex.net/v2/{language}"
BASELINE_PATH = Path("/tmp/pokemon-multilingual-ephemeral-baseline.json")
REPORT_PATH = Path("/tmp/pokemon-multilingual-ephemeral-validation.json")

SET_COLUMNS = (
    "id",
    "game_id",
    "code",
    "tcgdex_id",
    "yugioh_id",
    "riftbound_id",
    "name",
    "release_date",
    "created_at",
)
CARD_COLUMNS = (
    "id",
    "game_id",
    "name",
    "card_key",
    "oracle_id",
    "tcgdex_id",
    "yugoprodeck_id",
    "riftbound_id",
    "created_at",
)


def _request_json(session: requests.Session, url: str) -> Any:
    response = session.get(url, timeout=60)
    response.raise_for_status()
    return response.json()


def _physical_remote(language: str) -> dict[str, Any]:
    base = TCGDEX_BASE.format(language=language)
    with requests.Session() as http:
        http.headers.update(
            {
                "Accept": "application/json",
                "User-Agent": "Dontripit-Multilingual-Ephemeral-Validation/1.0",
            }
        )
        series = _request_json(http, f"{base}/series")
        sets = _request_json(http, f"{base}/sets")
        cards = _request_json(http, f"{base}/cards")
        if not isinstance(series, list) or not isinstance(sets, list) or not isinstance(cards, list):
            raise RuntimeError(f"Unexpected TCGdex catalog payload for {language}")

        series_ids = {
            str(item.get("id") or "").strip()
            for item in series
            if isinstance(item, dict) and str(item.get("id") or "").strip()
        }
        pocket_set_ids: set[str] = set()
        if "tcgp" in series_ids:
            pocket = _request_json(http, f"{base}/series/tcgp")
            if not isinstance(pocket, dict):
                raise RuntimeError(f"Unexpected tcgp payload for {language}")
            pocket_set_ids = {
                str(item.get("id") or "").strip()
                for item in (pocket.get("sets") or [])
                if isinstance(item, dict) and str(item.get("id") or "").strip()
            }
            if not pocket_set_ids:
                raise RuntimeError(f"Published tcgp series has no sets for {language}")

    set_ids = {
        str(item.get("id") or "").strip()
        for item in sets
        if isinstance(item, dict) and str(item.get("id") or "").strip()
    }
    physical_set_ids = set_ids - pocket_set_ids
    sorted_set_ids = sorted(set_ids, key=len, reverse=True)

    physical_card_ids: set[str] = set()
    unresolved: list[str] = []
    for item in cards:
        if not isinstance(item, dict):
            continue
        card_id = str(item.get("id") or "").strip()
        if not card_id:
            continue
        matched_set_id = next(
            (set_id for set_id in sorted_set_ids if card_id.startswith(f"{set_id}-")),
            None,
        )
        if matched_set_id is None:
            unresolved.append(card_id)
            continue
        if matched_set_id not in pocket_set_ids:
            physical_card_ids.add(card_id)

    if unresolved:
        raise RuntimeError(
            f"Unresolved TCGdex card/set identity for {language}: "
            f"count={len(unresolved)} samples={unresolved[:10]}"
        )

    return {
        "set_ids": physical_set_ids,
        "card_ids": physical_card_ids,
        "pocket_set_ids": pocket_set_ids,
        "tcgp_published": "tcgp" in series_ids,
    }


def _json_value(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def _hash_query(cur, table: str, columns: tuple[str, ...], where: str, params: tuple = ()) -> str:
    quoted = ", ".join(f'"{column}"' for column in columns)
    cur.execute(f'SELECT {quoted} FROM "{table}" WHERE {where} ORDER BY id', params)
    rows = [list(map(_json_value, row)) for row in cur.fetchall()]
    encoded = json.dumps(rows, ensure_ascii=False, separators=(",", ":"), default=str).encode(
        "utf-8"
    )
    return hashlib.sha256(encoded).hexdigest()


def _scalar(cur, query: str, params: tuple = ()) -> int:
    cur.execute(query, params)
    return int(cur.fetchone()[0] or 0)


def _external_ids(cur, table: str, source: str) -> set[str]:
    cur.execute(f'SELECT external_id FROM "{table}" WHERE source = %s', (source,))
    return {str(row[0]) for row in cur.fetchall()}


def _catalog_state(cur) -> dict[str, int]:
    tables = (
        "sets",
        "cards",
        "prints",
        "print_images",
        "print_identifiers",
        "set_identifiers",
        "card_identifiers",
        "print_localizations",
    )
    return {table: _scalar(cur, f'SELECT count(*) FROM "{table}"') for table in tables}


def validate(*, snapshot_path: Path | None = None, compare_path: Path | None = None) -> dict[str, Any]:
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL is required")
    baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    remote = {language: _physical_remote(language) for language in LANGUAGES}

    conn = psycopg2.connect(
        database_url,
        connect_timeout=20,
        application_name="dontripit_multilingual_ephemeral_validation",
    )
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM games WHERE slug = 'pokemon'")
            game_row = cur.fetchone()
            if game_row is None:
                raise RuntimeError("Ephemeral Pokémon game row is missing")
            game_id = int(game_row[0])

            canonical_set_hash = _hash_query(
                cur,
                "sets",
                SET_COLUMNS,
                "game_id = %s AND tcgdex_id IS NOT NULL",
                (game_id,),
            )
            canonical_card_hash = _hash_query(
                cur,
                "cards",
                CARD_COLUMNS,
                "game_id = %s AND tcgdex_id IS NOT NULL",
                (game_id,),
            )
            if canonical_set_hash != baseline["sets_sha256"]:
                raise RuntimeError("Canonical Pokémon Set rows changed during multilingual ingest")
            if canonical_card_hash != baseline["cards_sha256"]:
                raise RuntimeError("Canonical Pokémon Card rows changed during multilingual ingest")

            state = _catalog_state(cur)
            language_reports: dict[str, Any] = {}
            for language in LANGUAGES:
                source = f"tcgdex:{language}"
                expected_cards = remote[language]["card_ids"]
                expected_sets = remote[language]["set_ids"]
                actual_print_ids = _external_ids(cur, "print_identifiers", source)
                actual_card_ids = _external_ids(cur, "card_identifiers", source)
                actual_set_ids = _external_ids(cur, "set_identifiers", source)

                if actual_print_ids != expected_cards:
                    missing = sorted(expected_cards - actual_print_ids)[:25]
                    extra = sorted(actual_print_ids - expected_cards)[:25]
                    raise RuntimeError(
                        f"{language} print identifier coverage mismatch: "
                        f"expected={len(expected_cards)} actual={len(actual_print_ids)} "
                        f"missing={missing} extra={extra}"
                    )
                if actual_card_ids != expected_cards:
                    raise RuntimeError(
                        f"{language} card identifier coverage mismatch: "
                        f"expected={len(expected_cards)} actual={len(actual_card_ids)}"
                    )
                if actual_set_ids != expected_sets:
                    missing = sorted(expected_sets - actual_set_ids)[:25]
                    extra = sorted(actual_set_ids - expected_sets)[:25]
                    raise RuntimeError(
                        f"{language} set identifier coverage mismatch: "
                        f"expected={len(expected_sets)} actual={len(actual_set_ids)} "
                        f"missing={missing} extra={extra}"
                    )

                print_count = _scalar(
                    cur,
                    "SELECT count(*) FROM prints WHERE lower(language) = %s",
                    (language,),
                )
                localization_count = _scalar(
                    cur,
                    "SELECT count(*) FROM print_localizations WHERE language = %s AND source = 'tcgdex'",
                    (language,),
                )
                if print_count != len(expected_cards) or localization_count != len(expected_cards):
                    raise RuntimeError(
                        f"{language} physical/localization cardinality mismatch: "
                        f"prints={print_count} localizations={localization_count} expected={len(expected_cards)}"
                    )

                global_print_ids = _scalar(
                    cur,
                    "SELECT count(*) FROM prints WHERE lower(language) = %s AND tcgdex_id IS NOT NULL",
                    (language,),
                )
                if global_print_ids:
                    raise RuntimeError(
                        f"Safety violation: {global_print_ids} non-English prints own global tcgdex_id ({language})"
                    )

                if language == "es":
                    noncanonical = _scalar(
                        cur,
                        """
                        SELECT count(*)
                        FROM prints p
                        JOIN cards c ON c.id = p.card_id
                        JOIN sets s ON s.id = p.set_id
                        WHERE lower(p.language) = 'es'
                          AND (c.tcgdex_id IS NULL OR s.tcgdex_id IS NULL)
                        """,
                    )
                    if noncanonical:
                        raise RuntimeError(
                            f"Spanish overlay created {noncanonical} prints outside canonical EN identity"
                        )
                else:
                    regional_with_global_identity = _scalar(
                        cur,
                        """
                        SELECT count(*)
                        FROM prints p
                        JOIN cards c ON c.id = p.card_id
                        JOIN sets s ON s.id = p.set_id
                        WHERE lower(p.language) = 'ja'
                          AND (c.tcgdex_id IS NOT NULL OR s.tcgdex_id IS NOT NULL)
                        """,
                    )
                    if regional_with_global_identity:
                        raise RuntimeError(
                            "Japanese regional catalog leaked into legacy global TCGdex identity"
                        )

                pocket_leaks = actual_set_ids & remote[language]["pocket_set_ids"]
                if pocket_leaks:
                    raise RuntimeError(
                        f"Pokémon TCG Pocket leaked into physical {language} catalog: {sorted(pocket_leaks)}"
                    )

                language_reports[language] = {
                    "expected_physical_sets": len(expected_sets),
                    "expected_physical_cards": len(expected_cards),
                    "set_identifiers": len(actual_set_ids),
                    "card_identifiers": len(actual_card_ids),
                    "print_identifiers": len(actual_print_ids),
                    "prints": print_count,
                    "localizations": localization_count,
                    "global_tcgdex_print_ids": global_print_ids,
                    "tcgp_published": remote[language]["tcgp_published"],
                    "tcgp_sets_excluded": len(remote[language]["pocket_set_ids"]),
                }

            report = {
                "target": "ephemeral-postgresql-only",
                "production_writes": 0,
                "canonical_sets_unchanged": True,
                "canonical_cards_unchanged": True,
                "baseline_sets": baseline["sets"],
                "baseline_cards": baseline["cards"],
                "catalog_state": state,
                "languages": language_reports,
            }

            if compare_path is not None:
                previous = json.loads(compare_path.read_text(encoding="utf-8"))
                if previous.get("catalog_state") != state:
                    raise RuntimeError(
                        "Idempotency failure: stable catalog table counts changed on rerun: "
                        f"before={previous.get('catalog_state')} after={state}"
                    )
                report["idempotent_rerun"] = True

            if snapshot_path is not None:
                snapshot_path.write_text(
                    json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n",
                    encoding="utf-8",
                )

            REPORT_PATH.write_text(
                json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n",
                encoding="utf-8",
            )
            print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
            return report
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot", type=Path, default=None)
    parser.add_argument("--compare", type=Path, default=None)
    args = parser.parse_args()
    validate(snapshot_path=args.snapshot, compare_path=args.compare)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
