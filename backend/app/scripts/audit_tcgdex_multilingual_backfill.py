from __future__ import annotations

import json
import os
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import psycopg2
from psycopg2.extras import RealDictCursor
import requests


LANGUAGES = ("en", "es", "ja")
TCGDEX_BASE = "https://api.tcgdex.net/v2/{language}"
OUTPUT_JSON = Path("/tmp/tcgdex-multilingual-backfill-audit.json")
OUTPUT_MD = Path("/tmp/tcgdex-multilingual-backfill-audit.md")


def _request_json(session: requests.Session, url: str) -> Any:
    delay = 0.4
    last_error: Exception | None = None
    for attempt in range(1, 7):
        try:
            response = session.get(url, timeout=45)
            if response.status_code in (429, 500, 502, 503, 504):
                raise RuntimeError(f"retryable status={response.status_code}")
            response.raise_for_status()
            return response.json()
        except (requests.RequestException, RuntimeError, ValueError) as exc:
            last_error = exc
            if attempt == 6:
                break
            time.sleep(delay)
            delay *= 2
    raise RuntimeError(f"TCGdex request failed after retries: {url}: {last_error}")


def _fetch_remote_catalog(language: str) -> dict[str, Any]:
    with requests.Session() as http:
        http.headers.update(
            {
                "Accept": "application/json",
                "User-Agent": "Dontripit-Multilingual-ReadOnly-Audit/1.0",
            }
        )
        base = TCGDEX_BASE.format(language=language)
        sets = _request_json(http, f"{base}/sets")
        cards = _request_json(http, f"{base}/cards")

    if not isinstance(sets, list) or not isinstance(cards, list):
        raise RuntimeError(
            f"Unexpected TCGdex payload for {language}: "
            f"sets={type(sets).__name__}, cards={type(cards).__name__}"
        )

    set_rows: dict[str, dict] = {}
    for raw in sets:
        if not isinstance(raw, dict):
            continue
        external_id = str(raw.get("id") or "").strip()
        if not external_id:
            continue
        set_rows[external_id] = {
            "id": external_id,
            "name": str(raw.get("name") or "").strip() or None,
        }

    sorted_set_ids = sorted(set_rows, key=len, reverse=True)

    def resolve_set_id(card_external_id: str) -> str | None:
        for set_external_id in sorted_set_ids:
            if card_external_id.startswith(f"{set_external_id}-"):
                return set_external_id
        return None

    card_rows: dict[str, dict] = {}
    unresolved_set_ids: list[str] = []
    for raw in cards:
        if not isinstance(raw, dict):
            continue
        external_id = str(raw.get("id") or "").strip()
        if not external_id:
            continue
        set_external_id = resolve_set_id(external_id)
        if set_external_id is None and len(unresolved_set_ids) < 50:
            unresolved_set_ids.append(external_id)
        card_rows[external_id] = {
            "id": external_id,
            "local_id": str(raw.get("localId") or "").strip(),
            "name": str(raw.get("name") or "").strip() or None,
            "image": str(raw.get("image") or "").strip() or None,
            "set_id": set_external_id,
        }

    return {
        "language": language,
        "sets": set_rows,
        "cards": card_rows,
        "unresolved_set_card_ids": unresolved_set_ids,
    }


def _pct(numerator: int, denominator: int) -> float:
    if not denominator:
        return 0.0
    return round(100.0 * numerator / denominator, 4)


