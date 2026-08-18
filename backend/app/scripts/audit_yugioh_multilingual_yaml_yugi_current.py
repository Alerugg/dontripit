#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterator, Mapping

import ijson
from sqlalchemy import create_engine, text

TARGETS = ("es", "ja")


def s(v: Any) -> str:
    return str(v or "").strip()


def sl(v: Any) -> str:
    return s(v).lower()


def mapping(v: Any) -> Mapping[str, Any]:
    return v if isinstance(v, Mapping) else {}


def iter_cards(path: Path) -> Iterator[dict[str, Any]]:
    with path.open("rb") as fh:
        first = b""
        while True:
            ch = fh.read(1)
            if not ch:
                break
            if not ch.isspace():
                first = ch
                break
        fh.seek(0)
        if first == b"[":
            yield from (x for x in ijson.items(fh, "item") if isinstance(x, dict))
        elif first == b"{":
            yield from (x for _k, x in ijson.kvitems(fh, "") if isinstance(x, dict))
        else:
            raise ValueError("Unsupported YAML Yugi aggregate JSON shape")


def family(code: str) -> str:
    c = s(code).upper()
    return c.split("-", 1)[0] if "-" in c else c


def quality(code: str) -> str:
    c = s(code)
    if not c:
        return "missing"
    if any(ch in c for ch in ("?", "*")):
        return "placeholder"
    return "exact"


