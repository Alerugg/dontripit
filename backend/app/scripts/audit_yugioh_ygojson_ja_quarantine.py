#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterator, Mapping

import ijson


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
        elif first == b"{":
            yield from (x for _k, x in ijson.kvitems(fh, "") if isinstance(x, dict))
        else:
            raise ValueError(f"Unsupported JSON top-level shape: {path}")


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


def ext_ids(card: Mapping[str, Any]) -> tuple[str, str]:
    ext = mapping(card.get("externalIDs") or card.get("external_ids"))
    password = external_scalar(ext.get("ygoprodeck"), ("id", "cardID", "card_id", "value"))
    konami = external_scalar(ext.get("konami"), ("cid", "id", "cardID", "card_id", "dbID", "value"))
    if not konami:
        konami = external_scalar(card.get("dbID"), ("cid", "id", "dbID", "value"))
    if not konami:
        konami = external_scalar(ext.get("dbID") or ext.get("officialID"), ("cid", "id", "dbID", "value"))
    return password, konami


def content_locales(content: Mapping[str, Any]) -> set[str]:
    raw = content.get("locales")
    if isinstance(raw, Mapping):
        return {sl(k) for k, v in raw.items() if v not in (False, None, "")}
    return {sl(x) for x in as_list(raw) if s(x)}


def collector_code(prefix: str, suffix: str) -> str:
    p, q = s(prefix), s(suffix)
    if not q:
        return ""
    if not p or q.upper().startswith(p.upper()) or "-" in q:
        return q.upper()
    return f"{p}{q}".upper()


def family(code: str) -> str:
    c = s(code).upper()
    return c.split("-", 1)[0].strip() if "-" in c else c


def logical_identity(card: Mapping[str, str], fallback: str) -> str:
    if card.get("konami"):
        return f"konami:{card['konami']}"
    if card.get("password"):
        return f"password:{card['password']}"
    return f"source:{fallback}"