def _database_snapshot() -> dict[str, Any]:
    database_url = os.getenv("DATABASE_URL_UNPOOLED") or os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL_UNPOOLED or DATABASE_URL is required")

    conn = psycopg2.connect(
        database_url,
        connect_timeout=20,
        application_name="dontripit_tcgdex_multilingual_backfill_readonly_audit",
    )
    conn.set_session(readonly=True, autocommit=False)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SHOW transaction_read_only")
            transaction_read_only = str(cur.fetchone()["transaction_read_only"]).lower()
            if transaction_read_only != "on":
                raise RuntimeError(
                    f"Read-only guard failed: transaction_read_only={transaction_read_only!r}"
                )

            cur.execute(
                "SELECT current_database() AS database_name, current_user AS database_user"
            )
            db_identity = dict(cur.fetchone())

            cur.execute("SELECT id FROM games WHERE slug = 'pokemon'")
            game_row = cur.fetchone()
            if game_row is None:
                raise RuntimeError("Pokémon game row is missing from production catalog")
            pokemon_game_id = int(game_row["id"])

            cur.execute(
                """
                SELECT id, tcgdex_id, code, name
                FROM sets
                WHERE game_id = %s
                """,
                (pokemon_game_id,),
            )
            sets = [dict(row) for row in cur.fetchall()]

            cur.execute(
                """
                SELECT id, tcgdex_id, card_key, name
                FROM cards
                WHERE game_id = %s
                """,
                (pokemon_game_id,),
            )
            cards = [dict(row) for row in cur.fetchall()]

            cur.execute(
                """
                SELECT
                  p.id,
                  p.card_id,
                  p.set_id,
                  p.collector_number,
                  lower(trim(coalesce(p.language, ''))) AS language,
                  p.is_foil,
                  p.variant,
                  p.tcgdex_id
                FROM prints p
                JOIN cards c ON c.id = p.card_id
                WHERE c.game_id = %s
                """,
                (pokemon_game_id,),
            )
            prints = [dict(row) for row in cur.fetchall()]

            conn.rollback()
            return {
                "transaction_read_only": transaction_read_only,
                "database_identity": db_identity,
                "pokemon_game_id": pokemon_game_id,
                "sets": sets,
                "cards": cards,
                "prints": prints,
            }
    finally:
        conn.close()


