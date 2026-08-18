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

TARGETS = {
    "es": {"locale": "sp", "language": "es"},
    "ja": {"locale": "jp", "language": "ja"},
}


def s(v: Any) -> str:
    return str(v or "").strip()


def sl(v: Any) -> str:
    return s(v).lower()


def mapping(v: Any) -> Mapping[str, Any]:
    return v if isinstance(v, Mapping) else {}


def as_list(v: Any) -> list[Any]:
    if v is None:
        return []
    return list(v) if isinstance(v, (list, tuple, set)) else [v]


def iter_records(path: Path) -> Iterator[dict[str, Any]]:
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
            return
        if first == b"{":
            yield from (x for _k, x in ijson.kvitems(fh, "") if isinstance(x, dict))
            return
        raise ValueError(f"Unsupported JSON top-level shape: {path}")


def find_file(root: Path, name: str) -> Path:
    hits = sorted((p for p in root.rglob(name) if p.is_file()), key=lambda p: (len(p.parts), str(p)))
    if not hits:
        raise FileNotFoundError(name)
    return hits[0]


def external_scalar(v: Any, keys: tuple[str, ...]) -> str:
    if isinstance(v, Mapping):
        for key in keys:
            if key in v and s(v.get(key)):
                return s(v.get(key))
        return ""
    if isinstance(v, (list, tuple)):
        for item in v:
            got = external_scalar(item, keys)
            if got:
                return got
        return ""
    return s(v)


def ext_ids(card: Mapping[str, Any]) -> tuple[str, str]:
    ext = mapping(card.get("externalIDs") or card.get("external_ids"))
    ygo = external_scalar(ext.get("ygoprodeck"), ("id", "cardID", "card_id", "value"))
    konami = external_scalar(ext.get("konami"), ("cid", "id", "cardID", "card_id", "dbID", "value"))
    if not konami:
        konami = external_scalar(card.get("dbID"), ("cid", "id", "dbID", "value"))
    if not konami:
        konami = external_scalar(ext.get("dbID") or ext.get("officialID"), ("cid", "id", "dbID", "value"))
    return ygo, konami


def content_locales(content: Mapping[str, Any]) -> set[str]:
    raw = content.get("locales")
    if isinstance(raw, Mapping):
        return {sl(k) for k, v in raw.items() if v not in (False, None, "")}
    return {sl(x) for x in as_list(raw) if s(x)}


def iter_printings(content: Mapping[str, Any]) -> Iterator[Mapping[str, Any]]:
    rows = content.get("cards") or []
    if isinstance(rows, Mapping):
        rows = rows.values()
    for row in rows:
        if isinstance(row, Mapping):
            yield row


def pid(row: Mapping[str, Any]) -> str:
    return s(row.get("id") or row.get("uuid"))


def cid(row: Mapping[str, Any]) -> str:
    value = row.get("card")
    return s(value.get("id") or value.get("uuid")) if isinstance(value, Mapping) else s(value)


def collector_code(prefix: str, suffix: str) -> str:
    p, q = s(prefix), s(suffix)
    if not q:
        return ""
    if not p or q.upper().startswith(p.upper()) or "-" in q:
        return q
    return f"{p}{q}"


def collector_quality(code: str) -> str:
    if not s(code):
        return "missing"
    if any(ch in code for ch in ("?", "*")):
        return "placeholder"
    return "exact"


def family_from_collector(code: str) -> str:
    c = s(code).upper()
    return c.split("-", 1)[0].strip() if "-" in c else c


