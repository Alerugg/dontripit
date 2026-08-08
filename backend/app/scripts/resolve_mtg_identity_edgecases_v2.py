from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import requests

from app.ingest.connectors.scryfall_mtg_v2 import ScryfallMtgV2Connector


def _write(path: Path | None, payload: dict) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def _iter_rows(connector: ScryfallMtgV2Connector, url: str):
    headers = {
        "User-Agent": connector._SCRYFALL_HEADERS["User-Agent"],
        "Accept": "application/gzip,application/jsonl,application/x-ndjson,*/*;q=0.8",
    }
    with requests.get(url, headers=headers, stream=True, timeout=240) as response:
        response.raise_for_status()
        response.raw.decode_content = False
        is_gzip = url.lower().endswith(".gz") or "gzip" in str(response.headers.get("Content-Type") or "").lower()
        if is_gzip:
            with gzip.GzipFile(fileobj=response.raw, mode="rb") as compressed:
                with io.TextIOWrapper(compressed, encoding="utf-8") as stream:
                    for line in stream:
                        line = line.strip()
                        if line:
                            yield json.loads(line)
        else:
            for line in response.iter_lines(decode_unicode=True):
                line = str(line or "").strip()
                if line:
                    yield json.loads(line)


def _is_paper(card: dict) -> bool:
    games = card.get("games")
    return not isinstance(games, list) or "paper" in {str(v or "").strip().lower() for v in games}


def _face_signature(face: dict) -> dict:
    return {
        "name": face.get("name"),
        "mana_cost": face.get("mana_cost"),
        "type_line": face.get("type_line"),
        "oracle_text": face.get("oracle_text"),
        "colors": face.get("colors"),
        "power": face.get("power"),
        "toughness": face.get("toughness"),
        "loyalty": face.get("loyalty"),
        "defense": face.get("defense"),
    }


def _rules_signature(card: dict) -> str:
    payload = {
        "name": card.get("name"),
        "layout": card.get("layout"),
        "mana_cost": card.get("mana_cost"),
        "type_line": card.get("type_line"),
        "oracle_text": card.get("oracle_text"),
        "colors": card.get("colors"),
        "color_identity": card.get("color_identity"),
        "power": card.get("power"),
        "toughness": card.get("toughness"),
        "loyalty": card.get("loyalty"),
        "defense": card.get("defense"),
        "faces": [_face_signature(face) for face in (card.get("card_faces") or []) if isinstance(face, dict)],
    }
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _finish_values(card: dict) -> tuple[str, ...]:
    values = {str(v or "").strip().lower() for v in (card.get("finishes") or []) if str(v or "").strip()}
    if not values:
        if card.get("nonfoil"):
            values.add("nonfoil")
        if card.get("foil"):
            values.add("foil")
    return tuple(sorted(values))