def _plan(remote: dict[str, dict[str, Any]], db: dict[str, Any]) -> dict[str, Any]:
    db_sets_by_tcgdex = {
        str(row["tcgdex_id"]).strip(): row
        for row in db["sets"]
        if str(row.get("tcgdex_id") or "").strip()
    }
    db_cards_by_tcgdex = {
        str(row["tcgdex_id"]).strip(): row
        for row in db["cards"]
        if str(row.get("tcgdex_id") or "").strip()
    }

    existing_print_keys: set[tuple[int, int, str, str, bool, str]] = set()
    print_language_counts: dict[str, int] = defaultdict(int)
    for row in db["prints"]:
        language = str(row.get("language") or "unknown").strip().lower() or "unknown"
        print_language_counts[language] += 1
        existing_print_keys.add(
            (
                int(row["set_id"]),
                int(row["card_id"]),
                str(row.get("collector_number") or "").strip(),
                language,
                bool(row.get("is_foil")),
                str(row.get("variant") or "default"),
            )
        )

    en = remote["en"]
    es = remote["es"]
    ja = remote["ja"]

    overlap = {}
    for language in LANGUAGES:
        card_ids = set(remote[language]["cards"])
        set_ids = set(remote[language]["sets"])
        en_card_ids = set(en["cards"])
        en_set_ids = set(en["sets"])
        overlap[language] = {
            "remote_cards": len(card_ids),
            "remote_sets": len(set_ids),
            "card_ids_shared_with_en": len(card_ids & en_card_ids),
            "card_ids_shared_with_en_pct": _pct(len(card_ids & en_card_ids), len(card_ids)),
            "set_ids_shared_with_en": len(set_ids & en_set_ids),
            "set_ids_shared_with_en_pct": _pct(len(set_ids & en_set_ids), len(set_ids)),
            "unresolved_set_from_card_id_samples": remote[language]["unresolved_set_card_ids"],
        }

    es_eligible = 0
    es_missing_card = 0
    es_missing_set = 0
    es_unresolved_set = 0
    es_existing_print = 0
    es_candidates: list[dict] = []
    for card in es["cards"].values():
        external_id = card["id"]
        set_external_id = card.get("set_id")
        if not set_external_id:
            es_unresolved_set += 1
            continue
        db_card = db_cards_by_tcgdex.get(external_id)
        db_set = db_sets_by_tcgdex.get(set_external_id)
        if db_card is None:
            es_missing_card += 1
        if db_set is None:
            es_missing_set += 1
        if db_card is None or db_set is None:
            if len(es_candidates) < 50:
                es_candidates.append(
                    {
                        "id": external_id,
                        "set_id": set_external_id,
                        "local_id": card.get("local_id"),
                        "name": card.get("name"),
                        "missing_card": db_card is None,
                        "missing_set": db_set is None,
                    }
                )
            continue

        es_eligible += 1
        key = (
            int(db_set["id"]),
            int(db_card["id"]),
            str(card.get("local_id") or "").strip(),
            "es",
            False,
            "default",
        )
        if key in existing_print_keys:
            es_existing_print += 1

    ja_card_ids = set(ja["cards"])
    ja_set_ids = set(ja["sets"])
    global_card_collisions = ja_card_ids & set(db_cards_by_tcgdex)
    global_set_collisions = ja_set_ids & set(db_sets_by_tcgdex)

    ja_collision_samples = []
    for external_id in sorted(global_card_collisions)[:50]:
        remote_card = ja["cards"][external_id]
        canonical_card = db_cards_by_tcgdex[external_id]
        ja_collision_samples.append(
            {
                "external_id": external_id,
                "ja_name": remote_card.get("name"),
                "ja_local_id": remote_card.get("local_id"),
                "ja_set_id": remote_card.get("set_id"),
                "production_canonical_name": canonical_card.get("name"),
                "production_card_id": int(canonical_card["id"]),
            }
        )

    es_remote_cards = len(es["cards"])
    es_new_prints = max(es_eligible - es_existing_print, 0)

    return {
        "production_baseline": {
            "pokemon_sets": len(db["sets"]),
            "pokemon_cards": len(db["cards"]),
            "pokemon_prints": len(db["prints"]),
            "print_language_counts": dict(sorted(print_language_counts.items())),
            "sets_with_global_tcgdex_id": len(db_sets_by_tcgdex),
            "cards_with_global_tcgdex_id": len(db_cards_by_tcgdex),
        },
        "remote_identity_overlap": overlap,
        "spanish_overlay_plan": {
            "remote_cards": es_remote_cards,
            "exact_canonical_identity_eligible": es_eligible,
            "eligible_pct": _pct(es_eligible, es_remote_cards),
            "missing_canonical_card": es_missing_card,
            "missing_canonical_set": es_missing_set,
            "unresolved_set_from_card_id": es_unresolved_set,
            "already_existing_es_prints_matching_plan": es_existing_print,
            "estimated_new_es_prints": es_new_prints,
            "estimated_new_print_localizations": es_new_prints,
            "estimated_new_print_identifiers": es_new_prints,
            "estimated_card_identifier_links": es_eligible,
            "sample_ineligible_records": es_candidates,
        },
        "japanese_regional_plan": {
            "remote_sets": len(ja["sets"]),
            "remote_cards": len(ja["cards"]),
            "estimated_independent_sets": len(ja["sets"]),
            "estimated_independent_cards": len(ja["cards"]),
            "estimated_independent_prints": len(ja["cards"]),
            "estimated_print_localizations": len(ja["cards"]),
            "global_card_id_collisions_if_legacy_identity_were_reused": len(global_card_collisions),
            "global_set_id_collisions_if_legacy_identity_were_reused": len(global_set_collisions),
            "global_card_collision_pct": _pct(len(global_card_collisions), len(ja_card_ids)),
            "global_set_collision_pct": _pct(len(global_set_collisions), len(ja_set_ids)),
            "collision_samples": ja_collision_samples,
            "safety_policy": (
                "JA must use language-qualified set/card/print identifiers and independent "
                "regional Card/Set rows; global legacy tcgdex_id fields remain NULL."
            ),
        },
    }


