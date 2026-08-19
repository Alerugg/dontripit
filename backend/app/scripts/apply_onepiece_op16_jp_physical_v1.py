from __future__ import annotations

import argparse
import json
import os
import re
from collections import Counter
from pathlib import Path
from urllib.parse import urlparse

import psycopg2
import requests
from psycopg2.extras import RealDictCursor

from app.ingest.connectors.onepiece_v2 import OnePieceV2Connector

GAME = "onepiece"
SOURCE = "onepiece_official_jp"
JP_BASE = "https://www.onepiece-cardgame.com/cardlist/"
TARGET_SET = "OP-16"
EXPECTED_PRINTS = 149
EXPECTED_COLLECTORS = 119
EXPECTED_IMAGES = 149
CONFIRM = "APPLY_ONEPIECE_OP16_JP_PHYSICAL_V1"


def _norm_label(value: str | None) -> str:
    return re.sub(r"[^A-Z0-9]+", "", str(value or "").upper())


def _physical_key(collector: str | None, variant: str | None) -> tuple[str, str]:
    return (
        str(collector or "").strip().upper(),
        str(variant or "default").strip().lower() or "default",
    )


def _connect(*, readonly: bool):
    url = os.getenv("DATABASE_URL_UNPOOLED") or os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL_UNPOOLED or DATABASE_URL is required")
    conn = psycopg2.connect(
        url,
        connect_timeout=30,
        application_name="dontripit_onepiece_op16_jp_physical_v1",
    )
    conn.set_session(readonly=readonly, autocommit=False)
    return conn


def _fetch_official_surface() -> tuple[list[dict], dict]:
    timeout = float(os.getenv("ONEPIECE_HTTP_TIMEOUT", "30"))
    headers = {"User-Agent": "TCGCatalogV2/1.0 (+https://github.com/Alerugg/dontripit)"}
    connector = OnePieceV2Connector()

    index = requests.get(JP_BASE, timeout=timeout, headers=headers)
    index.raise_for_status()
    options = connector._parse_official_series_options(index.text)
    candidates = [(sid, label) for sid, label in options if "OP16" in _norm_label(label)]
    if not candidates:
        raise RuntimeError({"official_jp_op16_series_not_found": len(options)})

    raw_entries: list[dict] = []
    selected = []
    for series_id, label in candidates:
        response = requests.get(f"{JP_BASE}?series={series_id}", timeout=timeout, headers=headers)
        response.raise_for_status()
        parsed = connector._parse_official_cards_page(response.text, base_url=JP_BASE)
        target = [row for row in parsed if str(row.get("set_code") or "").upper() == TARGET_SET]
        if target:
            selected.append({"series_id": str(series_id), "label": label, "entries": len(target)})
            raw_entries.extend(target)

    by_key: dict[tuple[str, str], dict] = {}
    duplicate_drift = []
    for row in raw_entries:
        key = _physical_key(row.get("collector_number"), row.get("variant"))
        normalized = {
            "collector_number": key[0],
            "variant": key[1],
            "external_id": str(row.get("print_id") or "").strip(),
            "rarity": str(row.get("rarity") or "").strip(),
            "image_url": str(row.get("image_url") or "").strip(),
            "jp_name": str(row.get("name") or "").strip(),
        }
        previous = by_key.get(key)
        if previous is None:
            by_key[key] = normalized
        elif previous != normalized:
            duplicate_drift.append({"key": key, "first": previous, "other": normalized})

    surface = [by_key[key] for key in sorted(by_key)]
    collectors = {row["collector_number"] for row in surface}
    external_ids = [row["external_id"] for row in surface]
    image_hosts = Counter(urlparse(row["image_url"]).netloc for row in surface if row["image_url"])

    if duplicate_drift:
        raise RuntimeError({"official_jp_duplicate_identity_drift": duplicate_drift[:20]})
    if len(surface) != EXPECTED_PRINTS:
        raise RuntimeError({"official_jp_physical_count_drift": len(surface)})
    if len(collectors) != EXPECTED_COLLECTORS:
        raise RuntimeError({"official_jp_collector_count_drift": len(collectors)})
    if len(set(external_ids)) != EXPECTED_PRINTS or any(not value for value in external_ids):
        raise RuntimeError("official JP source external IDs are not 149 unique non-empty values")
    if sum(1 for row in surface if row["image_url"]) != EXPECTED_IMAGES:
        raise RuntimeError("official JP OP16 image coverage is no longer 149/149")
    if set(image_hosts) != {"www.onepiece-cardgame.com"}:
        raise RuntimeError({"unexpected_official_image_hosts": dict(image_hosts)})
    if any(not row["rarity"] for row in surface):
        raise RuntimeError("official JP OP16 contains an empty rarity")

    return surface, {
        "series_candidates": len(candidates),
        "selected_series": selected,
        "image_hosts": dict(image_hosts),
    }