def build_source(root: Path) -> tuple[dict[str, dict[str, str]], dict[str, list[dict[str, str]]], dict[str, set[str]]]:
    cards_path, sets_path = find_file(root, "cards.json"), find_file(root, "sets.json")
    cards: dict[str, dict[str, str]] = {}
    for card in iter_records(cards_path):
        card_uuid = s(card.get("id") or card.get("uuid"))
        if not card_uuid:
            continue
        ygo, konami = ext_ids(card)
        cards[card_uuid] = {"ygoprodeck": ygo, "konami": konami}

    rows: dict[str, list[dict[str, str]]] = {k: [] for k in TARGETS}
    logical: dict[str, tuple[str, str, str]] = {}
    logical_conflicts: set[str] = set()
    for set_obj in iter_records(sets_path):
        set_uuid = s(set_obj.get("id") or set_obj.get("uuid"))
        locales = mapping(set_obj.get("locales"))
        raw_contents = set_obj.get("contents") or []
        contents = list(raw_contents.values()) if isinstance(raw_contents, Mapping) else list(raw_contents)
        contents = [x for x in contents if isinstance(x, Mapping)]
        for target, spec in TARGETS.items():
            loc = locales.get(spec["locale"])
            if not isinstance(loc, Mapping):
                continue
            loc_lang = sl(loc.get("language") or loc.get("lang"))
            prefix = s(loc.get("prefix"))
            for content in contents:
                scoped = content_locales(content)
                if not scoped or spec["locale"] not in scoped:
                    continue
                for row in iter_printings(content):
                    print_uuid = pid(row)
                    card_uuid = cid(row)
                    rarity = sl(row.get("rarity")) or "unknown"
                    effective_lang = sl(row.get("language")) or loc_lang
                    if print_uuid:
                        semantic = (card_uuid, set_uuid, rarity)
                        old = logical.get(print_uuid)
                        if old is None:
                            logical[print_uuid] = semantic
                        elif old != semantic:
                            logical_conflicts.add(print_uuid)
                    if effective_lang != spec["language"]:
                        continue
                    collector = collector_code(prefix, s(row.get("suffix")))
                    rows[target].append({
                        "print_uuid": print_uuid,
                        "card_uuid": card_uuid,
                        "set_uuid": set_uuid,
                        "collector": collector,
                        "family": family_from_collector(collector),
                        "rarity": rarity,
                        "quality": collector_quality(collector),
                    })

    quarantines: dict[str, set[str]] = {}
    for target, target_rows in rows.items():
        placeholders = {r["print_uuid"] for r in target_rows if r["print_uuid"] and r["quality"] == "placeholder"}
        groups: dict[tuple[str, str, str], list[dict[str, str]]] = defaultdict(list)
        for r in target_rows:
            if r["quality"] == "exact":
                groups[(r["set_uuid"], r["collector"].upper(), r["rarity"])].append(r)
        collisions: set[str] = set()
        for group in groups.values():
            card_ids = {r["card_uuid"] for r in group if r["card_uuid"]}
            if len(group) > 1 and len(card_ids) > 1:
                collisions.update(r["print_uuid"] for r in group if r["print_uuid"])
        target_ids = {r["print_uuid"] for r in target_rows if r["print_uuid"]}
        quarantines[target] = (logical_conflicts & target_ids) | placeholders | collisions
    return cards, rows, quarantines


