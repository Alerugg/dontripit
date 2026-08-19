from __future__ import annotations

import hashlib
import json
import os
import re
from collections import Counter, defaultdict
from pathlib import Path

import psycopg2
import requests
from psycopg2.extras import RealDictCursor

from app.ingest.connectors.onepiece_v2 import OnePieceV2Connector
from app.ingest.normalization import normalize_variant

JP_BASE = "https://www.onepiece-cardgame.com/cardlist/"
LANGUAGE_JA = "ja"
LANGUAGE_EN = "en"


def _norm_set(value: object) -> str:
    return re.sub(r"[^A-Z0-9]+", "", str(value or "").upper())


def _stable_digest(rows: list[dict]) -> str:
    material = "".join(
        "|".join(
            [
                row["set_token"],
                row["collector_number"],
                row["variant"],
                row["source_print_id"],
                str(row.get("rarity") or ""),
                row["image_url"],
            ]
        ) + "\n"
        for row in sorted(
            rows,
            key=lambda x: (
                x["set_token"],
                x["collector_number"],
                x["variant"],
                x["source_print_id"],
            ),
        )
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _load_official() -> dict:
    connector = OnePieceV2Connector()
    timeout = float(os.getenv("ONEPIECE_HTTP_TIMEOUT", "30"))
    headers = {"User-Agent": "TCGCatalogV2/1.0 (+https://github.com/Alerugg/dontripit)"}
    session = requests.Session()
    session.headers.update(headers)

    index = session.get(JP_BASE, timeout=timeout)
    index.raise_for_status()
    options = connector._parse_official_series_options(index.text)
    if not options:
        raise RuntimeError("official Japanese One Piece cardlist returned zero series options")

    by_key: dict[tuple[str, str, str], dict] = {}
    conflicts = []
    releases = []
    parsed_entries = 0
    for series_id, label in options:
        response = session.get(f"{JP_BASE}?series={series_id}", timeout=timeout)
        response.raise_for_status()
        entries = connector._parse_official_cards_page(response.text, base_url=JP_BASE)
        parsed_entries += len(entries)
        release_set_tokens = set()
        for row in entries:
            set_token = _norm_set(row.get("set_code"))
            collector = str(row.get("collector_number") or "").upper().strip()
            variant = normalize_variant(row.get("variant"))
            source_print_id = str(row.get("print_id") or "").upper().strip()
            if not set_token or not collector or not source_print_id:
                continue
            image_url = str(row.get("image_url") or "").strip()
            normalized = {
                "set_token": set_token,
                "set_code": str(row.get("set_code") or "").upper().strip(),
                "collector_number": collector,
                "variant": variant,
                "source_print_id": source_print_id,
                "rarity": str(row.get("rarity") or "").strip() or None,
                "image_url": image_url,
                "series_ids": [str(series_id)],
                "series_labels": [label],
            }
            release_set_tokens.add(set_token)
            key = (set_token, collector, variant)
            existing = by_key.get(key)
            if existing is None:
                by_key[key] = normalized
                continue
            comparable_keys = ("source_print_id", "rarity", "image_url")
            if any(existing.get(field) != normalized.get(field) for field in comparable_keys):
                conflicts.append(
                    {
                        "key": key,
                        "first": {field: existing.get(field) for field in comparable_keys},
                        "other": {field: normalized.get(field) for field in comparable_keys},
                        "first_series": existing.get("series_ids"),
                        "other_series": [str(series_id)],
                    }
                )
            if str(series_id) not in existing["series_ids"]:
                existing["series_ids"].append(str(series_id))
            if label not in existing["series_labels"]:
                existing["series_labels"].append(label)
        releases.append(
            {
                "series_id": str(series_id),
                "label": label,
                "parsed_entries": len(entries),
                "set_tokens": sorted(release_set_tokens),
            }
        )

    physical = list(by_key.values())
    by_set = defaultdict(list)
    for row in physical:
        by_set[row["set_token"]].append(row)

    return {
        "series_option_count": len(options),
        "parsed_entries": parsed_entries,
        "unique_physical": len(physical),
        "sets": dict(by_set),
        "conflicts": conflicts,
        "releases": releases,
        "digest": _stable_digest(physical),
    }


def main() -> int:
    official = _load_official()
    url = os.getenv("DATABASE_URL_UNPOOLED") or os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL_UNPOOLED or DATABASE_URL is required")
    conn = psycopg2.connect(
        url,
        connect_timeout=30,
        application_name="dontripit_onepiece_jp_full_surface_v1",
    )
    conn.set_session(readonly=True, autocommit=False)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT id FROM games WHERE slug='onepiece' LIMIT 1")
            game_id = int(cur.fetchone()["id"])
            cur.execute("SELECT count(*) n FROM cards WHERE game_id=%s", (game_id,))
            game_cards = int(cur.fetchone()["n"])
            cur.execute("SELECT id,code,name,region FROM sets WHERE game_id=%s ORDER BY code,id", (game_id,))
            set_rows = [dict(row) for row in cur.fetchall()]
            sets_by_token = defaultdict(list)
            for row in set_rows:
                sets_by_token[_norm_set(row["code"])].append(row)

            cur.execute(
                """SELECT p.id print_id,p.set_id,p.card_id,p.collector_number,p.language,p.variant,p.rarity,p.print_key,
                          s.code set_code,c.name card_name,
                          (SELECT pi.url FROM print_images pi WHERE pi.print_id=p.id
                           ORDER BY pi.is_primary DESC,pi.id ASC LIMIT 1) image_url,
                          (SELECT pi.source FROM print_images pi WHERE pi.print_id=p.id
                           ORDER BY pi.is_primary DESC,pi.id ASC LIMIT 1) image_source,
                          (SELECT ident.external_id FROM print_identifiers ident
                           WHERE ident.print_id=p.id AND ident.source='onepiece_official_jp'
                           LIMIT 1) jp_external_id
                   FROM prints p
                   JOIN sets s ON s.id=p.set_id
                   JOIN cards c ON c.id=p.card_id
                   WHERE c.game_id=%s
                   ORDER BY s.code,p.language,p.collector_number,p.variant,p.id""",
                (game_id,),
            )
            print_rows = [dict(row) for row in cur.fetchall()]
            conn.rollback()
    finally:
        conn.close()

    prints_by_set = defaultdict(list)
    for row in print_rows:
        prints_by_set[_norm_set(row["set_code"])].append(row)

    conflict_keys_by_set = defaultdict(list)
    for conflict in official["conflicts"]:
        conflict_keys_by_set[str(conflict["key"][0])].append(conflict)

    set_reports = []
    safe_materializable = []
    already_materialized = []
    blocked = []
    total_new_safe_prints = 0

    for set_token, official_rows in sorted(official["sets"].items()):
        official_keys = {(row["collector_number"], row["variant"]) for row in official_rows}
        official_collectors = {row["collector_number"] for row in official_rows}
        source_ids = [row["source_print_id"] for row in official_rows]
        missing_images = sum(1 for row in official_rows if not row["image_url"])
        source_id_unique = len(set(source_ids)) == len(source_ids)
        candidate_sets = sets_by_token.get(set_token, [])
        db_rows = prints_by_set.get(set_token, [])
        en_rows = [row for row in db_rows if str(row.get("language") or "").lower() == LANGUAGE_EN]
        ja_rows = [row for row in db_rows if str(row.get("language") or "").lower() == LANGUAGE_JA]

        card_ids_by_collector = defaultdict(set)
        for row in db_rows:
            collector = str(row.get("collector_number") or "").upper().strip()
            if collector:
                card_ids_by_collector[collector].add(int(row["card_id"]))
        card_collisions = {
            collector: sorted(ids)
            for collector, ids in card_ids_by_collector.items()
            if len(ids) > 1
        }
        neon_collectors = set(card_ids_by_collector)
        en_keys = {
            (str(row["collector_number"]).upper(), normalize_variant(row["variant"]))
            for row in en_rows
        }
        ja_keys = {
            (str(row["collector_number"]).upper(), normalize_variant(row["variant"]))
            for row in ja_rows
        }
        logical_exact = bool(candidate_sets) and official_collectors == neon_collectors
        set_conflicts = conflict_keys_by_set.get(set_token, [])

        ja_exact_source_matches = 0
        if ja_rows:
            ja_by_key = {
                (str(row["collector_number"]).upper(), normalize_variant(row["variant"])): row
                for row in ja_rows
            }
            official_by_key = {(row["collector_number"], row["variant"]): row for row in official_rows}
            for key, expected in official_by_key.items():
                db = ja_by_key.get(key)
                if db is None:
                    continue
                if (
                    str(db.get("rarity") or "") == str(expected.get("rarity") or "")
                    and str(db.get("image_url") or "") == str(expected.get("image_url") or "")
                    and str(db.get("image_source") or "") == "onepiece_official_jp"
                    and str(db.get("jp_external_id") or "") == str(expected.get("source_print_id") or "")
                ):
                    ja_exact_source_matches += 1

        reason = None
        status = "blocked"
        if len(candidate_sets) != 1:
            reason = "no_or_ambiguous_neon_set"
        elif not logical_exact:
            reason = "logical_collector_mismatch"
        elif card_collisions:
            reason = "collector_card_collision"
        elif missing_images:
            reason = "official_missing_images"
        elif not source_id_unique:
            reason = "official_source_id_collision"
        elif set_conflicts:
            reason = "official_duplicate_physical_conflict"
        elif not ja_rows:
            status = "safe_materializable"
        elif ja_keys == official_keys and ja_exact_source_matches == len(official_rows):
            status = "already_materialized_exact"
        else:
            reason = "partial_or_drifted_ja_surface"

        row_report = {
            "set_token": set_token,
            "official_set_codes": sorted({row["set_code"] for row in official_rows}),
            "official_physical": len(official_rows),
            "official_logical_collectors": len(official_collectors),
            "official_missing_images": missing_images,
            "official_source_ids_unique": source_id_unique,
            "official_conflicts": len(set_conflicts),
            "neon_set_matches": [
                {"id": int(row["id"]), "code": row["code"], "name": row["name"], "region": row["region"]}
                for row in candidate_sets
            ],
            "neon_logical_collectors": len(neon_collectors),
            "logical_overlap": len(official_collectors & neon_collectors),
            "collectors_only_jp": sorted(official_collectors - neon_collectors),
            "collectors_only_neon": sorted(neon_collectors - official_collectors),
            "collector_card_collisions": card_collisions,
            "en_physical": len(en_rows),
            "ja_physical": len(ja_rows),
            "ja_exact_source_matches": ja_exact_source_matches,
            "regional_variant_delta_only_jp": sorted(official_keys - en_keys),
            "regional_variant_delta_only_en": sorted(en_keys - official_keys),
            "status": status,
            "blocked_reason": reason,
        }
        set_reports.append(row_report)
        if status == "safe_materializable":
            safe_materializable.append(set_token)
            total_new_safe_prints += len(official_rows)
        elif status == "already_materialized_exact":
            already_materialized.append(set_token)
        else:
            blocked.append({"set_token": set_token, "reason": reason})

    official_tokens = set(official["sets"])
    neon_tokens = {token for token in sets_by_token if token}
    report = {
        "status": "pass",
        "production_writes": 0,
        "source": "onepiece_official_jp",
        "official_base_url": JP_BASE,
        "official_series_options": official["series_option_count"],
        "official_parsed_entries_across_releases": official["parsed_entries"],
        "official_unique_physical_identities": official["unique_physical"],
        "official_set_tokens": len(official_tokens),
        "official_surface_sha256": official["digest"],
        "official_duplicate_physical_conflicts": len(official["conflicts"]),
        "onepiece_cards": game_cards,
        "onepiece_sets": len(set_rows),
        "safe_materializable_sets": safe_materializable,
        "safe_materializable_set_count": len(safe_materializable),
        "safe_new_ja_prints": total_new_safe_prints,
        "already_materialized_exact_sets": already_materialized,
        "already_materialized_exact_set_count": len(already_materialized),
        "blocked_sets": blocked,
        "blocked_set_count": len(blocked),
        "official_set_tokens_without_neon_set": sorted(official_tokens - neon_tokens),
        "neon_set_tokens_without_official_jp_surface": sorted(neon_tokens - official_tokens),
        "sets": set_reports,
        "official_conflicts": official["conflicts"],
    }
    out = Path(os.getenv("ONEPIECE_JP_FULL_SURFACE_OUTPUT", "/tmp/onepiece-jp-full-surface-v1.json"))
    out.parent.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n"
    out.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
