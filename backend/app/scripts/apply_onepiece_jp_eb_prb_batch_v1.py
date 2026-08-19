from __future__ import annotations

import argparse
import json
import os
import re
from collections import defaultdict
from pathlib import Path

import psycopg2
from psycopg2.extras import RealDictCursor

from app.ingest.normalization import normalize_collector_number, normalize_variant
from app.scripts.audit_onepiece_jp_full_surface_v1 import _load_official

GAME = "onepiece"
LANGUAGE = "ja"
IDENTIFIER_SOURCE = "onepiece_official_jp"
IMAGE_SOURCE = "onepiece_official_jp"
FULL_SURFACE_SHA256 = "772684372981c8004acc0b17598f2853118b2ea0c375e5654631b2cfacdf2008"
EXPECTED = {
    "EB01": {"physical": 118, "logical": 61},
    "EB02": {"physical": 93, "logical": 61},
    "EB03": {"physical": 94, "logical": 62},
    "EB04": {"physical": 88, "logical": 61},
    "PRB01": {"physical": 2, "logical": 1},
    "PRB02": {"physical": 39, "logical": 18},
}
EXPECTED_TOTAL = 434
CONFIRM = "APPLY_ONEPIECE_JP_EB_PRB_BATCH_V1"


def _norm_set(value: object) -> str:
    return re.sub(r"[^A-Z0-9]+", "", str(value or "").upper())


def _connect(*, readonly: bool):
    url = os.getenv("DATABASE_URL_UNPOOLED") or os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL_UNPOOLED or DATABASE_URL is required")
    conn = psycopg2.connect(url, connect_timeout=30, application_name="dontripit_onepiece_jp_eb_prb_v1")
    conn.set_session(readonly=readonly, autocommit=False)
    return conn


def _targets_from_source() -> dict[str, list[dict]]:
    official = _load_official()
    if official["digest"] != FULL_SURFACE_SHA256:
        raise RuntimeError({"official_full_surface_digest_drift": {"expected": FULL_SURFACE_SHA256, "actual": official["digest"]}})
    selected = {}
    for token, expected in EXPECTED.items():
        rows = list(official["sets"].get(token) or [])
        logical = {row["collector_number"] for row in rows}
        if len(rows) != expected["physical"] or len(logical) != expected["logical"]:
            raise RuntimeError({"official_set_surface_drift": {"set": token, "physical": len(rows), "logical": len(logical), "expected": expected}})
        if any(not str(row.get("image_url") or "").startswith("https://www.onepiece-cardgame.com/") for row in rows):
            raise RuntimeError({"official_set_image_drift": token})
        if len({row["source_print_id"] for row in rows}) != len(rows):
            raise RuntimeError({"official_source_id_collision": token})
        selected[token] = rows
    if sum(len(rows) for rows in selected.values()) != EXPECTED_TOTAL:
        raise RuntimeError("selected batch total drift")
    return selected


