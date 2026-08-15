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
from app.mtg_identity_v2 import card_identity_key, clean, finish_values, physical_print_key
from app.scripts.build_mtg_v2_snapshot import _image_rows, _is_paper

LANGUAGES = ("es", "ja")


def _db_url() -> str:
    value = os.getenv("DATABASE_URL_UNPOOLED") or os.getenv("DATABASE_URL")
    if not value:
        raise RuntimeError("DATABASE_URL_UNPOOLED or DATABASE_URL is required")
    if value.startswith("postgresql+psycopg2://"):
        return "postgresql://" + value[len("postgresql+psycopg2://"):]
    if value.startswith("postgres://"):
        return "postgresql://" + value[len("postgres://"):]
    return value


def _all_cards_metadata(connector: ScryfallMtgV2Connector) -> dict:
    payload = connector._request_json(f"{connector.base_url}/bulk-data")
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list):
        raise RuntimeError("Scryfall bulk-data listing has no data array")
    for item in data:
        if not isinstance(item, dict):
            continue
        kind = clean(item.get("type")).lower().replace("-", "_")
        name = clean(item.get("name")).lower().replace("-", " ").replace("_", " ")
        if kind == "all_cards" or name == "all cards":
            resolved = connector._resolve_bulk_detail(item)
            if resolved is not None:
                return resolved
    raise RuntimeError("Scryfall all_cards bulk endpoint unavailable")


def _iter_all_cards(connector: ScryfallMtgV2Connector, url: str):
    headers = {
        "User-Agent": connector._SCRYFALL_HEADERS["User-Agent"],
        "Accept": "application/gzip,application/jsonl,application/x-ndjson,*/*;q=0.8",
    }
    with requests.get(url, headers=headers, stream=True, timeout=300) as response:
        response.raise_for_status()
        response.raw.decode_content = False
        gzipped = url.lower().endswith(".gz") or "gzip" in clean(response.headers.get("Content-Type")).lower()
        if gzipped:
            binary = gzip.GzipFile(fileobj=response.raw, mode="rb")
            stream = io.TextIOWrapper(binary, encoding="utf-8")
        else:
            stream = response.iter_lines(decode_unicode=True)
        try:
            for raw in stream:
                line = str(raw or "").strip()
                if not line:
                    continue
                row = json.loads(line)
                if isinstance(row, dict):
                    yield row
        finally:
            if gzipped:
                stream.close()


def _remote_scan() -> tuple[dict, dict[str, dict]]:
    connector = ScryfallMtgV2Connector()
    metadata = _all_cards_metadata(connector)
    url = connector._bulk_download_url(metadata)
    if not url:
        raise RuntimeError("Scryfall all_cards metadata has no download URL")

    stats = {lang: Counter() for lang in LANGUAGES}
    state = {
        lang: {
            "oracle_ids": set(), "card_keys": set(), "set_codes": set(),
            "scryfall_ids": set(), "print_keys": set(), "natural_keys": set(),
            "natural_collisions": [], "print_key_collisions": [],
        }
        for lang in LANGUAGES
    }
    total_seen = 0
    paper_seen = 0

    for card in _iter_all_cards(connector, url):
        total_seen += 1
        if not _is_paper(card):
            continue
        paper_seen += 1
        lang = clean(card.get("lang")).lower()
        if lang not in LANGUAGES:
            continue

        s = stats[lang]
        st = state[lang]
        s["source_objects"] += 1
        sid = clean(card.get("id")).lower()
        oracle_id = clean(card.get("oracle_id")).lower()
        card_key = card_identity_key(card)
        set_code = clean(card.get("set")).lower()
        if sid:
            if sid in st["scryfall_ids"]:
                s["duplicate_scryfall_ids"] += 1
            st["scryfall_ids"].add(sid)
        else:
            s["objects_without_scryfall_id"] += 1
        if oracle_id:
            st["oracle_ids"].add(oracle_id)
        else:
            s["objects_without_oracle_id"] += 1
        if card_key:
            st["card_keys"].add(card_key)
        if set_code:
            st["set_codes"].add(set_code)

        if _image_rows(card):
            s["objects_with_image"] += 1
        else:
            s["objects_without_image"] += 1
        for field in ("printed_name", "printed_type_line", "printed_text"):
            if clean(card.get(field)):
                s[f"objects_with_{field}"] += 1
        if isinstance(card.get("prices"), dict) and any(card.get("prices", {}).values()):
            s["objects_with_scryfall_price_payload"] += 1

        finishes = finish_values(card)
        s["exact_prints"] += len(finishes)
        for finish in finishes:
            s[f"finish_{finish}"] += 1
            pkey = physical_print_key(card, finish)
            if pkey in st["print_keys"]:
                s["print_key_collisions"] += 1
                if len(st["print_key_collisions"]) < 20:
                    st["print_key_collisions"].append(pkey)
            st["print_keys"].add(pkey)
            natural = (set_code, clean(card.get("collector_number")), lang, finish != "nonfoil", finish)
            if natural in st["natural_keys"]:
                s["natural_key_collisions"] += 1
                if len(st["natural_collisions"]) < 20:
                    st["natural_collisions"].append(list(natural))
            st["natural_keys"].add(natural)

    source = {
        "bulk_type": clean(metadata.get("type")) or "all_cards",
        "bulk_updated_at": metadata.get("updated_at"),
        "bulk_objects_seen": total_seen,
        "paper_objects_seen": paper_seen,
    }
    return source, {lang: {"stats": dict(stats[lang]), **state[lang]} for lang in LANGUAGES}