def _derive_ja_print_key(en_print_key: str | None) -> str:
    value = str(en_print_key or "").strip()
    parts = value.split(":")
    if len(parts) < 5 or parts[0] != "onepiece" or parts[-2].lower() != "en":
        raise RuntimeError({"unexpected_EN_print_key_shape": value})
    parts[-2] = "ja"
    return ":".join(parts)


def _load_state(cur, official: list[dict]) -> dict:
    cur.execute("SELECT id FROM games WHERE slug=%s LIMIT 1", (GAME,))
    game = cur.fetchone()
    if not game:
        raise RuntimeError("One Piece game row missing")
    game_id = int(game["id"])

    cur.execute("SELECT count(*) n FROM cards WHERE game_id=%s", (game_id,))
    cards_before = int(cur.fetchone()["n"])
    cur.execute("SELECT count(*) n FROM sets WHERE game_id=%s", (game_id,))
    sets_before = int(cur.fetchone()["n"])

    cur.execute(
        """SELECT p.id print_id,p.set_id,p.card_id,p.collector_number,p.variant,p.rarity,
                  p.language,p.is_foil,p.print_key,c.name card_name,s.code set_code
           FROM prints p
           JOIN cards c ON c.id=p.card_id
           JOIN sets s ON s.id=p.set_id
           WHERE c.game_id=%s
             AND upper(replace(coalesce(s.code,''),'_','-')) IN ('OP16','OP-16')
           ORDER BY p.language,p.collector_number,p.variant,p.id""",
        (game_id,),
    )
    all_op16 = [dict(row) for row in cur.fetchall()]
    en_rows = [row for row in all_op16 if str(row.get("language") or "").lower() == "en"]
    ja_rows = [row for row in all_op16 if str(row.get("language") or "").lower() == "ja"]
    other_languages = [row for row in all_op16 if str(row.get("language") or "").lower() not in {"en", "ja"}]

    if len(en_rows) != EXPECTED_PRINTS:
        raise RuntimeError({"EN_OP16_baseline_drift": len(en_rows)})
    if other_languages:
        raise RuntimeError({"unexpected_OP16_languages": Counter(str(row.get("language")) for row in other_languages)})
    if len({int(row["set_id"]) for row in en_rows}) != 1:
        raise RuntimeError("EN OP16 does not resolve to exactly one canonical Set")
    if len({_physical_key(row["collector_number"], row["variant"]) for row in en_rows}) != EXPECTED_PRINTS:
        raise RuntimeError("EN OP16 exact physical key surface is not 149 unique")
    if len({str(row["collector_number"]).upper() for row in en_rows}) != EXPECTED_COLLECTORS:
        raise RuntimeError("EN OP16 logical collector baseline is not 119")
    if len(ja_rows) not in {0, EXPECTED_PRINTS}:
        raise RuntimeError({"partial_JA_OP16_surface_forbidden": len(ja_rows)})

    en_by_key = {_physical_key(row["collector_number"], row["variant"]): row for row in en_rows}
    official_by_key = {_physical_key(row["collector_number"], row["variant"]): row for row in official}
    if set(en_by_key) != set(official_by_key):
        raise RuntimeError(
            {
                "JP_EN_exact_physical_geometry_mismatch": {
                    "only_en": sorted(set(en_by_key) - set(official_by_key))[:30],
                    "only_jp": sorted(set(official_by_key) - set(en_by_key))[:30],
                }
            }
        )

    proposal = []
    rarity_drift = []
    for key in sorted(official_by_key):
        source = official_by_key[key]
        en = en_by_key[key]
        if str(en.get("rarity") or "").casefold() != str(source["rarity"]).casefold():
            rarity_drift.append({"key": key, "en": en.get("rarity"), "jp": source["rarity"]})
        proposal.append(
            {
                **source,
                "set_id": int(en["set_id"]),
                "card_id": int(en["card_id"]),
                "is_foil": bool(en["is_foil"]),
                "en_print_id": int(en["print_id"]),
                "en_print_key": str(en["print_key"] or ""),
                "ja_print_key": _derive_ja_print_key(en["print_key"]),
            }
        )
    if rarity_drift:
        raise RuntimeError({"JP_EN_rarity_drift": rarity_drift[:30]})
    if len({row["ja_print_key"] for row in proposal}) != EXPECTED_PRINTS:
        raise RuntimeError("derived JA print keys are not 149 unique")

    # Global source external IDs must never already claim another print.
    external_ids = [row["external_id"] for row in proposal]
    cur.execute(
        """SELECT pi.print_id,pi.external_id,p.language,p.collector_number,p.variant
           FROM print_identifiers pi
           JOIN prints p ON p.id=pi.print_id
           WHERE pi.source=%s AND pi.external_id=ANY(%s)""",
        (SOURCE, external_ids),
    )
    identifiers = [dict(row) for row in cur.fetchall()]
    identifiers_by_external: dict[str, list[dict]] = {}
    for row in identifiers:
        identifiers_by_external.setdefault(str(row["external_id"]), []).append(row)

    ja_by_key = {_physical_key(row["collector_number"], row["variant"]): row for row in ja_rows}
    existing_exact = []
    new_rows = []
    errors = []
    for row in proposal:
        key = _physical_key(row["collector_number"], row["variant"])
        current = ja_by_key.get(key)
        if current is None:
            if identifiers_by_external.get(row["external_id"]):
                errors.append({"key": key, "external_id_claimed_without_expected_JA_print": identifiers_by_external[row["external_id"]]})
            new_rows.append(row)
            continue

        checks = {
            "card_id": int(current["card_id"]) == row["card_id"],
            "set_id": int(current["set_id"]) == row["set_id"],
            "rarity": str(current.get("rarity") or "").casefold() == row["rarity"].casefold(),
            "is_foil": bool(current["is_foil"]) == row["is_foil"],
            "print_key": str(current.get("print_key") or "") == row["ja_print_key"],
        }
        failed = [name for name, ok in checks.items() if not ok]
        if failed:
            errors.append({"key": key, "print_id": int(current["print_id"]), "failed": failed})
            continue

        claims = identifiers_by_external.get(row["external_id"], [])
        if len(claims) != 1 or int(claims[0]["print_id"]) != int(current["print_id"]):
            errors.append({"key": key, "print_id": int(current["print_id"]), "identifier_claims": claims})
            continue

        cur.execute(
            """SELECT id,url,is_primary,source
               FROM print_images
               WHERE print_id=%s AND source=%s""",
            (int(current["print_id"]), SOURCE),
        )
        source_images = [dict(image) for image in cur.fetchall()]
        exact_images = [image for image in source_images if str(image["url"]) == row["image_url"] and bool(image["is_primary"])]
        if len(source_images) != 1 or len(exact_images) != 1:
            errors.append({"key": key, "print_id": int(current["print_id"]), "source_images": source_images})
            continue
        existing_exact.append({**row, "print_id": int(current["print_id"])})

    if errors:
        raise RuntimeError(json.dumps({"JA_OP16_existing_surface_errors": errors[:30]}, ensure_ascii=False, default=str))
    if len(new_rows) + len(existing_exact) != EXPECTED_PRINTS:
        raise RuntimeError("JA OP16 proposal accounting drift")

    en_fingerprint = [
        (
            int(row["print_id"]), int(row["set_id"]), int(row["card_id"]), str(row["collector_number"]),
            str(row["variant"]), str(row.get("rarity") or ""), str(row.get("language") or ""),
            bool(row["is_foil"]), str(row.get("print_key") or ""),
        )
        for row in en_rows
    ]

    return {
        "game_id": game_id,
        "cards_before": cards_before,
        "sets_before": sets_before,
        "en_fingerprint": en_fingerprint,
        "new_rows": new_rows,
        "existing_exact": existing_exact,
        "proposal": proposal,
    }