def run(*, report_path: Path | None = None) -> dict:
    connector = ScryfallMtgV2Connector()
    metadata = connector._bulk_metadata()
    url = connector._bulk_download_url(metadata)
    if not url:
        raise AssertionError("Scryfall bulk download URL unavailable")

    missing_oracle_rows: list[dict] = []
    missing_oracle_groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    no_image_rows: list[dict] = []
    multi_finish_examples: list[dict] = []
    natural_finish_owner: dict[tuple[str, str, str, str], str] = {}
    finish_collisions: list[dict] = []
    counts = Counter()

    for card in _iter_rows(connector, url):
        if not isinstance(card, dict) or not _is_paper(card):
            continue
        counts["paper_rows"] += 1
        sid = str(card.get("id") or "").strip()
        name = str(card.get("name") or "").strip()
        layout = str(card.get("layout") or "").strip().lower()
        set_code = str(card.get("set") or "").strip().lower()
        collector = str(card.get("collector_number") or "").strip()
        lang = str(card.get("lang") or "").strip().lower()
        finishes = _finish_values(card)

        for finish in finishes or ("unknown",):
            key = (set_code, collector, lang, finish)
            previous = natural_finish_owner.get(key)
            if previous and previous != sid:
                finish_collisions.append({"key": key, "owners": [previous, sid]})
            else:
                natural_finish_owner[key] = sid

        if len(finishes) > 1 and len(multi_finish_examples) < 30:
            multi_finish_examples.append({
                "scryfall_id": sid,
                "name": name,
                "set": set_code,
                "collector_number": collector,
                "lang": lang,
                "finishes": list(finishes),
                "foil_flag": bool(card.get("foil")),
                "nonfoil_flag": bool(card.get("nonfoil")),
            })

        image_uris = card.get("image_uris") or {}
        face_has_image = any(bool(face.get("image_uris")) for face in (card.get("card_faces") or []) if isinstance(face, dict))
        if not image_uris and not face_has_image:
            counts["no_image_rows"] += 1
            if len(no_image_rows) < 100:
                no_image_rows.append({
                    "scryfall_id": sid,
                    "name": name,
                    "layout": layout,
                    "set": set_code,
                    "set_type": card.get("set_type"),
                    "collector_number": collector,
                    "lang": lang,
                    "oracle_id": card.get("oracle_id"),
                    "card_faces": [face.get("name") for face in (card.get("card_faces") or []) if isinstance(face, dict)],
                })

        if not str(card.get("oracle_id") or "").strip():
            counts["missing_oracle_rows"] += 1
            signature = _rules_signature(card)
            row = {
                "scryfall_id": sid,
                "name": name,
                "layout": layout,
                "set": set_code,
                "set_name": card.get("set_name"),
                "set_type": card.get("set_type"),
                "collector_number": collector,
                "lang": lang,
                "rarity": card.get("rarity"),
                "type_line": card.get("type_line"),
                "illustration_id": card.get("illustration_id"),
                "card_faces": [face.get("name") for face in (card.get("card_faces") or []) if isinstance(face, dict)],
                "rules_signature": signature,
            }
            missing_oracle_rows.append(row)
            missing_oracle_groups[(name, layout)].append(row)

    group_analysis = []
    ambiguous_name_layout_groups = []
    for (name, layout), rows in sorted(missing_oracle_groups.items()):
        rule_sigs = sorted({row["rules_signature"] for row in rows})
        entry = {
            "name": name,
            "layout": layout,
            "rows": len(rows),
            "unique_rules_signatures": len(rule_sigs),
            "sets": sorted({row["set"] for row in rows}),
            "languages": sorted({row["lang"] for row in rows}),
            "scryfall_ids": [row["scryfall_id"] for row in rows],
            "rules_signatures": rule_sigs,
        }
        group_analysis.append(entry)
        if len(rule_sigs) > 1:
            ambiguous_name_layout_groups.append(entry)

    fallback_identity_count_name_layout = len(missing_oracle_groups)
    fallback_identity_count_rules = len({(name, layout, row["rules_signature"]) for (name, layout), rows in missing_oracle_groups.items() for row in rows})

    recommendation = (
        "name+layout is source-stable for current missing-oracle scope"
        if not ambiguous_name_layout_groups
        else "name+layout is ambiguous; include canonical rules signature in fallback Card identity"
    )

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": "read_only_mtg_identity_edgecase_resolution",
        "status": "review_required" if ambiguous_name_layout_groups or finish_collisions else "pass",
        "counts": {
            "paper_rows": int(counts["paper_rows"]),
            "missing_oracle_rows": int(counts["missing_oracle_rows"]),
            "missing_oracle_name_layout_groups": fallback_identity_count_name_layout,
            "missing_oracle_name_layout_rules_groups": fallback_identity_count_rules,
            "ambiguous_name_layout_groups": len(ambiguous_name_layout_groups),
            "no_image_rows": int(counts["no_image_rows"]),
            "natural_finish_identity_collisions": len(finish_collisions),
        },
        "fallback_card_identity": {
            "candidate": "oracle_id when present; otherwise source-backed normalized name + layout, adding rules_signature only if required by ambiguity",
            "recommendation": recommendation,
            "groups": group_analysis,
            "ambiguous_groups": ambiguous_name_layout_groups,
        },
        "missing_oracle_rows": missing_oracle_rows,
        "no_image_samples": no_image_rows,
        "multi_finish_examples": multi_finish_examples,
        "finish_identity_collisions": finish_collisions[:100],
        "database_writes": 0,
    }
    _write(report_path, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report-path", type=Path, default=None)
    args = parser.parse_args()
    run(report_path=args.report_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