def _db_scan(remote: dict[str, dict]) -> dict:
    conn = psycopg2.connect(_db_url(), connect_timeout=20, application_name="dontripit_mtg_multilingual_audit_readonly")
    conn.set_session(readonly=True, autocommit=False)
    try:
        with conn.cursor() as cur:
            cur.execute("SHOW transaction_read_only")
            if str(cur.fetchone()[0]).lower() != "on":
                raise RuntimeError("Read-only database guard failed")
            cur.execute("SELECT version_num FROM alembic_version")
            revision = str(cur.fetchone()[0])
            cur.execute("SELECT id, slug, name FROM games WHERE slug IN ('mtg','magic-the-gathering','magic') ORDER BY id LIMIT 1")
            row = cur.fetchone()
            if not row:
                raise RuntimeError("MTG game row not found")
            game_id, slug, name = int(row[0]), str(row[1]), str(row[2])

            def count(sql: str, params=()) -> int:
                cur.execute(sql, params)
                return int(cur.fetchone()[0])

            counts = {
                "sets": count("SELECT count(*) FROM sets WHERE game_id=%s", (game_id,)),
                "cards": count("SELECT count(*) FROM cards WHERE game_id=%s", (game_id,)),
                "prints": count("SELECT count(*) FROM prints p JOIN cards c ON c.id=p.card_id WHERE c.game_id=%s", (game_id,)),
            }
            cur.execute("SELECT lower(coalesce(p.language,'')), count(*) FROM prints p JOIN cards c ON c.id=p.card_id WHERE c.game_id=%s GROUP BY 1 ORDER BY 1", (game_id,))
            language_counts = {str(k): int(v) for k, v in cur.fetchall()}

            cur.execute("SELECT lower(oracle_id) FROM cards WHERE game_id=%s AND oracle_id IS NOT NULL", (game_id,))
            db_oracles = {str(r[0]) for r in cur.fetchall()}
            cur.execute("SELECT card_key FROM cards WHERE game_id=%s AND card_key IS NOT NULL", (game_id,))
            db_card_keys = {str(r[0]) for r in cur.fetchall()}
            cur.execute("SELECT lower(code) FROM sets WHERE game_id=%s", (game_id,))
            db_sets = {str(r[0]) for r in cur.fetchall()}
            cur.execute("SELECT lower(p.scryfall_id), p.variant FROM prints p JOIN cards c ON c.id=p.card_id WHERE c.game_id=%s AND p.scryfall_id IS NOT NULL", (game_id,))
            db_exact = {(str(sid), str(variant)) for sid, variant in cur.fetchall()}

            mapping = {}
            for lang in LANGUAGES:
                st = remote[lang]
                remote_oracles = st["oracle_ids"]
                remote_card_keys = st["card_keys"]
                remote_sets = st["set_codes"]
                remote_exact_pairs = {
                    (pkey.split(":")[1], pkey.rsplit(":", 1)[-1])
                    for pkey in st["print_keys"]
                    if pkey.startswith("scryfall:") and pkey.count(":") >= 2
                }
                mapping[lang] = {
                    "oracle_ids": len(remote_oracles),
                    "oracle_ids_already_canonical": len(remote_oracles & db_oracles),
                    "oracle_ids_missing_canonical": len(remote_oracles - db_oracles),
                    "card_keys_already_canonical": len(remote_card_keys & db_card_keys),
                    "set_codes": len(remote_sets),
                    "set_codes_already_present": len(remote_sets & db_sets),
                    "set_codes_missing": len(remote_sets - db_sets),
                    "exact_prints_already_present": len(remote_exact_pairs & db_exact),
                    "exact_prints_new": len(remote_exact_pairs - db_exact),
                }
        conn.rollback()
    finally:
        conn.close()
    return {
        "alembic_version": revision,
        "game": {"id": game_id, "slug": slug, "name": name},
        "counts": counts,
        "language_counts": language_counts,
        "mapping": mapping,
    }


def _serializable(remote: dict[str, dict]) -> dict:
    out = {}
    for lang, st in remote.items():
        out[lang] = {
            "stats": st["stats"],
            "unique_oracle_ids": len(st["oracle_ids"]),
            "unique_card_keys": len(st["card_keys"]),
            "unique_set_codes": len(st["set_codes"]),
            "unique_scryfall_ids": len(st["scryfall_ids"]),
            "unique_print_keys": len(st["print_keys"]),
            "natural_key_collision_samples": st["natural_collisions"],
            "print_key_collision_samples": st["print_key_collisions"],
        }
    return out


def run(output: Path) -> dict:
    source, remote = _remote_scan()
    database = _db_scan(remote)
    serialized = _serializable(remote)
    blockers = {}
    for lang in LANGUAGES:
        stats = serialized[lang]["stats"]
        blockers[lang] = {
            "duplicate_scryfall_ids": int(stats.get("duplicate_scryfall_ids", 0)),
            "print_key_collisions": int(stats.get("print_key_collisions", 0)),
            "natural_key_collisions": int(stats.get("natural_key_collisions", 0)),
        }
    safe_identity = all(not any(values.values()) for values in blockers.values())
    report = {
        "status": "pass" if safe_identity else "blocked",
        "mode": "strict-read-only-audit",
        "database_writes": 0,
        "source": source,
        "remote": serialized,
        "database": database,
        "identity_blockers": blockers,
        "safe_to_design_ephemeral_backfill": safe_identity,
        "price_policy": {
            "scryfall_price_payload_observed": True,
            "certified_language_exact": False,
            "materialize_during_multilingual_backfill": False,
            "reason": "Identity/localization rollout must not present unproven generic market data as language-exact pricing.",
        },
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit current physical MTG ES/JA coverage from Scryfall all_cards against production")
    parser.add_argument("--output", type=Path, default=Path("/tmp/mtg-multilingual-audit.json"))
    args = parser.parse_args()
    run(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
