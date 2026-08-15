from __future__ import annotations

import argparse
import gzip
import io
import json
import os
from collections import Counter
from pathlib import Path

import psycopg2
import requests

from app.ingest.connectors.scryfall_mtg_v2 import ScryfallMtgV2Connector
from app.mtg_identity_v2 import clean, finish_values
from app.scripts.build_mtg_v2_snapshot import _is_paper

LANGUAGES = ("es", "ja")


def db_url() -> str:
    value = os.getenv("DATABASE_URL_UNPOOLED") or os.getenv("DATABASE_URL")
    if not value:
        raise RuntimeError("DATABASE_URL is required")
    if value.startswith("postgresql+psycopg2://"):
        return "postgresql://" + value[len("postgresql+psycopg2://"):]
    if value.startswith("postgres://"):
        return "postgresql://" + value[len("postgres://"):]
    return value


def all_cards_metadata(connector: ScryfallMtgV2Connector) -> dict:
    payload = connector._request_json(f"{connector.base_url}/bulk-data")
    for item in payload.get("data") or []:
        if not isinstance(item, dict):
            continue
        kind = clean(item.get("type")).lower().replace("-", "_")
        name = clean(item.get("name")).lower().replace("_", " ").replace("-", " ")
        if kind == "all_cards" or name == "all cards":
            resolved = connector._resolve_bulk_detail(item)
            if resolved:
                return resolved
    raise RuntimeError("Scryfall all_cards bulk endpoint unavailable")


def iter_cards(connector: ScryfallMtgV2Connector, url: str):
    headers = {
        "User-Agent": connector._SCRYFALL_HEADERS["User-Agent"],
        "Accept": "application/gzip,application/jsonl,application/x-ndjson,*/*;q=0.8",
    }
    with requests.get(url, headers=headers, stream=True, timeout=300) as response:
        response.raise_for_status()
        response.raw.decode_content = False
        gzipped = url.lower().endswith(".gz") or "gzip" in clean(response.headers.get("Content-Type")).lower()
        if gzipped:
            stream = io.TextIOWrapper(gzip.GzipFile(fileobj=response.raw, mode="rb"), encoding="utf-8")
        else:
            stream = response.iter_lines(decode_unicode=True)
        try:
            for raw in stream:
                line = str(raw or "").strip()
                if line:
                    row = json.loads(line)
                    if isinstance(row, dict):
                        yield row
        finally:
            if gzipped:
                stream.close()


def load_db():
    conn = psycopg2.connect(db_url(), connect_timeout=20, application_name="dontripit_mtg_multilingual_delta_readonly")
    conn.set_session(readonly=True, autocommit=False)
    with conn.cursor() as cur:
        cur.execute("SHOW transaction_read_only")
        assert str(cur.fetchone()[0]).lower() == "on"
        cur.execute("SELECT id FROM games WHERE slug='mtg'")
        game_id = int(cur.fetchone()[0])
        cur.execute("""
            SELECT p.id, lower(coalesce(p.scryfall_id,'')), p.variant,
                   lower(s.code), p.collector_number, lower(coalesce(p.language,'')), p.is_foil
            FROM prints p
            JOIN cards c ON c.id=p.card_id
            JOIN sets s ON s.id=p.set_id
            WHERE c.game_id=%s
        """, (game_id,))
        exact = {}
        natural = {}
        by_language = Counter()
        for pid, sid, variant, set_code, collector, language, is_foil in cur.fetchall():
            by_language[language] += 1
            if sid:
                exact[(sid, str(variant))] = int(pid)
            natural[(str(set_code), str(collector), str(language), bool(is_foil), str(variant))] = (int(pid), str(sid))
        cur.execute("SELECT lower(oracle_id) FROM cards WHERE game_id=%s AND oracle_id IS NOT NULL", (game_id,))
        oracles = {str(row[0]) for row in cur.fetchall()}
        cur.execute("SELECT lower(code) FROM sets WHERE game_id=%s", (game_id,))
        sets = {str(row[0]) for row in cur.fetchall()}
    conn.rollback()
    conn.close()
    return exact, natural, oracles, sets, dict(by_language)


def run(output: Path) -> dict:
    exact_db, natural_db, oracle_db, set_db, db_languages = load_db()
    connector = ScryfallMtgV2Connector()
    meta = all_cards_metadata(connector)
    url = connector._bulk_download_url(meta)
    if not url:
        raise RuntimeError("all_cards has no download URL")

    counts = {lang: Counter() for lang in LANGUAGES}
    samples = {lang: {"natural_conflicts": [], "missing_oracle": [], "missing_set": []} for lang in LANGUAGES}
    seen_remote = {lang: set() for lang in LANGUAGES}

    for card in iter_cards(connector, url):
        if not _is_paper(card):
            continue
        lang = clean(card.get("lang")).lower()
        if lang not in LANGUAGES:
            continue
        sid = clean(card.get("id")).lower()
        oracle = clean(card.get("oracle_id")).lower()
        set_code = clean(card.get("set")).lower()
        collector = clean(card.get("collector_number"))
        if oracle not in oracle_db:
            counts[lang]["source_objects_missing_canonical_oracle"] += 1
            if len(samples[lang]["missing_oracle"]) < 10:
                samples[lang]["missing_oracle"].append(oracle)
        if set_code not in set_db:
            counts[lang]["source_objects_missing_set"] += 1
            if len(samples[lang]["missing_set"]) < 10:
                samples[lang]["missing_set"].append(set_code)
        for finish in finish_values(card):
            pair = (sid, finish)
            if pair in seen_remote[lang]:
                counts[lang]["remote_exact_duplicates"] += 1
                continue
            seen_remote[lang].add(pair)
            counts[lang]["remote_exact_prints"] += 1
            is_foil = finish != "nonfoil"
            natural = (set_code, collector, lang, is_foil, finish)
            if pair in exact_db:
                counts[lang]["already_present_exact"] += 1
                continue
            existing = natural_db.get(natural)
            if existing:
                pid, existing_sid = existing
                if not existing_sid or existing_sid == sid:
                    counts[lang]["already_present_natural_alias"] += 1
                else:
                    counts[lang]["natural_conflicts"] += 1
                    if len(samples[lang]["natural_conflicts"]) < 20:
                        samples[lang]["natural_conflicts"].append({
                            "natural": list(natural), "remote_scryfall_id": sid,
                            "existing_print_id": pid, "existing_scryfall_id": existing_sid,
                        })
                continue
            counts[lang]["new_prints_to_insert"] += 1

    report = {
        "status": "pass" if all(counts[l]["natural_conflicts"] == 0 and counts[l]["remote_exact_duplicates"] == 0 for l in LANGUAGES) else "blocked",
        "mode": "strict-read-only-additive-delta",
        "database_writes": 0,
        "source": {"bulk_type": meta.get("type"), "bulk_updated_at": meta.get("updated_at")},
        "database_language_counts": db_languages,
        "languages": {lang: {"counts": dict(counts[lang]), "samples": samples[lang]} for lang in LANGUAGES},
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("/tmp/mtg-multilingual-delta.json"))
    args = parser.parse_args()
    run(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
