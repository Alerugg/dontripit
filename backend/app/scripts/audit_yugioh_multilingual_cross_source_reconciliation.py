#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
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
        raise ValueError(f"unsupported json top-level shape: {path}")


def find_file(root: Path, name: str) -> Path:
    hits = sorted((p for p in root.rglob(name) if p.is_file()), key=lambda p: (len(p.parts), str(p)))
    if not hits:
        raise FileNotFoundError(name)
    return hits[0]


def external_scalar(v: Any, keys: tuple[str, ...]) -> str:
    if isinstance(v, Mapping):
        for key in keys:
            if s(v.get(key)):
                return s(v.get(key))
        return ""
    if isinstance(v, (list, tuple)):
        for item in v:
            got = external_scalar(item, keys)
            if got:
                return got
        return ""
    return s(v)


def ygojson_ext(card: Mapping[str, Any]) -> tuple[str, str]:
    ext = mapping(card.get("externalIDs") or card.get("external_ids"))
    password = external_scalar(ext.get("ygoprodeck"), ("id", "cardID", "card_id", "value"))
    konami = external_scalar(ext.get("konami"), ("cid", "id", "cardID", "card_id", "dbID", "value"))
    if not konami:
        konami = external_scalar(card.get("dbID"), ("cid", "id", "dbID", "value"))
    if not konami:
        konami = external_scalar(ext.get("dbID") or ext.get("officialID"), ("cid", "id", "dbID", "value"))
    return password, konami


def logical_card_identity(password: str, konami: str, fallback: str) -> str:
    if konami:
        return f"konami:{konami}"
    if password:
        return f"password:{password}"
    return f"source:{fallback}"


def norm_rarity(value: Any) -> str:
    raw = sl(value)
    if not raw:
        return "unknown"
    token = re.sub(r"[^a-z0-9]+", "", raw)
    if token.endswith("rare") and token != "rare":
        token = token[:-4]
    aliases = {
        "collectors": "collector",
        "collectorsrare": "collector",
        "quartercenturysecret": "quartercenturysecret",
        "platinumsecret": "platinumsecret",
        "prismaticsecret": "prismaticsecret",
        "normalparallel": "normalparallel",
        "ultraparallel": "ultraparallel",
        "superparallel": "superparallel",
        "secretparallel": "secretparallel",
        "duelterminalnormalparallel": "duelterminalnormalparallel",
        "duelterminalrareparallel": "duelterminalrareparallel",
        "duelterminalsuperparallel": "duelterminalsuperparallel",
        "duelterminalultraparallel": "duelterminalultraparallel",
        "duelterminalsecretparallel": "duelterminalsecretparallel",
        "10000secret": "10000secret",
    }
    return aliases.get(token, token or "unknown")


def collector_code(prefix: str, suffix: str) -> str:
    p, q = s(prefix), s(suffix)
    if not q:
        return ""
    if not p or q.upper().startswith(p.upper()) or "-" in q:
        return q.upper()
    return f"{p}{q}".upper()


def collector_quality(code: str) -> str:
    c = s(code)
    if not c:
        return "missing"
    if any(ch in c for ch in ("?", "*")):
        return "placeholder"
    return "exact"


def family(code: str) -> str:
    c = s(code).upper()
    return c.split("-", 1)[0].strip() if "-" in c else c


def content_locales(content: Mapping[str, Any]) -> set[str]:
    raw = content.get("locales")
    if isinstance(raw, Mapping):
        return {sl(k) for k, v in raw.items() if v not in (False, None, "")}
    return {sl(x) for x in as_list(raw) if s(x)}


def printing_id(row: Mapping[str, Any]) -> str:
    return s(row.get("id") or row.get("uuid"))


def printing_card_id(row: Mapping[str, Any]) -> str:
    v = row.get("card")
    return s(v.get("id") or v.get("uuid")) if isinstance(v, Mapping) else s(v)


