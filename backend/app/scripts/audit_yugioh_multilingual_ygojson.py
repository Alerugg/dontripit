#!/usr/bin/env python3
"""Read-only source audit for Yu-Gi-Oh! multilingual physical print data.

This script intentionally does not connect to Don’tRipIt's database and does not
persist copyrighted localized text or images. It inspects a locally extracted
YGOJSON aggregate release and emits only structural/count metrics plus a small
sample of physical identifiers.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any, Iterator, Mapping, MutableMapping, Optional, Tuple

import ijson

TARGETS = {
    "es": {"locale": "sp", "language": "es", "expected_format": "tcg", "regional_token": "SP"},
    "ja": {"locale": "jp", "language": "ja", "expected_format": "ocg", "regional_token": "JP"},
}


def _norm(value: Any) -> str:
    return str(value or "").strip()


def _norm_lower(value: Any) -> str:
    return _norm(value).lower()


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, (tuple, set)):
        return list(value)
    return [value]


def _iter_records(path: Path) -> Iterator[dict[str, Any]]:
    """Stream a top-level JSON array or object values without loading it all."""
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
            for item in ijson.items(fh, "item"):
                if isinstance(item, dict):
                    yield item
            return
        if first == b"{":
            for _key, item in ijson.kvitems(fh, ""):
                if isinstance(item, dict):
                    yield item
            return
        raise ValueError(f"Unsupported JSON top-level shape in {path}")


def _find_file(root: Path, filename: str) -> Path:
    matches = sorted(p for p in root.rglob(filename) if p.is_file())
    if not matches:
        raise FileNotFoundError(f"Could not find {filename} below {root}")
    matches.sort(key=lambda p: (len(p.relative_to(root).parts), str(p)))
    return matches[0]


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _json_if_present(path: Optional[Path]) -> Any:
    if path is None or not path.exists():
        return None
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _extract_ygoprodeck_id(card: Mapping[str, Any]) -> Optional[str]:
    external = card.get("externalIDs") or card.get("external_ids") or {}
    if not isinstance(external, Mapping):
        return None
    ygo = external.get("ygoprodeck")
    if isinstance(ygo, Mapping):
        for key in ("id", "cardID", "card_id"):
            if ygo.get(key) not in (None, ""):
                return _norm(ygo.get(key))
    elif ygo not in (None, ""):
        return _norm(ygo)
    for key in ("ygoprodeckID", "ygoprodeck_id"):
        if external.get(key) not in (None, ""):
            return _norm(external.get(key))
    return None


def _extract_official_id(card: Mapping[str, Any]) -> Optional[str]:
    for key in ("dbID", "officialID", "official_id", "konamiID", "konami_id"):
        if card.get(key) not in (None, ""):
            return _norm(card.get(key))
    external = card.get("externalIDs") or {}
    if isinstance(external, Mapping):
        for key in ("dbID", "officialID", "konami"):
            value = external.get(key)
            if isinstance(value, Mapping):
                value = value.get("id") or value.get("cid")
            if value not in (None, ""):
                return _norm(value)
    return None


def _card_text_blob(card: Mapping[str, Any], language: str) -> Mapping[str, Any]:
    text = card.get("text") or {}
    if not isinstance(text, Mapping):
        return {}
    blob = text.get(language)
    return blob if isinstance(blob, Mapping) else {}


def _formats(locale_blob: Mapping[str, Any], content: Mapping[str, Any]) -> tuple[set[str], str]:
    local = {_norm_lower(x) for x in _as_list(locale_blob.get("formats")) if _norm(x)}
    if local:
        return local, "locale"
    inherited = {_norm_lower(x) for x in _as_list(content.get("formats")) if _norm(x)}
    return inherited, "content_fallback" if inherited else "missing"


def _content_locales(content: Mapping[str, Any]) -> tuple[set[str], bool]:
    raw = content.get("locales")
    if raw in (None, "", [], {}):
        return set(), False
    if isinstance(raw, Mapping):
        vals = {str(k).lower() for k, v in raw.items() if v not in (False, None, "")}
    else:
        vals = {_norm_lower(v) for v in _as_list(raw) if _norm(v)}
    return vals, True


def _iter_printings(content: Mapping[str, Any]) -> Iterator[Mapping[str, Any]]:
    """YGOJSON v1 stores physical printing rows in contents[].cards."""
    raw = content.get("cards") or []
    if isinstance(raw, Mapping):
        for value in raw.values():
            if isinstance(value, Mapping):
                yield value
    elif isinstance(raw, list):
        for value in raw:
            if isinstance(value, Mapping):
                yield value


def _printing_card_id(printing: Mapping[str, Any]) -> str:
    card = printing.get("card")
    if isinstance(card, Mapping):
        return _norm(card.get("id") or card.get("uuid"))
    return _norm(card)


def _printing_id(printing: Mapping[str, Any]) -> str:
    return _norm(printing.get("id") or printing.get("uuid"))


def _set_id(set_obj: Mapping[str, Any]) -> str:
    return _norm(set_obj.get("id") or set_obj.get("uuid"))


def _set_locales(set_obj: Mapping[str, Any]) -> Mapping[str, Any]:
    locales = set_obj.get("locales") or {}
    return locales if isinstance(locales, Mapping) else {}


def _locale_language(locale_blob: Mapping[str, Any]) -> str:
    return _norm_lower(locale_blob.get("language") or locale_blob.get("lang"))


def _locale_prefix(locale_blob: Mapping[str, Any]) -> str:
    return _norm(locale_blob.get("prefix"))


def _locale_date(locale_blob: Mapping[str, Any]) -> str:
    return _norm(locale_blob.get("date") or locale_blob.get("releaseDate") or locale_blob.get("release_date"))


def _locale_editions(locale_blob: Mapping[str, Any]) -> list[str]:
    editions = []
    for item in _as_list(locale_blob.get("editions")):
        if isinstance(item, Mapping):
            value = item.get("edition") or item.get("name") or item.get("id")
        else:
            value = item
        if _norm(value):
            editions.append(_norm(value))
    return editions


def _printing_image_info(locale_blob: Mapping[str, Any], printing_id: str) -> tuple[bool, list[str]]:
    """Return localized printing-image availability and matching edition keys."""
    if not printing_id:
        return False, []
    matching_editions: list[str] = []

    card_info = locale_blob.get("cardInfo")
    if isinstance(card_info, Mapping):
        for edition, per_printing in card_info.items():
            if not isinstance(per_printing, Mapping):
                continue
            info = per_printing.get(printing_id)
            if isinstance(info, Mapping) and _norm(info.get("image")):
                matching_editions.append(_norm(edition))

    card_images = locale_blob.get("cardImages")
    if isinstance(card_images, Mapping):
        for edition, per_printing in card_images.items():
            if not isinstance(per_printing, Mapping):
                continue
            image = per_printing.get(printing_id)
            if _norm(image) and _norm(edition) not in matching_editions:
                matching_editions.append(_norm(edition))

    return bool(matching_editions), matching_editions


def _empty_target(target: Mapping[str, str]) -> dict[str, Any]:
    return {
        "locale": target["locale"],
        "language": target["language"],
        "expected_format": target["expected_format"],
        "regional_token": target["regional_token"],
        "sets_with_locale": 0,
        "sets_language_match": 0,
        "sets_language_mismatch": 0,
        "language_mismatch_samples": [],
        "sets_with_expected_format": 0,
        "sets_missing_expected_format": 0,
        "format_source": Counter(),
        "sets_with_release_date": 0,
        "sets_missing_release_date": 0,
        "sets_with_prefix": 0,
        "sets_without_prefix": 0,
        "sets_prefix_contains_regional_token": 0,
        "sets_prefix_missing_regional_token": 0,
        "sets_with_editions": 0,
        "edition_values": Counter(),
        "contents_total_in_locale_sets": 0,
        "contents_explicitly_scoped": 0,
        "contents_applicable_to_locale": 0,
        "contents_unscoped": 0,
        "contents_scoped_elsewhere": 0,
        "printing_memberships": 0,
        "target_language_printing_memberships": 0,
        "printing_language_override_rows": 0,
        "printing_language_override_target_match": 0,
        "printing_language_override_target_mismatch": 0,
        "printing_language_override_values": Counter(),
        "printing_rows_missing_uuid": 0,
        "printing_rows_missing_card_uuid": 0,
        "printing_rows_missing_rarity": 0,
        "printing_rows_missing_suffix": 0,
        "unique_printing_uuids": set(),
        "unique_target_language_printing_uuids": set(),
        "unique_cards_referenced": set(),
        "unique_target_language_cards_referenced": set(),
        "rarities": Counter(),
        "collector_numbers_unique": set(),
        "collector_numbers_with_regional_token": 0,
        "collector_numbers_missing_regional_token": 0,
        "localized_identity_duplicate_rows": 0,
        "localized_identity_conflicts": 0,
        "localized_identity_conflict_samples": [],
        "cards_with_ygoprodeck_bridge": 0,
        "cards_with_official_id": 0,
        "cards_with_both_bridges": 0,
        "cards_missing_from_cards_file": 0,
        "cards_with_localized_name": 0,
        "cards_with_localized_text": 0,
        "cards_with_official_localized_text": 0,
        "printing_rows_with_localized_image": 0,
        "printing_rows_without_localized_image": 0,
        "printing_image_editions": Counter(),
        "samples": [],
    }


def _freeze_target_metrics(metrics: MutableMapping[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in metrics.items():
        if isinstance(value, Counter):
            result[key] = dict(sorted(value.items(), key=lambda kv: str(kv[0])))
        elif isinstance(value, set):
            result[key] = len(value)
        else:
            result[key] = value

    denom = len(metrics["unique_target_language_cards_referenced"])
    if denom:
        result["bridge_ygoprodeck_pct"] = round(100.0 * metrics["cards_with_ygoprodeck_bridge"] / denom, 4)
        result["bridge_official_id_pct"] = round(100.0 * metrics["cards_with_official_id"] / denom, 4)
        result["bridge_both_pct"] = round(100.0 * metrics["cards_with_both_bridges"] / denom, 4)
        result["localized_name_pct"] = round(100.0 * metrics["cards_with_localized_name"] / denom, 4)
        result["localized_text_pct"] = round(100.0 * metrics["cards_with_localized_text"] / denom, 4)
        result["official_localized_text_pct"] = round(
            100.0 * metrics["cards_with_official_localized_text"] / denom, 4
        )
    else:
        for key in (
            "bridge_ygoprodeck_pct",
            "bridge_official_id_pct",
            "bridge_both_pct",
            "localized_name_pct",
            "localized_text_pct",
            "official_localized_text_pct",
        ):
            result[key] = 0.0

    print_denom = metrics["target_language_printing_memberships"]
    result["localized_printing_image_pct"] = (
        round(100.0 * metrics["printing_rows_with_localized_image"] / print_denom, 4)
        if print_denom
        else 0.0
    )
    return result


def audit(input_dir: Path, source_url: str, archive_path: Optional[Path]) -> dict[str, Any]:
    cards_path = _find_file(input_dir, "cards.json")
    sets_path = _find_file(input_dir, "sets.json")
    try:
        meta_path = _find_file(input_dir, "meta.json")
    except FileNotFoundError:
        meta_path = None

    cards: dict[str, dict[str, Any]] = {}
    card_count = 0
    for card in _iter_records(cards_path):
        card_id = _norm(card.get("id") or card.get("uuid"))
        if not card_id:
            continue
        card_count += 1
        target_text: dict[str, dict[str, bool]] = {}
        for code, target in TARGETS.items():
            blob = _card_text_blob(card, target["language"])
            name = isinstance(blob.get("name"), str) and bool(blob.get("name").strip())
            text = any(
                isinstance(blob.get(key), str) and bool(blob.get(key).strip())
                for key in ("effect", "pendulumEffect", "pendulum_effect")
            )
            target_text[code] = {
                "name": name,
                "text": text,
                "official": blob.get("official") is not False if blob else False,
            }
        cards[card_id] = {
            "ygoprodeck_id": _extract_ygoprodeck_id(card),
            "official_id": _extract_official_id(card),
            "localized": target_text,
        }

    target_metrics = {code: _empty_target(target) for code, target in TARGETS.items()}
    printing_semantics: dict[str, Tuple[str, str, str, str]] = {}
    printing_uuid_conflicts: list[dict[str, Any]] = []
    printing_uuid_duplicate_rows = 0
    set_count = 0

    for set_obj in _iter_records(sets_path):
        set_count += 1
        set_id = _set_id(set_obj)
        locales = _set_locales(set_obj)
        contents = set_obj.get("contents") or []
        if isinstance(contents, Mapping):
            content_rows = [v for v in contents.values() if isinstance(v, Mapping)]
        elif isinstance(contents, list):
            content_rows = [v for v in contents if isinstance(v, Mapping)]
        else:
            content_rows = []

        for code, target in TARGETS.items():
            locale_blob = locales.get(target["locale"])
            if not isinstance(locale_blob, Mapping):
                continue

            m = target_metrics[code]
            m["sets_with_locale"] += 1

            actual_lang = _locale_language(locale_blob)
            if actual_lang == target["language"]:
                m["sets_language_match"] += 1
            else:
                m["sets_language_mismatch"] += 1
                if len(m["language_mismatch_samples"]) < 10:
                    m["language_mismatch_samples"].append(
                        {"set_id": set_id, "expected": target["language"], "actual": actual_lang or None}
                    )

            release_date = _locale_date(locale_blob)
            if release_date:
                m["sets_with_release_date"] += 1
            else:
                m["sets_missing_release_date"] += 1

            prefix = _locale_prefix(locale_blob)
            if prefix:
                m["sets_with_prefix"] += 1
                if target["regional_token"] in prefix.upper():
                    m["sets_prefix_contains_regional_token"] += 1
                else:
                    m["sets_prefix_missing_regional_token"] += 1
            else:
                m["sets_without_prefix"] += 1

            editions = _locale_editions(locale_blob)
            if editions:
                m["sets_with_editions"] += 1
                m["edition_values"].update(editions)

            localized_identity_owner: dict[Tuple[str, str, str, str], str] = {}
            saw_expected_format = False

            for content in content_rows:
                m["contents_total_in_locale_sets"] += 1
                scoped_locales, is_scoped = _content_locales(content)
                if not is_scoped:
                    m["contents_unscoped"] += 1
                    continue

                m["contents_explicitly_scoped"] += 1
                if target["locale"] not in scoped_locales:
                    m["contents_scoped_elsewhere"] += 1
                    continue
                m["contents_applicable_to_locale"] += 1

                formats, format_source = _formats(locale_blob, content)
                m["format_source"][format_source] += 1
                saw_expected_format = saw_expected_format or target["expected_format"] in formats

                for printing in _iter_printings(content):
                    m["printing_memberships"] += 1

                    printing_id = _printing_id(printing)
                    card_id = _printing_card_id(printing)
                    suffix = _norm(printing.get("suffix"))
                    rarity = _norm(printing.get("rarity"))
                    override_language = _norm_lower(printing.get("language"))
                    effective_language = override_language or actual_lang

                    if override_language:
                        m["printing_language_override_rows"] += 1
                        m["printing_language_override_values"][override_language] += 1
                        if override_language == target["language"]:
                            m["printing_language_override_target_match"] += 1
                        else:
                            m["printing_language_override_target_mismatch"] += 1

                    if not printing_id:
                        m["printing_rows_missing_uuid"] += 1
                    else:
                        m["unique_printing_uuids"].add(printing_id)
                    if not card_id:
                        m["printing_rows_missing_card_uuid"] += 1
                    else:
                        m["unique_cards_referenced"].add(card_id)
                    if not rarity:
                        m["printing_rows_missing_rarity"] += 1
                    else:
                        m["rarities"][rarity] += 1
                    if not suffix:
                        m["printing_rows_missing_suffix"] += 1

                    if printing_id:
                        semantics = (card_id, set_id, suffix, rarity)
                        old = printing_semantics.get(printing_id)
                        if old is None:
                            printing_semantics[printing_id] = semantics
                        else:
                            printing_uuid_duplicate_rows += 1
                            if old != semantics and len(printing_uuid_conflicts) < 50:
                                printing_uuid_conflicts.append(
                                    {"printing_id": printing_id, "first": old, "second": semantics}
                                )

                    if effective_language != target["language"]:
                        continue

                    m["target_language_printing_memberships"] += 1
                    if printing_id:
                        m["unique_target_language_printing_uuids"].add(printing_id)
                    if card_id:
                        m["unique_target_language_cards_referenced"].add(card_id)

                    collector = f"{prefix}{suffix}" if prefix or suffix else ""
                    if collector:
                        m["collector_numbers_unique"].add(collector)
                        if target["regional_token"] in collector.upper():
                            m["collector_numbers_with_regional_token"] += 1
                        else:
                            m["collector_numbers_missing_regional_token"] += 1

                    has_image, image_editions = _printing_image_info(locale_blob, printing_id)
                    if has_image:
                        m["printing_rows_with_localized_image"] += 1
                        m["printing_image_editions"].update(image_editions)
                    else:
                        m["printing_rows_without_localized_image"] += 1

                    identity_token = collector or f"uuid:{printing_id}"
                    identity = (set_id, target["locale"], identity_token, rarity)
                    old_card = localized_identity_owner.get(identity)
                    if old_card is None:
                        localized_identity_owner[identity] = card_id
                    else:
                        m["localized_identity_duplicate_rows"] += 1
                        if old_card != card_id:
                            m["localized_identity_conflicts"] += 1
                            if len(m["localized_identity_conflict_samples"]) < 20:
                                m["localized_identity_conflict_samples"].append(
                                    {
                                        "set_id": set_id,
                                        "identity": identity_token,
                                        "rarity": rarity,
                                        "first_card": old_card,
                                        "second_card": card_id,
                                    }
                                )

                    if len(m["samples"]) < 12:
                        card_meta = cards.get(card_id) or {}
                        m["samples"].append(
                            {
                                "set_id": set_id,
                                "printing_id": printing_id or None,
                                "card_id": card_id or None,
                                "collector_number": collector or None,
                                "rarity": rarity or None,
                                "release_date": release_date or None,
                                "effective_language": effective_language or None,
                                "ygoprodeck_id": card_meta.get("ygoprodeck_id"),
                                "official_id": card_meta.get("official_id"),
                                "localized_image": has_image,
                            }
                        )

            if saw_expected_format:
                m["sets_with_expected_format"] += 1
            else:
                m["sets_missing_expected_format"] += 1

    for code, m in target_metrics.items():
        for card_id in m["unique_target_language_cards_referenced"]:
            card = cards.get(card_id)
            if not card:
                m["cards_missing_from_cards_file"] += 1
                continue
            ygo_id = card.get("ygoprodeck_id")
            official_id = card.get("official_id")
            if ygo_id:
                m["cards_with_ygoprodeck_bridge"] += 1
            if official_id:
                m["cards_with_official_id"] += 1
            if ygo_id and official_id:
                m["cards_with_both_bridges"] += 1

            loc = (card.get("localized") or {}).get(code) or {}
            if loc.get("name"):
                m["cards_with_localized_name"] += 1
            if loc.get("text"):
                m["cards_with_localized_text"] += 1
            if loc.get("official") and loc.get("name"):
                m["cards_with_official_localized_text"] += 1

    gates = {
        "spanish_locale_present": target_metrics["es"]["sets_with_locale"] > 0,
        "japanese_locale_present": target_metrics["ja"]["sets_with_locale"] > 0,
        "spanish_target_language_printings_present":
            target_metrics["es"]["target_language_printing_memberships"] > 0,
        "japanese_target_language_printings_present":
            target_metrics["ja"]["target_language_printing_memberships"] > 0,
        "spanish_language_consistent": target_metrics["es"]["sets_language_mismatch"] == 0,
        "japanese_language_consistent": target_metrics["ja"]["sets_language_mismatch"] == 0,
        "spanish_printing_ids_complete": target_metrics["es"]["printing_rows_missing_uuid"] == 0,
        "japanese_printing_ids_complete": target_metrics["ja"]["printing_rows_missing_uuid"] == 0,
        "spanish_card_refs_complete": target_metrics["es"]["printing_rows_missing_card_uuid"] == 0,
        "japanese_card_refs_complete": target_metrics["ja"]["printing_rows_missing_card_uuid"] == 0,
        "spanish_target_cards_resolve": target_metrics["es"]["cards_missing_from_cards_file"] == 0,
        "japanese_target_cards_resolve": target_metrics["ja"]["cards_missing_from_cards_file"] == 0,
        "spanish_no_localized_identity_conflicts":
            target_metrics["es"]["localized_identity_conflicts"] == 0,
        "japanese_no_localized_identity_conflicts":
            target_metrics["ja"]["localized_identity_conflicts"] == 0,
        "no_printing_uuid_semantic_conflicts": len(printing_uuid_conflicts) == 0,
    }

    source: dict[str, Any] = {
        "url": source_url,
        "cards_file": str(cards_path.relative_to(input_dir)),
        "sets_file": str(sets_path.relative_to(input_dir)),
        "meta_file": str(meta_path.relative_to(input_dir)) if meta_path else None,
        "meta": _json_if_present(meta_path),
    }
    if archive_path and archive_path.exists():
        source["archive_bytes"] = archive_path.stat().st_size
        source["archive_sha256"] = _sha256(archive_path)

    return {
        "schema_version": 2,
        "audit": "yugioh_multilingual_ygojson_source_v2",
        "mode": "read_only_source_only",
        "production_writes": 0,
        "source": source,
        "totals": {
            "cards_records": card_count,
            "sets_records": set_count,
            "printing_uuid_semantics": len(printing_semantics),
            "printing_uuid_duplicate_rows": printing_uuid_duplicate_rows,
            "printing_uuid_semantic_conflicts": len(printing_uuid_conflicts),
            "printing_uuid_conflict_samples": printing_uuid_conflicts,
        },
        "targets": {code: _freeze_target_metrics(m) for code, m in target_metrics.items()},
        "gates": gates,
        "gate_pass": all(gates.values()),
        "notes": [
            "YGOJSON v1 physical printing rows are read from contents[].cards.",
            "Only explicitly locale-scoped content rows are counted as physical target memberships.",
            "Per-print language overrides are respected; target-language counts never inherit a conflicting override.",
            "Localized text availability is measured from card.text[language]; no text payload is exported.",
            "Localized printing-image availability is measured from locale cardInfo with deprecated cardImages fallback; no image payload is exported.",
            "Collector numbers are formed from the source locale prefix plus printing suffix; old OCG rows without codes fall back to printing UUID for uniqueness checks.",
            "YGOPRODeck and official Konami CID bridges are measured only; database compatibility is a separate later gate.",
            "No pricing data is imported or inferred.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-dir", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument(
        "--source-url",
        default="https://github.com/iconmaster5326/YGOJSON/releases/download/v1/aggregate.zip",
    )
    parser.add_argument("--archive", type=Path, default=None)
    args = parser.parse_args()

    report = audit(
        args.input_dir.resolve(),
        args.source_url,
        args.archive.resolve() if args.archive else None,
    )
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    def compact_target(code: str) -> dict[str, Any]:
        t = report["targets"][code]
        return {
            "sets": t["sets_with_locale"],
            "prints": t["target_language_printing_memberships"],
            "unique_printing_uuids": t["unique_target_language_printing_uuids"],
            "unique_cards": t["unique_target_language_cards_referenced"],
            "ygoprodeck_bridge_pct": t["bridge_ygoprodeck_pct"],
            "official_id_bridge_pct": t["bridge_official_id_pct"],
            "localized_name_pct": t["localized_name_pct"],
            "localized_printing_image_pct": t["localized_printing_image_pct"],
        }

    print(
        json.dumps(
            {
                "gate_pass": report["gate_pass"],
                "gates": report["gates"],
                "es": compact_target("es"),
                "ja": compact_target("ja"),
                "report": str(args.report),
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    return 0 if report["gate_pass"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
