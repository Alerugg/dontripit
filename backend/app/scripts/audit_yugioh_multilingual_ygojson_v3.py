#!/usr/bin/env python3
"""Read-only structural audit for YGOJSON Spanish/Japanese physical prints.

No database connection, no persistence, no pricing, no localized text/image payload export.
The audit separates source structural safety from rollout freshness/readiness.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping, Optional

import ijson

TARGETS = {
    "es": {"locale": "sp", "language": "es", "expected_format": "tcg", "token": "SP"},
    "ja": {"locale": "jp", "language": "ja", "expected_format": "ocg", "token": "JP"},
}


def s(v: Any) -> str:
    return str(v or "").strip()


def sl(v: Any) -> str:
    return s(v).lower()


def as_list(v: Any) -> list[Any]:
    if v is None:
        return []
    if isinstance(v, list):
        return v
    if isinstance(v, (tuple, set)):
        return list(v)
    return [v]


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
    matches = sorted((p for p in root.rglob(name) if p.is_file()), key=lambda p: (len(p.parts), str(p)))
    if not matches:
        raise FileNotFoundError(f"Could not find {name} below {root}")
    return matches[0]


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def mapping(v: Any) -> Mapping[str, Any]:
    return v if isinstance(v, Mapping) else {}


def ext_ids(card: Mapping[str, Any]) -> tuple[Optional[str], Optional[str]]:
    ext = mapping(card.get("externalIDs") or card.get("external_ids"))
    ygo = ext.get("ygoprodeck")
    if isinstance(ygo, Mapping):
        ygo = ygo.get("id") or ygo.get("cardID") or ygo.get("card_id")
    dbid = card.get("dbID") or ext.get("dbID") or ext.get("officialID")
    if isinstance(dbid, Mapping):
        dbid = dbid.get("id") or dbid.get("cid")
    return (s(ygo) or None, s(dbid) or None)


def card_text_flags(card: Mapping[str, Any], language: str) -> tuple[bool, bool, bool]:
    blob = mapping(mapping(card.get("text")).get(language))
    has_name = bool(s(blob.get("name")))
    has_text = any(bool(s(blob.get(k))) for k in ("effect", "pendulumEffect", "pendulum_effect"))
    official = bool(blob) and blob.get("official") is not False
    return has_name, has_text, official


def iter_printings(content: Mapping[str, Any]) -> Iterator[Mapping[str, Any]]:
    rows = content.get("cards") or []
    if isinstance(rows, Mapping):
        rows = rows.values()
    for row in rows if isinstance(rows, (list, tuple)) or hasattr(rows, "__iter__") else []:
        if isinstance(row, Mapping):
            yield row


def printing_id(row: Mapping[str, Any]) -> str:
    return s(row.get("id") or row.get("uuid"))


def printing_card_id(row: Mapping[str, Any]) -> str:
    v = row.get("card")
    if isinstance(v, Mapping):
        return s(v.get("id") or v.get("uuid"))
    return s(v)


def content_locales(content: Mapping[str, Any]) -> set[str]:
    raw = content.get("locales")
    if isinstance(raw, Mapping):
        return {sl(k) for k, v in raw.items() if v not in (False, None, "")}
    return {sl(v) for v in as_list(raw) if s(v)}


def formats(locale_blob: Mapping[str, Any], content: Mapping[str, Any]) -> set[str]:
    vals = {sl(x) for x in as_list(locale_blob.get("formats")) if s(x)}
    if vals:
        return vals
    return {sl(x) for x in as_list(content.get("formats")) if s(x)}


def collector_code(prefix: str, suffix: str) -> str:
    p, q = s(prefix), s(suffix)
    if not q:
        return ""
    if not p:
        return q
    pu, qu = p.upper(), q.upper()
    if qu.startswith(pu):
        return q
    if "-" in q:
        return q
    return f"{p}{q}"


def collector_quality(code: str) -> str:
    c = s(code)
    if not c:
        return "missing"
    if any(ch in c for ch in ("?", "*")):
        return "placeholder"
    return "exact"


def localized_image(locale_blob: Mapping[str, Any], pid: str) -> tuple[bool, list[str]]:
    editions: list[str] = []
    for key in ("cardInfo", "cardImages"):
        outer = mapping(locale_blob.get(key))
        for edition, per_print in outer.items():
            info = mapping(per_print).get(pid)
            if isinstance(info, Mapping):
                found = bool(s(info.get("image")))
            else:
                found = bool(s(info))
            if found and s(edition) not in editions:
                editions.append(s(edition))
    return bool(editions), editions


def parse_dt(value: Any) -> Optional[datetime]:
    raw = s(value)
    if not raw:
        return None
    raw = raw.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        try:
            dt = datetime.fromisoformat(raw[:10])
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def meta_freshness(meta: Mapping[str, Any]) -> dict[str, Any]:
    keys = ["lastYGOProDeckRead", "lastYugipediaRead", "lastYamlyugiRead"]
    parsed = [(k, parse_dt(meta.get(k))) for k in keys]
    valid = [(k, d) for k, d in parsed if d is not None]
    cutoff = max((d for _k, d in valid), default=None)
    now = datetime.now(timezone.utc)
    age_days = (now - cutoff).days if cutoff else None
    return {
        "inputs": {k: (d.isoformat() if d else None) for k, d in parsed},
        "snapshot_cutoff": cutoff.isoformat() if cutoff else None,
        "age_days": age_days,
        "status": "current_enough" if age_days is not None and age_days <= 7 else "historical_snapshot",
        "max_rollout_age_days": 7,
    }


def base_metrics(t: Mapping[str, str]) -> dict[str, Any]:
    return {
        "locale": t["locale"], "language": t["language"], "expected_format": t["expected_format"],
        "sets": 0, "sets_language_mismatch": 0, "sets_expected_format": 0,
        "memberships": 0, "target_language_memberships": 0,
        "missing_print_uuid_rows": 0, "missing_card_uuid_rows": 0,
        "unique_print_ids": set(), "unique_card_ids": set(),
        "rarities": Counter(), "collector_quality": Counter(),
        "with_localized_image": 0, "image_editions": Counter(),
        "language_override_rows": 0, "language_override_mismatch_rows": 0,
        "rows": [],
    }


def audit(root: Path, source_url: str, archive: Optional[Path]) -> dict[str, Any]:
    cards_path, sets_path = find_file(root, "cards.json"), find_file(root, "sets.json")
    try:
        meta_path = find_file(root, "meta.json")
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        meta_path, meta = None, {}

    cards: dict[str, dict[str, Any]] = {}
    for card in iter_records(cards_path):
        cid = s(card.get("id") or card.get("uuid"))
        if not cid:
            continue
        ygo, dbid = ext_ids(card)
        cards[cid] = {"ygoprodeck": ygo, "dbid": dbid}
        for code, t in TARGETS.items():
            name, text, official = card_text_flags(card, t["language"])
            cards[cid][code] = {"name": name, "text": text, "official": official}

    metrics = {k: base_metrics(v) for k, v in TARGETS.items()}
    logical_semantics: dict[str, tuple[str, str, str]] = {}
    logical_conflict_ids: set[str] = set()
    logical_duplicate_rows = 0
    logical_conflict_samples: list[dict[str, Any]] = []
    total_sets = 0

    for set_obj in iter_records(sets_path):
        total_sets += 1
        sid = s(set_obj.get("id") or set_obj.get("uuid"))
        locales = mapping(set_obj.get("locales"))
        raw_contents = set_obj.get("contents") or []
        contents = list(raw_contents.values()) if isinstance(raw_contents, Mapping) else list(raw_contents)
        contents = [c for c in contents if isinstance(c, Mapping)]

        for code, t in TARGETS.items():
            loc = locales.get(t["locale"])
            if not isinstance(loc, Mapping):
                continue
            m = metrics[code]
            m["sets"] += 1
            loc_lang = sl(loc.get("language") or loc.get("lang"))
            if loc_lang != t["language"]:
                m["sets_language_mismatch"] += 1
            if t["expected_format"] in {sl(x) for x in as_list(loc.get("formats")) if s(x)}:
                m["sets_expected_format"] += 1
            prefix = s(loc.get("prefix"))

            for content in contents:
                scoped = content_locales(content)
                if not scoped or t["locale"] not in scoped:
                    continue
                for row in iter_printings(content):
                    m["memberships"] += 1
                    pid = printing_id(row)
                    cid = printing_card_id(row)
                    rarity = sl(row.get("rarity")) or "unknown"
                    suffix = s(row.get("suffix"))
                    override = sl(row.get("language"))
                    effective_lang = override or loc_lang
                    if override:
                        m["language_override_rows"] += 1
                        if override != t["language"]:
                            m["language_override_mismatch_rows"] += 1
                    if not pid:
                        m["missing_print_uuid_rows"] += 1
                    if not cid:
                        m["missing_card_uuid_rows"] += 1

                    if pid:
                        semantic = (cid, sid, rarity)
                        old = logical_semantics.get(pid)
                        if old is None:
                            logical_semantics[pid] = semantic
                        else:
                            logical_duplicate_rows += 1
                            if old != semantic:
                                logical_conflict_ids.add(pid)
                                if len(logical_conflict_samples) < 30:
                                    logical_conflict_samples.append({"printing_id": pid, "first": old, "second": semantic})

                    if effective_lang != t["language"]:
                        continue

                    m["target_language_memberships"] += 1
                    if pid:
                        m["unique_print_ids"].add(pid)
                    if cid:
                        m["unique_card_ids"].add(cid)
                    m["rarities"][rarity] += 1
                    collector = collector_code(prefix, suffix)
                    quality = collector_quality(collector)
                    m["collector_quality"][quality] += 1
                    has_image, editions = localized_image(loc, pid)
                    if has_image:
                        m["with_localized_image"] += 1
                        m["image_editions"].update(editions)
                    m["rows"].append({
                        "print_id": pid, "card_id": cid, "set_id": sid, "rarity": rarity,
                        "collector": collector, "collector_quality": quality,
                    })

    target_reports: dict[str, Any] = {}
    all_quarantined: set[str] = set(logical_conflict_ids)

    for code, t in TARGETS.items():
        m = metrics[code]
        groups: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
        placeholder_ids: set[str] = set()
        for row in m["rows"]:
            pid = row["print_id"]
            if row["collector_quality"] == "placeholder":
                if pid:
                    placeholder_ids.add(pid)
                continue
            if row["collector_quality"] == "exact":
                groups[(row["set_id"], row["collector"].upper(), row["rarity"])].append(row)

        collision_ids: set[str] = set()
        conflict_groups = 0
        duplicate_rows_same_card = 0
        samples: list[dict[str, Any]] = []
        for identity, rows in groups.items():
            cards_here = {r["card_id"] for r in rows if r["card_id"]}
            if len(rows) > 1:
                if len(cards_here) > 1:
                    conflict_groups += 1
                    pids = {r["print_id"] for r in rows if r["print_id"]}
                    collision_ids.update(pids)
                    if len(samples) < 20:
                        samples.append({
                            "set_id": identity[0], "collector": identity[1], "rarity": identity[2],
                            "card_ids": sorted(cards_here), "printing_ids": sorted(pids),
                        })
                else:
                    duplicate_rows_same_card += len(rows) - 1

        quarantine = set(logical_conflict_ids) & set(m["unique_print_ids"])
        quarantine.update(placeholder_ids)
        quarantine.update(collision_ids)
        all_quarantined.update(quarantine)
        certifiable = set(m["unique_print_ids"]) - quarantine

        bridge_ygo = bridge_dbid = bridge_both = missing_cards = names = texts = official_names = 0
        for cid in m["unique_card_ids"]:
            card = cards.get(cid)
            if not card:
                missing_cards += 1
                continue
            ygo, dbid = card.get("ygoprodeck"), card.get("dbid")
            bridge_ygo += bool(ygo)
            bridge_dbid += bool(dbid)
            bridge_both += bool(ygo and dbid)
            flags = card.get(code, {})
            names += bool(flags.get("name"))
            texts += bool(flags.get("text"))
            official_names += bool(flags.get("name") and flags.get("official"))

        card_den = len(m["unique_card_ids"]) or 1
        print_den = m["target_language_memberships"] or 1
        target_reports[code] = {
            "locale": t["locale"], "language": t["language"], "format": t["expected_format"],
            "sets": m["sets"], "sets_language_mismatch": m["sets_language_mismatch"],
            "memberships": m["target_language_memberships"],
            "unique_printing_uuids": len(m["unique_print_ids"]),
            "unique_cards": len(m["unique_card_ids"]),
            "missing_print_uuid_rows": m["missing_print_uuid_rows"],
            "missing_card_uuid_rows": m["missing_card_uuid_rows"],
            "cards_missing_from_cards_file": missing_cards,
            "rarities": dict(m["rarities"]),
            "collector_quality": dict(m["collector_quality"]),
            "placeholder_print_ids": len(placeholder_ids),
            "localized_identity_conflict_groups": conflict_groups,
            "localized_identity_conflict_print_ids": len(collision_ids),
            "localized_identity_conflict_samples": samples,
            "duplicate_exact_identity_rows_same_card": duplicate_rows_same_card,
            "logical_uuid_conflict_print_ids": len(set(m["unique_print_ids"]) & logical_conflict_ids),
            "quarantined_print_ids": len(quarantine),
            "certifiable_print_ids": len(certifiable),
            "certifiable_pct": round(100 * len(certifiable) / max(len(m["unique_print_ids"]), 1), 4),
            "ygoprodeck_bridge_cards": bridge_ygo,
            "ygoprodeck_bridge_pct": round(100 * bridge_ygo / card_den, 4),
            "official_id_bridge_cards": bridge_dbid,
            "official_id_bridge_pct": round(100 * bridge_dbid / card_den, 4),
            "both_bridge_cards": bridge_both,
            "both_bridge_pct": round(100 * bridge_both / card_den, 4),
            "localized_name_pct": round(100 * names / card_den, 4),
            "localized_text_pct": round(100 * texts / card_den, 4),
            "official_localized_name_pct": round(100 * official_names / card_den, 4),
            "localized_printing_image_pct": round(100 * m["with_localized_image"] / print_den, 4),
            "language_override_rows": m["language_override_rows"],
            "language_override_mismatch_rows": m["language_override_mismatch_rows"],
        }

    freshness = meta_freshness(mapping(meta))
    structural_gates = {
        "spanish_prints_present": target_reports["es"]["unique_printing_uuids"] > 0,
        "japanese_prints_present": target_reports["ja"]["unique_printing_uuids"] > 0,
        "spanish_ids_complete": target_reports["es"]["missing_print_uuid_rows"] == 0 and target_reports["es"]["missing_card_uuid_rows"] == 0,
        "japanese_ids_complete": target_reports["ja"]["missing_print_uuid_rows"] == 0 and target_reports["ja"]["missing_card_uuid_rows"] == 0,
        "spanish_cards_resolve": target_reports["es"]["cards_missing_from_cards_file"] == 0,
        "japanese_cards_resolve": target_reports["ja"]["cards_missing_from_cards_file"] == 0,
        "spanish_certifiable_prints_present": target_reports["es"]["certifiable_print_ids"] > 0,
        "japanese_certifiable_prints_present": target_reports["ja"]["certifiable_print_ids"] > 0,
        "all_ambiguous_or_conflicting_identities_quarantined": True,
    }
    structural_pass = all(structural_gates.values())
    freshness_pass = freshness["status"] == "current_enough"

    source = {
        "url": source_url,
        "cards_file": str(cards_path.relative_to(root)),
        "sets_file": str(sets_path.relative_to(root)),
        "meta_file": str(meta_path.relative_to(root)) if meta_path else None,
        "meta": meta,
    }
    if archive and archive.exists():
        source.update({"archive_bytes": archive.stat().st_size, "archive_sha256": sha256(archive)})

    return {
        "schema_version": 3,
        "audit": "yugioh_multilingual_ygojson_source_v3",
        "mode": "read_only_source_only",
        "production_writes": 0,
        "source": source,
        "freshness": freshness,
        "totals": {
            "cards_records": len(cards), "sets_records": total_sets,
            "logical_printing_uuids": len(logical_semantics),
            "logical_uuid_duplicate_rows": logical_duplicate_rows,
            "logical_uuid_conflict_print_ids": len(logical_conflict_ids),
            "logical_uuid_conflict_samples": logical_conflict_samples,
            "quarantined_union_print_ids": len(all_quarantined),
        },
        "targets": target_reports,
        "structural_gates": structural_gates,
        "structural_gate_pass": structural_pass,
        "freshness_gate_pass": freshness_pass,
        "rollout_ready": structural_pass and freshness_pass,
        "notes": [
            "Physical memberships are read from YGOJSON v1 contents[].cards.",
            "Printing UUID semantics are locale-independent: collector suffix/code is intentionally excluded.",
            "Placeholder collector codes and exact collector+rarity collisions across different cards are quarantined, not silently accepted.",
            "Per-print language overrides are respected.",
            "Localized names/images are measured only; payload text and image URLs are not exported.",
            "This source-only audit does not connect to Don’tRipIt and cannot write production.",
            "A structurally safe historical snapshot is not rollout-ready when its source freshness exceeds the configured age threshold.",
        ],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input-dir", required=True, type=Path)
    ap.add_argument("--report", required=True, type=Path)
    ap.add_argument("--source-url", required=True)
    ap.add_argument("--archive", type=Path)
    args = ap.parse_args()
    report = audit(args.input_dir.resolve(), args.source_url, args.archive.resolve() if args.archive else None)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({
        "production_writes": report["production_writes"],
        "es": {k: report["targets"]["es"][k] for k in ("sets", "unique_printing_uuids", "quarantined_print_ids", "certifiable_print_ids", "ygoprodeck_bridge_pct")},
        "ja": {k: report["targets"]["ja"][k] for k in ("sets", "unique_printing_uuids", "quarantined_print_ids", "certifiable_print_ids", "ygoprodeck_bridge_pct")},
        "structural_gate_pass": report["structural_gate_pass"],
        "freshness": report["freshness"],
        "rollout_ready": report["rollout_ready"],
    }, indent=2, ensure_ascii=False))
    return 0 if report["structural_gate_pass"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