def iter_printings(content: Mapping[str, Any]) -> Iterator[Mapping[str, Any]]:
    rows = content.get("cards") or []
    if isinstance(rows, Mapping):
        rows = rows.values()
    for row in rows:
        if isinstance(row, Mapping):
            yield row


def build_yaml(path: Path) -> tuple[dict[str, Any], dict[str, dict[str, set[str]]]]:
    target_rows = {lang: [] for lang in TARGETS}
    aliases: dict[str, dict[str, set[str]]] = defaultdict(lambda: {"passwords": set(), "konami_ids": set()})
    total_cards = 0
    for card in iter_records(path):
        total_cards += 1
        password = s(card.get("password"))
        konami = s(card.get("konami_id"))
        ident = logical_card_identity(password, konami, f"yaml:{total_cards}")
        if password:
            aliases[ident]["passwords"].add(password)
        if konami:
            aliases[ident]["konami_ids"].add(konami)
        sets = mapping(card.get("sets"))
        for lang in TARGETS:
            rows = sets.get(lang) or []
            if isinstance(rows, Mapping):
                rows = [rows]
            for row in rows if isinstance(rows, list) else []:
                if not isinstance(row, Mapping):
                    continue
                code = s(row.get("set_number")).upper()
                rarities = row.get("rarities") or []
                if not isinstance(rarities, list):
                    rarities = [rarities]
                if not rarities:
                    rarities = [None]
                for rarity in rarities:
                    target_rows[lang].append({
                        "identity": ident,
                        "password": password,
                        "konami": konami,
                        "collector": code,
                        "family": family(code),
                        "rarity": norm_rarity(rarity),
                        "rarity_raw": s(rarity) or "unknown",
                        "quality": collector_quality(code),
                        "set_name": s(row.get("set_name")),
                    })

    reports: dict[str, Any] = {}
    canonical: dict[str, dict[tuple[str, str, str], dict[str, Any]]] = {}
    for lang, rows in target_rows.items():
        exact = [r for r in rows if r["quality"] == "exact"]
        ambiguous = [r for r in rows if r["quality"] != "exact"]
        slot_identities: dict[tuple[str, str], set[str]] = defaultdict(set)
        for r in exact:
            slot_identities[(r["collector"], r["rarity"])].add(r["identity"])
        true_conflict_slots = {k: v for k, v in slot_identities.items() if len(v) > 1}
        conflict_keys = set(true_conflict_slots)
        by_key: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
        for r in exact:
            if (r["collector"], r["rarity"]) in conflict_keys:
                continue
            by_key[(r["identity"], r["collector"], r["rarity"])].append(r)
        canonical[lang] = {k: v[0] for k, v in by_key.items()}
        alias_collapsed = sum(len(v) - 1 for v in by_key.values() if len(v) > 1)
        reports[lang] = {
            "raw_rows": len(rows),
            "ambiguous_rows": len(ambiguous),
            "true_conflict_slots": len(true_conflict_slots),
            "true_conflict_rows": sum(1 for r in exact if (r["collector"], r["rarity"]) in conflict_keys),
            "true_conflict_samples": [
                {"collector": k[0], "rarity": k[1], "identities": sorted(v)}
                for k, v in list(true_conflict_slots.items())[:30]
            ],
            "canonical_keys": len(canonical[lang]),
            "alias_or_duplicate_rows_collapsed": alias_collapsed,
        }
    alias_groups = {
        ident: {"passwords": sorted(v["passwords"]), "konami_ids": sorted(v["konami_ids"])}
        for ident, v in aliases.items() if len(v["passwords"]) > 1
    }
    return {"total_cards": total_cards, "targets": reports, "alias_groups": alias_groups, "canonical": canonical}, aliases


