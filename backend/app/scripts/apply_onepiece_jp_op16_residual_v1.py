from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path

import psycopg2
from psycopg2.extras import RealDictCursor

from app.ingest.normalization import normalize_collector_number, normalize_variant
from app.scripts.audit_onepiece_jp_full_surface_v1 import _load_official

GAME = "onepiece"
SET_TOKEN = "OP16"
LANGUAGE = "ja"
IMAGE_SOURCE = "onepiece_official_jp"
IDENTIFIER_SOURCE = "onepiece_official_jp"
FULL_SURFACE_SHA256 = "772684372981c8004acc0b17598f2853118b2ea0c375e5654631b2cfacdf2008"
EXPECTED_BEFORE = 149
EXPECTED_AFTER = 154
CONFIRM = "APPLY_ONEPIECE_JP_OP16_RESIDUAL_V1"
EXPECTED_RESIDUAL = {
    ("OP16-010", "p1"): (15731, "UC", "OP16-010_P1", "https://www.onepiece-cardgame.com/images/cardlist/card/OP16-010_p1.png"),
    ("OP16-024", "p1"): (15745, "UC", "OP16-024_P1", "https://www.onepiece-cardgame.com/images/cardlist/card/OP16-024_p1.png"),
    ("OP16-032", "p2"): (15753, "SR", "OP16-032_P2", "https://www.onepiece-cardgame.com/images/cardlist/card/OP16-032_p2.png"),
    ("OP16-092", "p1"): (15813, "UC", "OP16-092_P1", "https://www.onepiece-cardgame.com/images/cardlist/card/OP16-092_p1.png"),
    ("OP16-092", "p2"): (15813, "UC", "OP16-092_P2", "https://www.onepiece-cardgame.com/images/cardlist/card/OP16-092_p2.png"),
}


def _norm_set(value: object) -> str:
    return re.sub(r"[^A-Z0-9]+", "", str(value or "").upper())


def _connect(*, readonly: bool):
    url = os.getenv("DATABASE_URL_UNPOOLED") or os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL_UNPOOLED or DATABASE_URL is required")
    conn = psycopg2.connect(url, connect_timeout=30, application_name="dontripit_op16_jp_residual_writer_v1")
    conn.set_session(readonly=readonly, autocommit=False)
    return conn


def _source_rows() -> dict[tuple[str, str], dict]:
    official = _load_official()
    if official["digest"] != FULL_SURFACE_SHA256:
        raise RuntimeError({"official_surface_digest_drift": official["digest"]})
    rows = list(official["sets"].get(SET_TOKEN) or [])
    if len(rows) != EXPECTED_AFTER:
        raise RuntimeError({"official_OP16_full_count_drift": len(rows)})
    by_key = {(row["collector_number"], normalize_variant(row["variant"])): row for row in rows}
    for key, frozen in EXPECTED_RESIDUAL.items():
        row = by_key.get(key)
        if row is None:
            raise RuntimeError({"frozen_residual_missing_from_live_source": key})
        card_id, rarity, external_id, image_url = frozen
        if str(row.get("rarity") or "") != rarity or str(row.get("source_print_id") or "") != external_id or str(row.get("image_url") or "") != image_url:
            raise RuntimeError({"frozen_residual_source_drift": {"key": key, "live": row, "frozen": frozen}})
    return by_key