def _assert_post_state(cur, built: dict) -> dict:
    game_id = built["game_id"]
    cur.execute("SELECT count(*) n FROM cards WHERE game_id=%s", (game_id,))
    cards_after = int(cur.fetchone()["n"])
    cur.execute("SELECT count(*) n FROM sets WHERE game_id=%s", (game_id,))
    sets_after = int(cur.fetchone()["n"])
    if cards_after != built["cards_before"] or sets_after != built["sets_before"]:
        raise RuntimeError({"logical_catalog_changed": {"cards": [built["cards_before"], cards_after], "sets": [built["sets_before"], sets_after]}})

    cur.execute(
        """SELECT p.id print_id,p.set_id,p.card_id,p.collector_number,p.variant,p.rarity,
                  p.language,p.is_foil,p.print_key
           FROM prints p JOIN cards c ON c.id=p.card_id JOIN sets s ON s.id=p.set_id
           WHERE c.game_id=%s AND upper(replace(coalesce(s.code,''),'_','-')) IN ('OP16','OP-16')
           ORDER BY p.language,p.collector_number,p.variant,p.id""",
        (game_id,),
    )
    rows = [dict(row) for row in cur.fetchall()]
    en_rows = [row for row in rows if str(row.get("language") or "").lower() == "en"]
    ja_rows = [row for row in rows if str(row.get("language") or "").lower() == "ja"]
    en_fingerprint_after = [
        (
            int(row["print_id"]), int(row["set_id"]), int(row["card_id"]), str(row["collector_number"]),
            str(row["variant"]), str(row.get("rarity") or ""), str(row.get("language") or ""),
            bool(row["is_foil"]), str(row.get("print_key") or ""),
        )
        for row in en_rows
    ]
    if en_fingerprint_after != built["en_fingerprint"]:
        raise RuntimeError("EN OP16 fingerprint changed during JP backfill")
    if len(ja_rows) != EXPECTED_PRINTS:
        raise RuntimeError({"JA_OP16_post_count": len(ja_rows)})

    ja_ids = [int(row["print_id"]) for row in ja_rows]
    cur.execute(
        """SELECT count(*) n,count(DISTINCT print_id) prints,count(DISTINCT external_id) external_ids
           FROM print_identifiers WHERE source=%s AND print_id=ANY(%s)""",
        (SOURCE, ja_ids),
    )
    identifier_counts = dict(cur.fetchone())
    cur.execute(
        """SELECT count(*) n,count(DISTINCT print_id) prints
           FROM print_images WHERE source=%s AND is_primary=true AND print_id=ANY(%s)""",
        (SOURCE, ja_ids),
    )
    image_counts = dict(cur.fetchone())
    if tuple(int(identifier_counts[key]) for key in ("n", "prints", "external_ids")) != (149, 149, 149):
        raise RuntimeError({"JP_identifier_postcondition_failed": identifier_counts})
    if tuple(int(image_counts[key]) for key in ("n", "prints")) != (149, 149):
        raise RuntimeError({"JP_image_postcondition_failed": image_counts})
    return {
        "cards_after": cards_after,
        "sets_after": sets_after,
        "en_prints_after": len(en_rows),
        "ja_prints_after": len(ja_rows),
        "jp_identifiers_after": int(identifier_counts["n"]),
        "jp_primary_images_after": int(image_counts["n"]),
    }


