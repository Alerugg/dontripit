from __future__ import annotations

import argparse
import json
import os
from collections import Counter, defaultdict
from pathlib import Path

import psycopg2


def _db_url() -> str:
    url = os.getenv("DATABASE_URL_UNPOOLED") or os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError("No production database URL configured")
    if url.startswith("postgresql+psycopg2://"):
        return "postgresql://" + url[len("postgresql+psycopg2://"):]
    if url.startswith("postgres://"):
        return "postgresql://" + url[len("postgres://"):]
    return url


def _jsonl(path: Path):
    with path.open("r", encoding="utf-8") as handle:
        for raw in handle:
            line = raw.strip()
            if line:
                value = json.loads(line)
                if isinstance(value, dict):
                    yield value


def _top(counter: Counter, limit: int = 30) -> dict[str, int]:
    return {str(key): int(value) for key, value in counter.most_common(limit)}


def run(*, snapshot_dir: Path, output: Path) -> dict:
    source_print_keys: set[str] = set()
    source_ids: set[str] = set()
    source_finishes: dict[str, set[str]] = defaultdict(set)
    source_languages = Counter()
    for row in _jsonl(snapshot_dir / "prints.jsonl"):
        key = str(row.get("print_key") or "")
        sid = str(row.get("scryfall_id") or "")
        finish = str(row.get("variant") or "")
        language = str(row.get("language") or "<null>")
        if key:
            source_print_keys.add(key)
        if sid:
            source_ids.add(sid)
            if finish:
                source_finishes[sid].add(finish)
        source_languages[language] += 1

    counts = Counter()
    extra_languages = Counter()
    extra_variants = Counter()
    extra_sets = Counter()
    extra_id_absent_languages = Counter()
    extra_id_present_languages = Counter()
    id_absent_examples = []
    id_present_examples = []
    malformed_examples = []
    extra_unique_ids: set[str] = set()
    exact_unique_ids: set[str] = set()

    conn = psycopg2.connect(_db_url())
    try:
        conn.set_session(readonly=True, autocommit=False)
        with conn.cursor() as cur:
            cur.execute("SET TRANSACTION READ ONLY")
            cur.execute("SELECT id FROM games WHERE slug='mtg'")
            game_id = int(cur.fetchone()[0])
            cur.execute(
                """
                SELECT p.print_key,p.scryfall_id,p.variant,p.language,s.code,p.collector_number
                FROM prints p
                JOIN cards c ON c.id=p.card_id
                JOIN sets s ON s.id=p.set_id
                WHERE c.game_id=%s
                """,
                (game_id,),
            )
            for print_key, scryfall_id, variant, language, set_code, collector_number in cur.fetchall():
                key = str(print_key or "")
                sid = str(scryfall_id or "")
                finish = str(variant or "")
                lang = str(language or "<null>")
                expected_key = f"mtg:scryfall:{sid}:{finish}" if sid and finish else ""

                if key.startswith("mtg:scryfall:"):
                    counts["production_exact_v2_prints"] += 1
                    if sid:
                        exact_unique_ids.add(sid)
                    if expected_key != key:
                        counts["production_exact_key_field_mismatches"] += 1
                        if len(malformed_examples) < 30:
                            malformed_examples.append({
                                "print_key": key,
                                "scryfall_id": sid,
                                "variant": finish,
                                "language": lang,
                                "set_code": str(set_code),
                            })
                else:
                    counts["production_non_v2_prints"] += 1
                    continue

                if key in source_print_keys:
                    counts["current_source_exact_matches"] += 1
                    continue

                counts["production_exact_extras"] += 1
                extra_unique_ids.add(sid)
                extra_languages[lang] += 1
                extra_variants[finish or "<null>"] += 1
                extra_sets[str(set_code)] += 1
                row = {
                    "print_key": key,
                    "scryfall_id": sid,
                    "variant": finish,
                    "language": lang,
                    "set_code": str(set_code),
                    "collector_number": str(collector_number),
                    "current_source_finishes_for_id": sorted(source_finishes.get(sid) or []),
                }
                if sid in source_ids:
                    counts["extra_source_id_present_finish_absent"] += 1
                    extra_id_present_languages[lang] += 1
                    if len(id_present_examples) < 30:
                        id_present_examples.append(row)
                else:
                    counts["extra_source_id_absent_from_current_default_cards"] += 1
                    extra_id_absent_languages[lang] += 1
                    if len(id_absent_examples) < 30:
                        id_absent_examples.append(row)
        conn.rollback()
    finally:
        conn.close()

    if counts["production_exact_extras"] != (
        counts["extra_source_id_present_finish_absent"]
        + counts["extra_source_id_absent_from_current_default_cards"]
    ):
        raise AssertionError("Extra Print classification does not reconcile")

    report = {
        "status": "pass",
        "mode": "read-only-production-extra-print-classification",
        "production_writes": 0,
        "source": {
            "exact_prints": len(source_print_keys),
            "unique_scryfall_ids": len(source_ids),
            "languages": _top(source_languages),
        },
        "production": {
            **{key: int(value) for key, value in sorted(counts.items())},
            "unique_exact_scryfall_ids": len(exact_unique_ids),
            "unique_extra_scryfall_ids": len(extra_unique_ids),
        },
        "extra_breakdown": {
            "languages": _top(extra_languages),
            "variants": _top(extra_variants),
            "sets": _top(extra_sets),
            "source_id_absent_languages": _top(extra_id_absent_languages),
            "source_id_present_finish_absent_languages": _top(extra_id_present_languages),
        },
        "samples": {
            "source_id_absent": id_absent_examples,
            "source_id_present_finish_absent": id_present_examples,
            "key_field_mismatch": malformed_examples,
        },
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Classify production MTG exact Prints outside current default_cards snapshot")
    parser.add_argument("--snapshot-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    run(snapshot_dir=args.snapshot_dir, output=args.output)


if __name__ == "__main__":
    main()