def build_ygojson(root: Path) -> dict[str, Any]:
    cards_path, sets_path = find_file(root, "cards.json"), find_file(root, "sets.json")
    cards: dict[str, dict[str, str]] = {}
    for card in iter_records(cards_path):
        card_uuid = s(card.get("id") or card.get("uuid"))
        if not card_uuid:
            continue
        password, konami = ygojson_ext(card)
        cards[card_uuid] = {
            "password": password,
            "konami": konami,
            "identity": logical_card_identity(password, konami, f"ygojson:{card_uuid}"),
        }

    rows_by_lang = {lang: [] for lang in TARGETS}
    logical_semantics: dict[str, tuple[str, str, str]] = {}
    logical_conflicts: set[str] = set()
    for set_obj in iter_records(sets_path):
        set_uuid = s(set_obj.get("id") or set_obj.get("uuid"))
        locales = mapping(set_obj.get("locales"))
        raw_contents = set_obj.get("contents") or []
        contents = list(raw_contents.values()) if isinstance(raw_contents, Mapping) else list(raw_contents)
        contents = [c for c in contents if isinstance(c, Mapping)]
        for lang, spec in TARGETS.items():
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
                    puid = printing_id(row)
                    cuid = printing_card_id(row)
                    rarity = norm_rarity(row.get("rarity"))
                    effective_lang = sl(row.get("language")) or loc_lang
                    if puid:
                        semantic = (cuid, set_uuid, rarity)
                        old = logical_semantics.get(puid)
                        if old is None:
                            logical_semantics[puid] = semantic
                        elif old != semantic:
                            logical_conflicts.add(puid)
                    if effective_lang != spec["language"]:
                        continue
                    card = cards.get(cuid) or {"password": "", "konami": "", "identity": f"source:{cuid}"}
                    collector = collector_code(prefix, s(row.get("suffix")))
                    rows_by_lang[lang].append({
                        "print_uuid": puid,
                        "card_uuid": cuid,
                        "identity": card["identity"],
                        "password": card["password"],
                        "konami": card["konami"],
                        "collector": collector,
                        "family": family(collector),
                        "rarity": rarity,
                        "quality": collector_quality(collector),
                        "set_uuid": set_uuid,
                    })

    reports, canonical = {}, {}
    for lang, rows in rows_by_lang.items():
        target_ids = {r["print_uuid"] for r in rows if r["print_uuid"]}
        q = set(logical_conflicts) & target_ids
        q.update(r["print_uuid"] for r in rows if r["print_uuid"] and r["quality"] != "exact")
        slots: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
        for r in rows:
            if r["quality"] == "exact":
                slots[(r["set_uuid"], r["collector"], r["rarity"])].append(r)
        source_conflict_slots = 0
        for group in slots.values():
            identities = {r["identity"] for r in group}
            if len(identities) > 1:
                source_conflict_slots += 1
                q.update(r["print_uuid"] for r in group if r["print_uuid"])
        cert = [r for r in rows if r["print_uuid"] and r["print_uuid"] not in q and r["quality"] == "exact"]
        by_key: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
        for r in cert:
            by_key[(r["identity"], r["collector"], r["rarity"])].append(r)
        canonical[lang] = by_key
        reports[lang] = {
            "raw_rows": len(rows), "quarantined_print_ids": len(q), "certifiable_rows": len(cert),
            "canonical_keys": len(by_key), "keys_with_multiple_physical_uuids": sum(1 for v in by_key.values() if len({r['print_uuid'] for r in v}) > 1),
            "physical_uuids": len({r["print_uuid"] for r in cert}), "source_conflict_slots": source_conflict_slots,
        }
    return {"targets": reports, "canonical": canonical, "cards": cards}