def _build(cur, source_by_key: dict[tuple[str, str], dict]) -> dict:
    cur.execute("SELECT id FROM games WHERE slug=%s LIMIT 1", (GAME,))
    game_id = int(cur.fetchone()["id"])
    cur.execute("SELECT count(*) n FROM cards WHERE game_id=%s", (game_id,))
    cards_before = int(cur.fetchone()["n"])
    cur.execute("SELECT count(*) n FROM sets WHERE game_id=%s", (game_id,))
    sets_before = int(cur.fetchone()["n"])
    cur.execute("SELECT id,code FROM sets WHERE game_id=%s", (game_id,))
    matches = [dict(row) for row in cur.fetchall() if _norm_set(row["code"]) == SET_TOKEN]
    if len(matches) != 1:
        raise RuntimeError({"OP16_set_not_unique": matches})
    set_id, set_code = int(matches[0]["id"]), str(matches[0]["code"])

    cur.execute(
        """SELECT p.id print_id,p.card_id,p.collector_number,p.variant,p.rarity,p.print_key,
                  (SELECT pi.url FROM print_images pi WHERE pi.print_id=p.id ORDER BY pi.is_primary DESC,pi.id ASC LIMIT 1) image_url,
                  (SELECT pi.source FROM print_images pi WHERE pi.print_id=p.id ORDER BY pi.is_primary DESC,pi.id ASC LIMIT 1) image_source,
                  (SELECT ident.external_id FROM print_identifiers ident WHERE ident.print_id=p.id AND ident.source=%s LIMIT 1) external_id
           FROM prints p WHERE p.set_id=%s AND lower(coalesce(p.language,''))=%s
           ORDER BY p.collector_number,p.variant,p.id""",
        (IDENTIFIER_SOURCE, set_id, LANGUAGE),
    )
    existing = [dict(row) for row in cur.fetchall()]
    if len(existing) not in (EXPECTED_BEFORE, EXPECTED_AFTER):
        raise RuntimeError({"partial_OP16_JA_surface_blocked": len(existing)})
    existing_by_key = {(str(row["collector_number"]).upper(), normalize_variant(row["variant"])): row for row in existing}
    source_keys = set(source_by_key)
    if not set(existing_by_key).issubset(source_keys):
        raise RuntimeError({"db_keys_not_in_live_source": sorted(set(existing_by_key)-source_keys)})

    # Every pre-existing row must remain exact against the live official full surface.
    mismatches = []
    for key, db in existing_by_key.items():
        source = source_by_key[key]
        if (
            str(db.get("rarity") or "") != str(source.get("rarity") or "")
            or str(db.get("image_url") or "") != str(source.get("image_url") or "")
            or str(db.get("image_source") or "") != IMAGE_SOURCE
            or str(db.get("external_id") or "") != str(source.get("source_print_id") or "")
        ):
            mismatches.append({"key": key, "db": db, "source": source})
    if mismatches:
        raise RuntimeError({"existing_OP16_JA_drift": mismatches})

    missing_keys = sorted(source_keys - set(existing_by_key))
    if len(existing) == EXPECTED_BEFORE and set(missing_keys) != set(EXPECTED_RESIDUAL):
        raise RuntimeError({"unexpected_residual_surface": missing_keys})
    if len(existing) == EXPECTED_AFTER and missing_keys:
        raise RuntimeError({"complete_surface_missing_keys": missing_keys})

    proposal = []
    for key in missing_keys:
        source = source_by_key[key]
        frozen = EXPECTED_RESIDUAL.get(key)
        if frozen is None:
            raise RuntimeError({"unfrozen_residual_key": key})
        card_id, rarity, external_id, image_url = frozen
        cur.execute("SELECT id FROM cards WHERE id=%s AND game_id=%s", (card_id, game_id))
        if cur.fetchone() is None:
            raise RuntimeError({"residual_card_missing": {"key": key, "card_id": card_id}})
        collector, variant = key
        print_key = f"onepiece:{set_code.lower()}:{normalize_collector_number(collector)}:{LANGUAGE}:{variant}"
        proposal.append({"collector": collector, "variant": variant, "card_id": card_id, "rarity": rarity, "external_id": external_id, "image_url": image_url, "print_key": print_key})

    if proposal:
        ext_ids = [row["external_id"] for row in proposal]
        cur.execute("SELECT print_id,external_id FROM print_identifiers WHERE source=%s AND external_id=ANY(%s)", (IDENTIFIER_SOURCE, ext_ids))
        claims = [dict(row) for row in cur.fetchall()]
        if claims:
            raise RuntimeError({"residual_identifier_already_claimed": claims})
    return {"game_id": game_id, "set_id": set_id, "cards_before": cards_before, "sets_before": sets_before, "existing": len(existing), "proposal": proposal}


def run(*, apply: bool, confirm: str = "") -> dict:
    if apply and confirm != CONFIRM:
        raise RuntimeError(f"--apply requires --confirm {CONFIRM}")
    source = _source_rows()
    conn = _connect(readonly=not apply)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            state = _build(cur, source)
            report = {"mode": "apply" if apply else "dry_run", "status": "pass", "production_writes": 0, "official_surface_sha256": FULL_SURFACE_SHA256, "existing_ja_before": state["existing"], "residual_ready": len(state["proposal"]), "proposal": state["proposal"], "created_print_ids": []}
            if not apply:
                conn.rollback()
                return report
            created = []
            for row in state["proposal"]:
                cur.execute(
                    """INSERT INTO prints(set_id,card_id,collector_number,language,rarity,is_foil,variant,print_key)
                       VALUES(%s,%s,%s,%s,%s,false,%s,%s) RETURNING id""",
                    (state["set_id"], row["card_id"], row["collector"], LANGUAGE, row["rarity"], row["variant"], row["print_key"]),
                )
                pid = int(cur.fetchone()["id"]); created.append(pid)
                cur.execute("INSERT INTO print_images(print_id,url,is_primary,source) VALUES(%s,%s,true,%s)", (pid, row["image_url"], IMAGE_SOURCE))
                cur.execute("INSERT INTO print_identifiers(print_id,source,external_id) VALUES(%s,%s,%s)", (pid, IDENTIFIER_SOURCE, row["external_id"]))
            cur.execute("SELECT count(*) n FROM prints WHERE set_id=%s AND lower(coalesce(language,''))=%s", (state["set_id"], LANGUAGE))
            if int(cur.fetchone()["n"]) != EXPECTED_AFTER:
                raise RuntimeError("post_apply_OP16_JA_count_failed")
            cur.execute("SELECT count(*) n FROM cards WHERE game_id=%s", (state["game_id"],)); cards_after=int(cur.fetchone()["n"])
            cur.execute("SELECT count(*) n FROM sets WHERE game_id=%s", (state["game_id"],)); sets_after=int(cur.fetchone()["n"])
            if cards_after != state["cards_before"] or sets_after != state["sets_before"]:
                raise RuntimeError("logical_catalog_mutated")
            report.update({"production_writes": len(created)*3, "created_print_ids": created, "ja_after": EXPECTED_AFTER})
            conn.commit(); return report
    except Exception:
        conn.rollback(); raise
    finally:
        conn.close()


def main() -> int:
    parser=argparse.ArgumentParser(); parser.add_argument("--apply",action="store_true"); parser.add_argument("--confirm",default=""); parser.add_argument("--report",type=Path,default=Path("/tmp/onepiece-jp-op16-residual-writer-v1.json")); args=parser.parse_args()
    payload=run(apply=args.apply,confirm=args.confirm); args.report.parent.mkdir(parents=True,exist_ok=True); text=json.dumps(payload,ensure_ascii=False,indent=2,sort_keys=True,default=str)+"\n"; args.report.write_text(text,encoding="utf-8"); print(text,end=""); return 0


if __name__ == "__main__": raise SystemExit(main())