def run(root: Path, report_path: Path) -> dict[str, Any]:
    cards, source_rows, quarantines = build_source(root)
    url = os.getenv("DATABASE_URL_UNPOOLED") or os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL_UNPOOLED or DATABASE_URL is required")

    engine = create_engine(url, pool_pre_ping=True)
    with engine.connect() as conn:
        tx = conn.begin()
        conn.execute(text("SET TRANSACTION READ ONLY"))
        ro = str(conn.execute(text("SHOW transaction_read_only")).scalar_one()).lower()
        if ro not in {"on", "true", "1"}:
            raise AssertionError(f"transaction_read_only={ro!r}")
        game_id = conn.execute(text("SELECT id FROM games WHERE slug='yugioh' LIMIT 1")).scalar_one()
        db_cards = {
            str(ext): int(dbid)
            for dbid, ext in conn.execute(text(
                "SELECT id, yugoprodeck_id FROM cards WHERE game_id=:g AND yugoprodeck_id IS NOT NULL"
            ), {"g": game_id}) if ext is not None
        }
        db_sets = {
            str(code).upper(): int(dbid)
            for dbid, code in conn.execute(text("SELECT id, code FROM sets WHERE game_id=:g"), {"g": game_id})
        }
        existing_ygo_ids = {
            str(v) for v in conn.execute(text(
                "SELECT p.yugioh_id FROM prints p JOIN cards c ON c.id=p.card_id WHERE c.game_id=:g AND p.yugioh_id IS NOT NULL"
            ), {"g": game_id}).scalars()
        }
        db_print_rows = list(conn.execute(text('''
            SELECT p.id, p.set_id, p.card_id, upper(p.collector_number), coalesce(lower(p.language),''),
                   coalesce(lower(p.rarity),''), p.is_foil, p.variant, p.yugioh_id
            FROM prints p JOIN cards c ON c.id=p.card_id WHERE c.game_id=:g
        '''), {"g": game_id}))
        tx.rollback()

    db_physical: dict[tuple[int, str, str], set[int]] = defaultdict(set)
    for _print_id, set_id, card_id, collector, language, _rarity, _foil, _variant, _yid in db_print_rows:
        db_physical[(int(set_id), s(collector).upper(), sl(language))].add(int(card_id))

    targets: dict[str, Any] = {}
    for target, spec in TARGETS.items():
        q = quarantines[target]
        exact_rows = [r for r in source_rows[target] if r["print_uuid"] and r["quality"] != "placeholder" and r["print_uuid"] not in q]
        unique: dict[str, dict[str, str]] = {}
        for r in exact_rows:
            unique.setdefault(r["print_uuid"], r)
        certifiable = list(unique.values())
        counters = Counter()
        missing_card_samples, missing_set_samples, collision_samples, uuid_collision_samples = [], [], [], []
        official_bridge_cards: set[str] = set()
        ygo_bridge_cards: set[str] = set()
        matched_card_ids: set[int] = set()
        matched_set_ids: set[int] = set()

        for r in certifiable:
            card = cards.get(r["card_uuid"]) or {}
            ygo_id, konami_id = s(card.get("ygoprodeck")), s(card.get("konami"))
            if ygo_id:
                ygo_bridge_cards.add(r["card_uuid"])
            if konami_id:
                official_bridge_cards.add(r["card_uuid"])
            db_card_id = db_cards.get(ygo_id) if ygo_id else None
            if db_card_id is None:
                counters["card_not_in_db"] += 1
                if len(missing_card_samples) < 30:
                    missing_card_samples.append({"print_uuid": r["print_uuid"], "collector": r["collector"], "ygo_id": ygo_id or None, "konami_id": konami_id or None})
            else:
                counters["card_exact_match"] += 1
                matched_card_ids.add(db_card_id)

            db_set_id = db_sets.get(r["family"])
            if db_set_id is None:
                counters["set_family_not_in_db"] += 1
                if len(missing_set_samples) < 30:
                    missing_set_samples.append({"set_uuid": r["set_uuid"], "family": r["family"], "collector": r["collector"]})
            else:
                counters["set_family_exact_match"] += 1
                matched_set_ids.add(db_set_id)

            if r["print_uuid"] in existing_ygo_ids:
                counters["uuid_already_used"] += 1
                if len(uuid_collision_samples) < 30:
                    uuid_collision_samples.append(r["print_uuid"])

            if db_card_id is not None and db_set_id is not None:
                existing_cards = db_physical.get((db_set_id, r["collector"].upper(), spec["language"]), set())
                if not existing_cards:
                    counters["physical_tuple_free"] += 1
                elif existing_cards == {db_card_id}:
                    counters["physical_tuple_same_card_exists"] += 1
                else:
                    counters["physical_tuple_conflict"] += 1
                    if len(collision_samples) < 30:
                        collision_samples.append({"print_uuid": r["print_uuid"], "collector": r["collector"], "source_card_id": db_card_id, "existing_card_ids": sorted(existing_cards)})

        n = len(certifiable) or 1
        targets[target] = {
            "language": spec["language"], "source_memberships": len(source_rows[target]),
            "quarantined_print_ids": len(q), "certifiable_unique_print_ids": len(certifiable),
            "card_exact_match_rows": counters["card_exact_match"],
            "card_exact_match_pct": round(100 * counters["card_exact_match"] / n, 4),
            "card_not_in_db_rows": counters["card_not_in_db"],
            "set_family_exact_match_rows": counters["set_family_exact_match"],
            "set_family_exact_match_pct": round(100 * counters["set_family_exact_match"] / n, 4),
            "set_family_not_in_db_rows": counters["set_family_not_in_db"],
            "uuid_already_used_rows": counters["uuid_already_used"],
            "physical_tuple_free_rows": counters["physical_tuple_free"],
            "physical_tuple_same_card_exists_rows": counters["physical_tuple_same_card_exists"],
            "physical_tuple_conflict_rows": counters["physical_tuple_conflict"],
            "unique_db_cards_matched": len(matched_card_ids), "unique_db_sets_matched": len(matched_set_ids),
            "ygoprodeck_bridge_source_cards": len(ygo_bridge_cards), "konami_bridge_source_cards": len(official_bridge_cards),
            "samples": {"missing_cards": missing_card_samples, "missing_set_families": missing_set_samples,
                        "physical_tuple_conflicts": collision_samples, "uuid_collisions": uuid_collision_samples},
        }

    report = {
        "mode": "read_only_database_compatibility_audit", "production_writes": 0,
        "database_transaction_read_only": True,
        "identity_policy": {
            "card": "exact YGOJSON externalIDs.ygoprodeck -> Card.yugoprodeck_id only; no name matching",
            "set": "exact collector family -> Set.code only; no fuzzy release-name matching",
            "print_uuid": "YGOJSON physical UUID checked against existing global Print.yugioh_id",
            "physical_collision": "conservative existing tuple check by set + full collector + target language, ignoring variant",
            "quarantine": "same placeholder/logical-UUID/localized-identity collision policy as source audit v3",
        },
        "database_inventory": {"cards_with_yugoprodeck_id": len(db_cards), "sets": len(db_sets),
                               "prints": len(db_print_rows), "prints_with_yugioh_id": len(existing_ygo_ids)},
        "targets": targets,
    }
    report["gates"] = {
        "read_only_enforced": True,
        "no_uuid_reuse": all(v["uuid_already_used_rows"] == 0 for v in targets.values()),
        "no_physical_tuple_conflicts": all(v["physical_tuple_conflict_rows"] == 0 for v in targets.values()),
        "cards_have_exact_bridge": all(v["card_exact_match_rows"] > 0 for v in targets.values()),
        "sets_have_exact_bridge": all(v["set_family_exact_match_rows"] > 0 for v in targets.values()),
    }
    report["gate_pass"] = all(report["gates"].values())
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True))
    return report


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input-dir", type=Path, required=True)
    ap.add_argument("--report", type=Path, required=True)
    args = ap.parse_args()
    run(args.input_dir, args.report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