def run(ygojson_root: Path, yaml_cards: Path, report_path: Path, yaml_head: str, yaml_last_modified: str) -> dict[str, Any]:
    yaml_data, _aliases = build_yaml(yaml_cards)
    ygo_data = build_ygojson(ygojson_root)

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
        db_cards_by_password = defaultdict(set)
        for cid, password in conn.execute(text("SELECT id, yugoprodeck_id FROM cards WHERE game_id=:g AND yugoprodeck_id IS NOT NULL"), {"g": gid}):
            db_cards_by_password[s(password)].add(int(cid))
        db_sets = {s(code).upper(): int(sid) for sid, code in conn.execute(text("SELECT id, code FROM sets WHERE game_id=:g"), {"g": gid}) if s(code)}
        existing_tuples = defaultdict(set)
        for set_id, card_id, collector, language in conn.execute(text('''
            SELECT p.set_id, p.card_id, upper(p.collector_number), lower(p.language)
            FROM prints p JOIN cards c ON c.id=p.card_id WHERE c.game_id=:g
        '''), {"g": gid}):
            existing_tuples[(int(set_id), s(collector).upper(), sl(language))].add(int(card_id))
        tx.rollback()

    targets = {}
    for lang in TARGETS:
        ykeys = ygo_data["canonical"][lang]
        mkeys = yaml_data["canonical"][lang]
        ys, ms = set(ykeys), set(mkeys)
        overlap = ys & ms
        yonly, monly = ys - ms, ms - ys
        coarse_y = defaultdict(set)
        coarse_m = defaultdict(set)
        for ident, collector, rarity in ys:
            coarse_y[(ident, collector)].add(rarity)
        for ident, collector, rarity in ms:
            coarse_m[(ident, collector)].add(rarity)
        coarse_overlap = set(coarse_y) & set(coarse_m)
        rarity_only_mismatches = {
            k for k in coarse_overlap if not (coarse_y[k] & coarse_m[k])
        }

        alias_multi_db = {}
        alias_groups = yaml_data["alias_groups"]
        for ident, group in alias_groups.items():
            dbids = set()
            for password in group["passwords"]:
                dbids.update(db_cards_by_password.get(password, set()))
            if len(dbids) > 1:
                alias_multi_db[ident] = {"passwords": group["passwords"], "db_card_ids": sorted(dbids)}

        delta_safe = 0
        delta_alias_blocked = 0
        delta_card_missing = 0
        delta_set_missing = 0
        delta_tuple_conflict = 0
        delta_samples = []
        missing_families = Counter()
        for key in sorted(monly):
            row = mkeys[key]
            passwords = yaml_data["alias_groups"].get(row["identity"], {}).get("passwords") or ([row["password"]] if row["password"] else [])
            dbids = set()
            for password in passwords:
                dbids.update(db_cards_by_password.get(password, set()))
            if len(dbids) > 1:
                delta_alias_blocked += 1
                status = "alias_multiple_db_cards"
            elif len(dbids) == 0:
                delta_card_missing += 1
                status = "card_missing"
            else:
                db_card_id = next(iter(dbids))
                set_id = db_sets.get(row["family"])
                if set_id is None:
                    delta_set_missing += 1
                    missing_families[row["family"]] += 1
                    status = "set_family_missing"
                else:
                    occupied = existing_tuples.get((set_id, row["collector"], lang), set())
                    if occupied and occupied != {db_card_id}:
                        delta_tuple_conflict += 1
                        status = "existing_physical_tuple_conflict"
                    else:
                        delta_safe += 1
                        status = "safe_existing_card_and_set"
            if len(delta_samples) < 50 and status != "safe_existing_card_and_set":
                delta_samples.append({"identity": row["identity"], "collector": row["collector"], "rarity": row["rarity"], "status": status})

        ygo_uuids_in_overlap = sum(len({r["print_uuid"] for r in ykeys[k]}) for k in overlap)
        overlap_compressed = sum(1 for k in overlap if len({r["print_uuid"] for r in ykeys[k]}) > 1)
        targets[lang] = {
            "ygojson": ygo_data["targets"][lang],
            "yaml_yugi": yaml_data["targets"][lang],
            "fine_identity_overlap_keys": len(overlap),
            "fine_identity_ygojson_only_keys": len(yonly),
            "fine_identity_yaml_only_keys": len(monly),
            "coarse_card_collector_overlap_keys": len(coarse_overlap),
            "coarse_overlap_rarity_disjoint_keys": len(rarity_only_mismatches),
            "ygojson_physical_uuids_covered_by_fine_overlap": ygo_uuids_in_overlap,
            "overlap_keys_with_multiple_ygojson_physical_uuids": overlap_compressed,
            "yaml_alias_groups_multiple_passwords": len(alias_groups),
            "yaml_alias_groups_mapping_to_multiple_db_cards": len(alias_multi_db),
            "yaml_alias_group_db_samples": list(alias_multi_db.items())[:30],
            "yaml_only_delta": {
                "keys": len(monly),
                "safe_existing_card_and_set": delta_safe,
                "alias_multiple_db_cards": delta_alias_blocked,
                "card_missing": delta_card_missing,
                "set_family_missing": delta_set_missing,
                "existing_physical_tuple_conflict": delta_tuple_conflict,
                "missing_set_families": len(missing_families),
                "missing_set_family_top": missing_families.most_common(50),
                "blocked_samples": delta_samples,
            },
        }

    report = {
        "mode": "read_only_cross_source_reconciliation",
        "production_writes": 0,
        "database_transaction_read_only": True,
        "sources": {
            "ygojson": "v1 aggregate historical snapshot",
            "yaml_yugi": {"master_head": yaml_head, "http_last_modified": yaml_last_modified or None},
        },
        "identity_policy": {
            "logical_card": "prefer official Konami ID; password/YGOPRODeck ID becomes alias evidence",
            "fine_print": "logical card + exact localized collector number + normalized rarity",
            "historical_physical_identity": "retain all certifiable YGOJSON physical UUIDs; never collapse UUID/edition history into YAML rows",
            "current_delta": "YAML-only fine identities are candidates only after exact DB card/set/tuple gates and alias ambiguity checks",
            "images": "YGOJSON localized image linkage only where exact; YAML card-level image is not a localized physical-print image",
        },
        "targets": targets,
    }
    report["gates"] = {
        "read_only_enforced": True,
        "cross_source_overlap_present_es": targets["es"]["fine_identity_overlap_keys"] > 0,
        "cross_source_overlap_present_ja": targets["ja"]["fine_identity_overlap_keys"] > 0,
        "yaml_true_conflicts_small_and_quarantined_es": targets["es"]["yaml_yugi"]["true_conflict_slots"] <= 20,
        "yaml_true_conflicts_small_and_quarantined_ja": targets["ja"]["yaml_yugi"]["true_conflict_slots"] <= 20,
        "no_existing_tuple_conflict_in_yaml_delta_es": targets["es"]["yaml_only_delta"]["existing_physical_tuple_conflict"] == 0,
        "no_existing_tuple_conflict_in_yaml_delta_ja": targets["ja"]["yaml_only_delta"]["existing_physical_tuple_conflict"] == 0,
    }
    report["structural_pass"] = all(report["gates"].values())
    report["rollout_ready"] = False
    report["rollout_blockers"] = [
        "YAML-only missing set families must be modeled safely, especially OCG/JA",
        "multi-password Konami aliases mapping to multiple existing DB Card rows require ephemeral canonicalization or explicit quarantine",
        "ephemeral backfill and idempotency certification not yet executed",
        "localized images remain exact only where YGOJSON supplies physical-print linkage",
    ]
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return report


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ygojson-dir", type=Path, required=True)
    ap.add_argument("--yaml-cards", type=Path, required=True)
    ap.add_argument("--report", type=Path, required=True)
    ap.add_argument("--yaml-head", default="")
    ap.add_argument("--yaml-last-modified", default="")
    args = ap.parse_args()
    run(args.ygojson_dir, args.yaml_cards, args.report, args.yaml_head, args.yaml_last_modified)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