def _build(cur, source: dict[str, list[dict]]) -> dict:
    cur.execute("SELECT id FROM games WHERE slug=%s LIMIT 1", (GAME,))
    game = cur.fetchone()
    if not game:
        raise RuntimeError("One Piece game row missing")
    game_id = int(game["id"])
    cur.execute("SELECT count(*) n FROM cards WHERE game_id=%s", (game_id,))
    cards_before = int(cur.fetchone()["n"])
    cur.execute("SELECT count(*) n FROM sets WHERE game_id=%s", (game_id,))
    sets_before = int(cur.fetchone()["n"])
    cur.execute("SELECT id,code,name,region FROM sets WHERE game_id=%s", (game_id,))
    all_sets = [dict(row) for row in cur.fetchall()]
    sets_by_token = defaultdict(list)
    for row in all_sets:
        sets_by_token[_norm_set(row["code"])].append(row)

    set_states = {}
    all_targets = []
    all_existing = []
    all_proposal = []

    for token, source_rows in source.items():
        matches = sets_by_token.get(token, [])
        if len(matches) != 1:
            raise RuntimeError({"set_not_unique": {"set": token, "matches": matches}})
        set_row = matches[0]
        set_id = int(set_row["id"])
        cur.execute(
            """SELECT p.id print_id,p.card_id,p.collector_number,p.language,p.variant,p.rarity,p.print_key,c.name card_name
               FROM prints p JOIN cards c ON c.id=p.card_id
               WHERE p.set_id=%s ORDER BY p.language,p.collector_number,p.variant,p.id""",
            (set_id,),
        )
        db_rows = [dict(row) for row in cur.fetchall()]
        card_ids_by_collector = defaultdict(set)
        for row in db_rows:
            collector = str(row["collector_number"] or "").upper().strip()
            if collector:
                card_ids_by_collector[collector].add(int(row["card_id"]))
        source_collectors = {row["collector_number"] for row in source_rows}
        if set(card_ids_by_collector) != source_collectors:
            raise RuntimeError({"logical_collector_mismatch": {"set": token, "only_source": sorted(source_collectors-set(card_ids_by_collector)), "only_db": sorted(set(card_ids_by_collector)-source_collectors)}})
        collisions = {collector: sorted(ids) for collector, ids in card_ids_by_collector.items() if len(ids) != 1}
        if collisions:
            raise RuntimeError({"collector_card_collision": {"set": token, "collisions": collisions}})

        targets = []
        for source_row in source_rows:
            collector = source_row["collector_number"]
            variant = normalize_variant(source_row["variant"])
            card_id = next(iter(card_ids_by_collector[collector]))
            targets.append(
                {
                    "set_token": token,
                    "set_id": set_id,
                    "set_code": str(set_row["code"]),
                    "card_id": card_id,
                    "collector_number": collector,
                    "variant": variant,
                    "rarity": source_row.get("rarity"),
                    "image_url": source_row["image_url"],
                    "external_id": source_row["source_print_id"],
                    "print_key": f"onepiece:{str(set_row['code']).lower()}:{normalize_collector_number(collector)}:{LANGUAGE}:{variant}",
                }
            )
        ja_rows = [row for row in db_rows if str(row.get("language") or "").lower() == LANGUAGE]
        expected_count = EXPECTED[token]["physical"]
        if len(ja_rows) not in (0, expected_count):
            raise RuntimeError({"partial_JA_surface_blocked": {"set": token, "count": len(ja_rows)}})
        ja_by_key = {(str(row["collector_number"]).upper(), normalize_variant(row["variant"])): row for row in ja_rows}
        target_by_key = {(row["collector_number"], row["variant"]): row for row in targets}
        if ja_rows and set(ja_by_key) != set(target_by_key):
            raise RuntimeError({"existing_JA_key_drift": {"set": token, "only_db": sorted(set(ja_by_key)-set(target_by_key)), "only_source": sorted(set(target_by_key)-set(ja_by_key))}})

        existing = []
        proposal = []
        for key, target in target_by_key.items():
            db = ja_by_key.get(key)
            if db is None:
                proposal.append(target)
                continue
            failures = []
            if int(db["card_id"]) != target["card_id"]: failures.append("card_id")
            if str(db.get("rarity") or "") != str(target.get("rarity") or ""): failures.append("rarity")
            if str(db.get("print_key") or "") != target["print_key"]: failures.append("print_key")
            if failures:
                raise RuntimeError({"existing_JA_identity_drift": {"set": token, "key": key, "failures": failures}})
            existing.append({**target, "print_id": int(db["print_id"])})

        external_ids = [row["external_id"] for row in targets]
        cur.execute(
            """SELECT ident.print_id,ident.external_id,p.language,p.collector_number,p.variant
               FROM print_identifiers ident JOIN prints p ON p.id=ident.print_id
               WHERE ident.source=%s AND ident.external_id=ANY(%s)""",
            (IDENTIFIER_SOURCE, external_ids),
        )
        claims = [dict(row) for row in cur.fetchall()]
        if not existing and claims:
            raise RuntimeError({"preexisting_identifier_claims_without_JA_surface": {"set": token, "claims": claims}})
        if existing:
            claims_by_external = defaultdict(list)
            for claim in claims:
                claims_by_external[str(claim["external_id"])].append(claim)
            print_ids = [row["print_id"] for row in existing]
            cur.execute("SELECT print_id,url,is_primary,source FROM print_images WHERE print_id=ANY(%s) ORDER BY print_id,id", (print_ids,))
            images_by_print = defaultdict(list)
            for image in cur.fetchall():
                images_by_print[int(image["print_id"])].append(dict(image))
            for row in existing:
                claims_for = claims_by_external.get(row["external_id"], [])
                if len(claims_for) != 1 or int(claims_for[0]["print_id"]) != row["print_id"]:
                    raise RuntimeError({"existing_identifier_drift": {"set": token, "external_id": row["external_id"], "claims": claims_for}})
                exact_images = [img for img in images_by_print.get(row["print_id"], []) if bool(img["is_primary"]) and str(img["url"]) == row["image_url"] and str(img.get("source") or "") == IMAGE_SOURCE]
                if len(exact_images) != 1:
                    raise RuntimeError({"existing_image_drift": {"set": token, "print_id": row["print_id"]}})

        set_states[token] = {"set_id": set_id, "existing": len(existing), "proposal": len(proposal)}
        all_targets.extend(targets)
        all_existing.extend(existing)
        all_proposal.extend(proposal)

    if len(all_targets) != EXPECTED_TOTAL or len(all_existing) + len(all_proposal) != EXPECTED_TOTAL:
        raise RuntimeError("batch target accounting drift")
    return {
        "game_id": game_id,
        "cards_before": cards_before,
        "sets_before": sets_before,
        "targets": all_targets,
        "existing": all_existing,
        "proposal": all_proposal,
        "set_states": set_states,
    }