def run(cards_path: Path, report_path: Path, source_head: str, source_last_modified: str) -> dict[str, Any]:
    url = os.getenv("DATABASE_URL_UNPOOLED") or os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL_UNPOOLED or DATABASE_URL is required")

    engine = create_engine(url, pool_pre_ping=True)
    with engine.connect() as conn:
        tx = conn.begin()
        conn.execute(text("SET TRANSACTION READ ONLY"))
        ro = sl(conn.execute(text("SHOW transaction_read_only")).scalar_one())
        if ro not in {"on", "true", "1"}:
            raise AssertionError(f"transaction_read_only={ro!r}")
        gid = conn.execute(text("SELECT id FROM games WHERE slug='yugioh' LIMIT 1")).scalar_one()
        db_cards = {
            s(ext): int(cid)
            for cid, ext in conn.execute(text(
                "SELECT id, yugoprodeck_id FROM cards WHERE game_id=:g AND yugoprodeck_id IS NOT NULL"
            ), {"g": gid})
            if s(ext)
        }
        db_sets = {
            s(code).upper(): int(sid)
            for sid, code in conn.execute(text("SELECT id, code FROM sets WHERE game_id=:g"), {"g": gid})
            if s(code)
        }
        tx.rollback()

    stats = {
        lang: {
            "cards_with_sets": set(), "cards_with_name": set(), "cards_with_text": set(),
            "memberships": [], "quality": Counter(), "rarities": Counter(),
            "password_cards": set(), "konami_cards": set(), "db_card_ids": set(), "db_set_ids": set(),
            "missing_password_cards": set(), "missing_set_families": set(),
        } for lang in TARGETS
    }
    total_cards = 0
    cards_with_password = 0
    cards_with_konami = 0

    for card in iter_cards(cards_path):
        total_cards += 1
        password = s(card.get("password"))
        konami_id = s(card.get("konami_id"))
        if password:
            cards_with_password += 1
        if konami_id:
            cards_with_konami += 1
        names = mapping(card.get("name"))
        texts = mapping(card.get("text"))
        sets = mapping(card.get("sets"))
        source_card_key = password or (f"kdb:{konami_id}" if konami_id else f"row:{total_cards}")

        for lang in TARGETS:
            rows = sets.get(lang) or []
            if isinstance(rows, Mapping):
                rows = [rows]
            if not isinstance(rows, list) or not rows:
                continue
            st = stats[lang]
            st["cards_with_sets"].add(source_card_key)
            if s(names.get(lang)):
                st["cards_with_name"].add(source_card_key)
            if s(texts.get(lang)):
                st["cards_with_text"].add(source_card_key)
            if password:
                st["password_cards"].add(source_card_key)
            if konami_id:
                st["konami_cards"].add(source_card_key)
            db_card_id = db_cards.get(password) if password else None
            if db_card_id is not None:
                st["db_card_ids"].add(db_card_id)
            else:
                st["missing_password_cards"].add(source_card_key)

            for row in rows:
                if not isinstance(row, Mapping):
                    continue
                code = s(row.get("set_number")).upper()
                set_name = s(row.get("set_name"))
                rarities = row.get("rarities") or []
                if not isinstance(rarities, list):
                    rarities = [rarities]
                if not rarities:
                    rarities = [None]
                fam = family(code)
                db_set_id = db_sets.get(fam)
                if db_set_id is not None:
                    st["db_set_ids"].add(db_set_id)
                else:
                    st["missing_set_families"].add(fam)
                for rarity_raw in rarities:
                    rarity = s(rarity_raw) or "unknown"
                    st["quality"][quality(code)] += 1
                    st["rarities"][rarity] += 1
                    st["memberships"].append({
                        "card_key": source_card_key,
                        "password": password or None,
                        "konami_id": konami_id or None,
                        "set_number": code,
                        "set_family": fam,
                        "set_name": set_name or None,
                        "rarity": rarity,
                        "db_card_id": db_card_id,
                        "db_set_id": db_set_id,
                    })

    targets: dict[str, Any] = {}
    for lang, st in stats.items():
        exact = [r for r in st["memberships"] if quality(r["set_number"]) == "exact"]
        ambiguous = [r for r in st["memberships"] if quality(r["set_number"]) != "exact"]
        exact_identity: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
        for r in exact:
            exact_identity[(r["set_number"], sl(r["rarity"]), r["card_key"])].append(r)
        duplicate_rows = sum(len(v) - 1 for v in exact_identity.values() if len(v) > 1)

        by_print_slot: dict[tuple[str, str], set[str]] = defaultdict(set)
        for r in exact:
            by_print_slot[(r["set_number"], sl(r["rarity"]))].add(r["card_key"])
        conflict_slots = {k: v for k, v in by_print_slot.items() if len(v) > 1}

        dedup = {k: v[0] for k, v in exact_identity.items()}
        exact_rows = list(dedup.values())
        db_card_match = sum(1 for r in exact_rows if r["db_card_id"] is not None)
        db_set_match = sum(1 for r in exact_rows if r["db_set_id"] is not None)
        both_match = sum(1 for r in exact_rows if r["db_card_id"] is not None and r["db_set_id"] is not None)
        unique_set_numbers = {r["set_number"] for r in exact_rows}
        unique_families = {r["set_family"] for r in exact_rows}
        unique_rarities = {r["rarity"] for r in exact_rows}
        card_den = max(len(st["cards_with_sets"]), 1)
        row_den = max(len(exact_rows), 1)
        targets[lang] = {
            "cards_with_physical_set_membership": len(st["cards_with_sets"]),
            "cards_with_localized_name": len(st["cards_with_name"]),
            "localized_name_pct": round(100 * len(st["cards_with_name"]) / card_den, 4),
            "cards_with_localized_text": len(st["cards_with_text"]),
            "localized_text_pct": round(100 * len(st["cards_with_text"]) / card_den, 4),
            "cards_with_password": len(st["password_cards"]),
            "cards_with_konami_id": len(st["konami_cards"]),
            "raw_print_memberships_expanded_by_rarity": len(st["memberships"]),
            "exact_unique_card_set_rarity_rows": len(exact_rows),
            "ambiguous_or_placeholder_rows": len(ambiguous),
            "duplicate_exact_rows_same_card": duplicate_rows,
            "physical_slot_conflicts": len(conflict_slots),
            "physical_slot_conflict_samples": [
                {"set_number": k[0], "rarity": k[1], "card_keys": sorted(v)}
                for k, v in list(conflict_slots.items())[:30]
            ],
            "unique_set_numbers": len(unique_set_numbers),
            "unique_set_families": len(unique_families),
            "unique_rarities": len(unique_rarities),
            "db_card_exact_match_rows": db_card_match,
            "db_card_exact_match_pct": round(100 * db_card_match / row_den, 4),
            "db_existing_set_family_rows": db_set_match,
            "db_existing_set_family_pct": round(100 * db_set_match / row_den, 4),
            "db_card_and_set_match_rows": both_match,
            "db_card_and_set_match_pct": round(100 * both_match / row_den, 4),
            "unique_db_cards_matched": len(st["db_card_ids"]),
            "unique_db_sets_matched": len(st["db_set_ids"]),
            "unique_source_cards_missing_db_password_bridge": len(st["missing_password_cards"]),
            "unique_missing_set_families": len(st["missing_set_families"] - {""}),
            "missing_set_family_samples": sorted(x for x in st["missing_set_families"] if x)[:50],
            "rarities_top": st["rarities"].most_common(30),
            "collector_quality": dict(st["quality"]),
        }

    report = {
        "mode": "read_only_current_yaml_yugi_source_and_db_audit",
        "production_writes": 0,
        "database_transaction_read_only": True,
        "source": {
            "project": "DawnbrandBots/yaml-yugi",
            "api": "https://dawnbrandbots.github.io/yaml-yugi/cards.json",
            "master_head_sha": source_head,
            "http_last_modified": source_last_modified or None,
            "total_cards": total_cards,
            "cards_with_password": cards_with_password,
            "cards_with_konami_id": cards_with_konami,
        },
        "identity_policy": {
            "card": "password -> Card.yugoprodeck_id exact only; konami_id retained as secondary evidence",
            "print": "language-specific set_number + rarity + source card identity",
            "regional_set": "set_number family; missing JA families are not forced into EN/TCG sets",
            "images": "not certified per localized physical print; card-level image filenames are not treated as localized print images",
            "known_granularity_limit": "YAML Yugi set rows do not expose YGOJSON-style physical printing UUID/edition linkage; use as current identity/delta source, not a blind historical replacement",
        },
        "database_inventory": {"cards_with_yugoprodeck_id": len(db_cards), "sets": len(db_sets)},
        "targets": targets,
    }
    report["gates"] = {
        "read_only_enforced": True,
        "spanish_physical_rows_present": targets["es"]["exact_unique_card_set_rarity_rows"] > 0,
        "japanese_physical_rows_present": targets["ja"]["exact_unique_card_set_rarity_rows"] > 0,
        "spanish_names_present": targets["es"]["localized_name_pct"] > 95,
        "japanese_names_present": targets["ja"]["localized_name_pct"] > 95,
        "no_unquarantined_slot_conflicts": targets["es"]["physical_slot_conflicts"] == 0 and targets["ja"]["physical_slot_conflicts"] == 0,
    }
    report["structural_pass"] = all(report["gates"].values())
    report["rollout_ready"] = False
    report["rollout_blockers"] = [
        "cross-source reconciliation with YGOJSON historical UUID/edition identities not yet certified",
        "ephemeral database backfill not yet executed",
        "localized image linkage is not proven by YAML Yugi set rows",
    ]
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return report


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cards", type=Path, required=True)
    ap.add_argument("--report", type=Path, required=True)
    ap.add_argument("--source-head", default="")
    ap.add_argument("--source-last-modified", default="")
    args = ap.parse_args()
    run(args.cards, args.report, args.source_head, args.source_last_modified)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