def run() -> dict[str, Any]:
    db = _database_snapshot()
    remote = {language: _fetch_remote_catalog(language) for language in LANGUAGES}
    plan = _plan(remote, db)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": "strict-read-only-backfill-plan",
        "database_transaction_read_only": db["transaction_read_only"],
        "database_identity": db["database_identity"],
        "scope": {
            "game": "pokemon",
            "languages": list(LANGUAGES),
            "remote_source": "TCGdex REST v2",
            "database_writes": 0,
            "personal_data_tables_queried": False,
        },
        **plan,
    }


def render_markdown(report: dict[str, Any]) -> str:
    baseline = report["production_baseline"]
    overlap = report["remote_identity_overlap"]
    es = report["spanish_overlay_plan"]
    ja = report["japanese_regional_plan"]

    lines = [
        "# Don’tRipIt TCGdex multilingual backfill dry-run",
        "",
        f"Generated: `{report['generated_at']}`",
        "",
        f"DB transaction: **{report['database_transaction_read_only']}** (strict read-only)",
        "",
        "## Production Pokémon baseline",
        "",
        f"- Sets: **{baseline['pokemon_sets']}**",
        f"- Cards: **{baseline['pokemon_cards']}**",
        f"- Prints: **{baseline['pokemon_prints']}**",
        f"- Print languages: `{json.dumps(baseline['print_language_counts'], ensure_ascii=False, sort_keys=True)}`",
        f"- Sets with legacy global TCGdex ID: **{baseline['sets_with_global_tcgdex_id']}**",
        f"- Cards with legacy global TCGdex ID: **{baseline['cards_with_global_tcgdex_id']}**",
        "",
        "## Remote EN / ES / JA identity overlap",
        "",
        "| Lang | Remote cards | Cards shared with EN | Remote sets | Sets shared with EN |",
        "|---|---:|---:|---:|---:|",
    ]
    for language in LANGUAGES:
        row = overlap[language]
        lines.append(
            f"| {language} | {row['remote_cards']} | {row['card_ids_shared_with_en']} "
            f"({row['card_ids_shared_with_en_pct']:.2f}%) | {row['remote_sets']} | "
            f"{row['set_ids_shared_with_en']} ({row['set_ids_shared_with_en_pct']:.2f}%) |"
        )

    lines.extend(
        [
            "",
            "## Spanish overlay plan",
            "",
            f"- Remote ES cards: **{es['remote_cards']}**",
            f"- Exact canonical EN identity eligible: **{es['exact_canonical_identity_eligible']}** "
            f"({es['eligible_pct']:.2f}%)",
            f"- Missing canonical card identity: **{es['missing_canonical_card']}**",
            f"- Missing canonical set identity: **{es['missing_canonical_set']}**",
            f"- Unresolved set from remote card ID: **{es['unresolved_set_from_card_id']}**",
            f"- Existing ES prints matching the plan: **{es['already_existing_es_prints_matching_plan']}**",
            f"- Estimated new ES physical prints: **{es['estimated_new_es_prints']}**",
            "",
            "## Japanese regional plan",
            "",
            f"- Remote JA sets: **{ja['remote_sets']}**",
            f"- Remote JA cards / estimated independent prints: **{ja['remote_cards']}**",
            f"- Card IDs colliding with production global TCGdex IDs: "
            f"**{ja['global_card_id_collisions_if_legacy_identity_were_reused']}** "
            f"({ja['global_card_collision_pct']:.2f}%)",
            f"- Set IDs colliding with production global TCGdex IDs: "
            f"**{ja['global_set_id_collisions_if_legacy_identity_were_reused']}** "
            f"({ja['global_set_collision_pct']:.2f}%)",
            f"- Safety policy: {ja['safety_policy']}",
            "",
            "## Safety proof",
            "",
            "- The production database connection is forced read-only before catalog SELECTs.",
            "- No INSERT, UPDATE, DELETE, DDL, migration or backfill statement is executed.",
            "- No account, auth, email or other personal-data table is queried.",
            "- TCGdex is accessed only with public GET requests.",
            "- The DB transaction is rolled back before closing.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    report = run()
    OUTPUT_JSON.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    markdown = render_markdown(report)
    OUTPUT_MD.write_text(markdown + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