def run(*, apply: bool, confirm: str = "") -> dict:
    if apply and confirm != CONFIRM:
        raise RuntimeError(f"--apply requires --confirm {CONFIRM}")
    official, source_meta = _fetch_official_surface()
    conn = _connect(readonly=not apply)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            built = _load_state(cur, official)
            report = {
                "mode": "apply" if apply else "dry_run",
                "status": "pass",
                "production_writes": 0,
                "source": SOURCE,
                "target_set": TARGET_SET,
                "official_physical": len(official),
                "official_collectors": len({row["collector_number"] for row in official}),
                "official_images": sum(1 for row in official if row["image_url"]),
                "source_meta": source_meta,
                "cards_before": built["cards_before"],
                "sets_before": built["sets_before"],
                "en_prints_before": EXPECTED_PRINTS,
                "existing_exact_ja": len(built["existing_exact"]),
                "new_ja_prints_ready": len(built["new_rows"]),
                "op16_119": [row for row in built["proposal"] if row["collector_number"] == "OP16-119"],
            }
            if not apply:
                conn.rollback()
                return report

            inserted_prints = inserted_identifiers = inserted_images = 0
            for row in built["new_rows"]:
                cur.execute(
                    """INSERT INTO prints(
                           set_id,card_id,collector_number,language,rarity,is_foil,variant,print_key
                       ) VALUES(%s,%s,%s,'ja',%s,%s,%s,%s)
                       RETURNING id""",
                    (
                        row["set_id"], row["card_id"], row["collector_number"], row["rarity"],
                        row["is_foil"], row["variant"], row["ja_print_key"],
                    ),
                )
                print_id = int(cur.fetchone()["id"])
                inserted_prints += 1

                cur.execute(
                    """INSERT INTO print_identifiers(print_id,source,external_id)
                       VALUES(%s,%s,%s)""",
                    (print_id, SOURCE, row["external_id"]),
                )
                inserted_identifiers += cur.rowcount
                cur.execute(
                    """INSERT INTO print_images(print_id,url,is_primary,source)
                       VALUES(%s,%s,true,%s)""",
                    (print_id, row["image_url"], SOURCE),
                )
                inserted_images += cur.rowcount

            post = _assert_post_state(cur, built)
            if (inserted_prints, inserted_identifiers, inserted_images) != (
                len(built["new_rows"]), len(built["new_rows"]), len(built["new_rows"])
            ):
                raise RuntimeError(
                    {"insert_accounting_failed": [inserted_prints, inserted_identifiers, inserted_images]}
                )
            report.update(post)
            report["inserted_prints"] = inserted_prints
            report["inserted_identifiers"] = inserted_identifiers
            report["inserted_images"] = inserted_images
            report["production_writes"] = inserted_prints + inserted_identifiers + inserted_images
            conn.commit()
            return report
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply exact One Piece OP16 Japanese physical prints from Bandai official JP")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm", default="")
    parser.add_argument("--report", type=Path, default=Path("/tmp/onepiece-op16-jp-physical-v1.json"))
    args = parser.parse_args()
    payload = run(apply=args.apply, confirm=args.confirm)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n"
    args.report.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