def run(*, apply: bool, confirm: str = "") -> dict:
    if apply and confirm != CONFIRM:
        raise RuntimeError(f"--apply requires --confirm {CONFIRM}")
    source = _targets_from_source()
    conn = _connect(readonly=not apply)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            state = _build(cur, source)
            report = {
                "mode": "apply" if apply else "dry_run",
                "status": "pass",
                "production_writes": 0,
                "official_full_surface_sha256": FULL_SURFACE_SHA256,
                "sets": sorted(EXPECTED),
                "target_physical": EXPECTED_TOTAL,
                "already_exact": len(state["existing"]),
                "new_prints_ready": len(state["proposal"]),
                "cards_before": state["cards_before"],
                "sets_before": state["sets_before"],
                "set_states": state["set_states"],
                "created_print_ids": [],
            }
            if not apply:
                conn.rollback()
                return report

            created = []
            for row in state["proposal"]:
                cur.execute(
                    """INSERT INTO prints(set_id,card_id,collector_number,language,rarity,is_foil,variant,print_key)
                       VALUES(%s,%s,%s,%s,%s,false,%s,%s) RETURNING id""",
                    (row["set_id"], row["card_id"], row["collector_number"], LANGUAGE, row["rarity"], row["variant"], row["print_key"]),
                )
                print_id = int(cur.fetchone()["id"])
                created.append(print_id)
                cur.execute("INSERT INTO print_images(print_id,url,is_primary,source) VALUES(%s,%s,true,%s)", (print_id, row["image_url"], IMAGE_SOURCE))
                cur.execute("INSERT INTO print_identifiers(print_id,source,external_id) VALUES(%s,%s,%s)", (print_id, IDENTIFIER_SOURCE, row["external_id"]))

            cur.execute("SELECT count(*) n FROM cards WHERE game_id=%s", (state["game_id"],))
            cards_after = int(cur.fetchone()["n"])
            cur.execute("SELECT count(*) n FROM sets WHERE game_id=%s", (state["game_id"],))
            sets_after = int(cur.fetchone()["n"])
            if cards_after != state["cards_before"] or sets_after != state["sets_before"]:
                raise RuntimeError({"logical_catalog_mutated": {"cards": (state["cards_before"], cards_after), "sets": (state["sets_before"], sets_after)}})
            for token, expected in EXPECTED.items():
                set_id = state["set_states"][token]["set_id"]
                cur.execute("SELECT count(*) n FROM prints WHERE set_id=%s AND lower(coalesce(language,''))=%s", (set_id, LANGUAGE))
                count = int(cur.fetchone()["n"])
                if count != expected["physical"]:
                    raise RuntimeError({"post_apply_set_count_drift": {"set": token, "count": count, "expected": expected["physical"]}})
            report["created_print_ids"] = created
            report["production_writes"] = len(created) * 3
            report["cards_after"] = cards_after
            report["sets_after"] = sets_after
            conn.commit()
            return report
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply exact Japanese EB01-04 and PRB01-02 One Piece physical prints")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm", default="")
    parser.add_argument("--report", type=Path, default=Path("/tmp/onepiece-jp-eb-prb-batch-v1.json"))
    args = parser.parse_args()
    payload = run(apply=args.apply, confirm=args.confirm)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n"
    args.report.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