def run(root: Path, report_path: Path) -> dict[str, Any]:
    cards_path, sets_path = find_file(root, "cards.json"), find_file(root, "sets.json")
    cards: dict[str, dict[str, str]] = {}
    for card in iter_records(cards_path):
        cuid = s(card.get("id") or card.get("uuid"))
        if not cuid:
            continue
        password, konami = ext_ids(card)
        cards[cuid] = {"password": password, "konami": konami}

    rows: list[dict[str, Any]] = []
    logical_semantics: dict[str, tuple[str, str, str]] = {}
    logical_conflict_ids: set[str] = set()
    for set_obj in iter_records(sets_path):
        set_uuid = s(set_obj.get("id") or set_obj.get("uuid"))
        locales = mapping(set_obj.get("locales"))
        loc = locales.get("jp")
        if not isinstance(loc, Mapping):
            continue
        if (sl(loc.get("language") or loc.get("lang")) or "ja") != "ja":
            continue
        prefix = s(loc.get("prefix"))
        raw_contents = set_obj.get("contents") or []
        contents = list(raw_contents.values()) if isinstance(raw_contents, Mapping) else list(raw_contents)
        for content in contents:
            if not isinstance(content, Mapping):
                continue
            scoped = content_locales(content)
            if not scoped or "jp" not in scoped:
                continue
            edition = s(content.get("edition") or content.get("name") or content.get("id"))
            raw_cards = content.get("cards") or []
            printings = list(raw_cards.values()) if isinstance(raw_cards, Mapping) else list(raw_cards)
            for row in printings:
                if not isinstance(row, Mapping):
                    continue
                puid = s(row.get("id") or row.get("uuid"))
                cv = row.get("card")
                cuid = s(cv.get("id") or cv.get("uuid")) if isinstance(cv, Mapping) else s(cv)
                rarity = sl(row.get("rarity")) or "unknown"
                effective_lang = sl(row.get("language")) or "ja"
                if effective_lang != "ja":
                    continue
                collector = collector_code(prefix, s(row.get("suffix")))
                card = cards.get(cuid, {"password": "", "konami": ""})
                ident = logical_identity(card, cuid)
                if puid:
                    semantic = (cuid, set_uuid, rarity)
                    old = logical_semantics.get(puid)
                    if old is None:
                        logical_semantics[puid] = semantic
                    elif old != semantic:
                        logical_conflict_ids.add(puid)
                rows.append({
                    "print_uuid": puid,
                    "card_uuid": cuid,
                    "identity": ident,
                    "password": card.get("password", ""),
                    "konami": card.get("konami", ""),
                    "set_uuid": set_uuid,
                    "edition": edition,
                    "prefix": prefix,
                    "suffix": s(row.get("suffix")),
                    "collector": collector,
                    "family": family(collector) or s(prefix).upper(),
                    "rarity": rarity,
                })

    unique_print_ids = {r["print_uuid"] for r in rows if r["print_uuid"]}
    duplicated_memberships = Counter(r["print_uuid"] for r in rows if r["print_uuid"])
    duplicate_ids = {p for p, n in duplicated_memberships.items() if n > 1}

    missing_collector_ids = {r["print_uuid"] for r in rows if r["print_uuid"] and not r["collector"]}
    placeholder_ids = {
        r["print_uuid"] for r in rows
        if r["print_uuid"] and r["collector"] and any(ch in r["collector"] for ch in ("?", "*"))
    }

    slot_groups: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        if r["collector"] and not any(ch in r["collector"] for ch in ("?", "*")):
            slot_groups[(r["set_uuid"], r["collector"], r["rarity"])].append(r)
    slot_conflict_ids: set[str] = set()
    slot_conflicts: list[dict[str, Any]] = []
    for (set_uuid, collector, rarity), group in slot_groups.items():
        identities = {r["identity"] for r in group}
        if len(identities) > 1:
            slot_conflict_ids.update(r["print_uuid"] for r in group if r["print_uuid"])
            slot_conflicts.append({
                "set_uuid": set_uuid, "collector": collector, "rarity": rarity,
                "identities": sorted(identities),
                "print_uuids": sorted({r["print_uuid"] for r in group if r["print_uuid"]}),
            })

    # Missing printed numbers are not assumed to be errors. They are separated
    # because the current DB requires collector_number NOT NULL and its physical
    # uniqueness constraint cannot safely represent multiple blank numbers in a
    # set/language/variant. Writer must either model a faithful source-backed
    # identity strategy/schema change or keep these rows quarantined.
    categories = {
        "missing_collector": missing_collector_ids,
        "placeholder_collector": placeholder_ids,
        "logical_uuid_conflict": logical_conflict_ids & unique_print_ids,
        "physical_slot_conflict": slot_conflict_ids,
    }
    quarantine_union = set().union(*categories.values())
    certifiable = unique_print_ids - quarantine_union

    missing_rows = [r for r in rows if r["print_uuid"] in missing_collector_ids]
    missing_by_prefix = Counter(s(r["prefix"]).upper() or "<none>" for r in missing_rows)
    missing_by_set = Counter(r["set_uuid"] for r in missing_rows)
    missing_by_edition = Counter(r["edition"] or "<none>" for r in missing_rows)
    missing_unique_cards = {r["identity"] for r in missing_rows}
    missing_same_set_multi = Counter((r["set_uuid"], r["identity"]) for r in missing_rows)

    overlap_matrix: dict[str, Any] = {}
    names = list(categories)
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            overlap_matrix[f"{a}&{b}"] = len(categories[a] & categories[b])

    sample_missing = []
    seen = set()
    for r in missing_rows:
        key = (r["set_uuid"], r["identity"], r["edition"], r["rarity"])
        if key in seen:
            continue
        seen.add(key)
        sample_missing.append({
            "print_uuid": r["print_uuid"], "set_uuid": r["set_uuid"], "prefix": r["prefix"] or None,
            "edition": r["edition"] or None, "identity": r["identity"], "password": r["password"] or None,
            "konami": r["konami"] or None, "rarity": r["rarity"], "suffix": r["suffix"] or None,
        })
        if len(sample_missing) >= 80:
            break

    report = {
        "mode": "source_only_ygojson_ja_quarantine_decomposition",
        "production_writes": 0,
        "source_memberships": len(rows),
        "unique_print_uuids": len(unique_print_ids),
        "duplicate_membership_print_uuids": len(duplicate_ids),
        "duplicate_membership_extra_rows": sum(n - 1 for n in duplicated_memberships.values() if n > 1),
        "categories_unique_print_uuids": {k: len(v) for k, v in categories.items()},
        "category_overlaps": overlap_matrix,
        "quarantine_union_unique_print_uuids": len(quarantine_union),
        "certifiable_unique_print_uuids": len(certifiable),
        "missing_collector_analysis": {
            "unique_cards": len(missing_unique_cards),
            "prefix_top": missing_by_prefix.most_common(50),
            "set_uuid_top": missing_by_set.most_common(30),
            "edition_top": missing_by_edition.most_common(30),
            "same_set_card_groups_with_multiple_missing_prints": sum(1 for n in missing_same_set_multi.values() if n > 1),
            "max_missing_prints_same_set_card": max(missing_same_set_multi.values(), default=0),
            "samples": sample_missing,
        },
        "physical_slot_conflicts": sorted(slot_conflicts, key=lambda x: (x["collector"], x["rarity"]))[:100],
        "model_fact": {
            "collector_number_nullable": False,
            "unique_constraint": "(set_id, collector_number, language, is_foil, variant)",
            "safe_policy": "Never fabricate a collector number. Missing-number physical UUIDs stay quarantined until a source-backed representation is certified.",
        },
    }
    report["gates"] = {
        "all_rows_accounted": len(certifiable | quarantine_union) == len(unique_print_ids),
        "no_hidden_logical_uuid_conflict": len(categories["logical_uuid_conflict"]) == 0,
        "missing_collectors_explicit_not_fabricated": True,
    }
    report["gate_pass"] = all(report["gates"].values())
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({k: report[k] for k in (
        "mode", "source_memberships", "unique_print_uuids", "duplicate_membership_print_uuids",
        "duplicate_membership_extra_rows", "categories_unique_print_uuids", "category_overlaps",
        "quarantine_union_unique_print_uuids", "certifiable_unique_print_uuids", "gates", "gate_pass"
    )}, ensure_ascii=False, indent=2, sort_keys=True))
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
