from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from app.ingest.connectors.scryfall_mtg_v2 import ScryfallMtgV2Connector
from app.scripts.preflight_mtg_identity_v2 import _finish_values, _is_paper, _iter_bulk_rows


def _write(path: Path | None, payload: dict) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def _compact(card: dict) -> dict:
    faces = card.get("card_faces") if isinstance(card.get("card_faces"), list) else []
    return {
        "id": card.get("id"),
        "name": card.get("name"),
        "oracle_id": card.get("oracle_id"),
        "set": card.get("set"),
        "set_name": card.get("set_name"),
        "set_type": card.get("set_type"),
        "collector_number": card.get("collector_number"),
        "lang": card.get("lang"),
        "layout": card.get("layout"),
        "finishes": list(_finish_values(card)),
        "games": card.get("games"),
        "released_at": card.get("released_at"),
        "promo": card.get("promo"),
        "promo_types": card.get("promo_types"),
        "reprint": card.get("reprint"),
        "variation": card.get("variation"),
        "variation_of": card.get("variation_of"),
        "illustration_id": card.get("illustration_id"),
        "oversized": card.get("oversized"),
        "digital": card.get("digital"),
        "reserved": card.get("reserved"),
        "frame": card.get("frame"),
        "frame_effects": card.get("frame_effects"),
        "border_color": card.get("border_color"),
        "full_art": card.get("full_art"),
        "textless": card.get("textless"),
        "story_spotlight": card.get("story_spotlight"),
        "security_stamp": card.get("security_stamp"),
        "card_faces": [
            {
                "name": face.get("name"),
                "oracle_id": face.get("oracle_id"),
                "illustration_id": face.get("illustration_id"),
                "type_line": face.get("type_line"),
                "image_status": bool(face.get("image_uris")),
            }
            for face in faces
            if isinstance(face, dict)
        ],
        "all_parts": [
            {
                "id": part.get("id"),
                "component": part.get("component"),
                "name": part.get("name"),
                "type_line": part.get("type_line"),
            }
            for part in (card.get("all_parts") or [])
            if isinstance(part, dict)
        ],
    }


def run(*, report_path: Path | None = None) -> dict:
    connector = ScryfallMtgV2Connector()
    metadata = connector._bulk_metadata()
    download_url = connector._bulk_download_url(metadata)
    if not download_url:
        raise AssertionError("Scryfall default_cards metadata exposes no downloadable bulk URL")

    rows: list[dict] = []
    layouts: Counter[str] = Counter()
    set_types: Counter[str] = Counter()
    sets: Counter[str] = Counter()
    names: Counter[str] = Counter()
    face_oracle_patterns: Counter[str] = Counter()
    variation_of_present = 0
    all_parts_present = 0

    for line in _iter_bulk_rows(connector, download_url):
        card = json.loads(line)
        if not _is_paper(card) or card.get("oracle_id"):
            continue
        compact = _compact(card)
        rows.append(compact)
        layouts[str(card.get("layout") or "unknown")] += 1
        set_types[str(card.get("set_type") or "unknown")] += 1
        sets[str(card.get("set") or "unknown")] += 1
        names[str(card.get("name") or "")] += 1
        faces = [face for face in (card.get("card_faces") or []) if isinstance(face, dict)]
        face_oracles = [str(face.get("oracle_id") or "").strip() for face in faces]
        if faces and all(face_oracles):
            face_oracle_patterns["all_faces_have_oracle_id"] += 1
        elif any(face_oracles):
            face_oracle_patterns["some_faces_have_oracle_id"] += 1
        elif faces:
            face_oracle_patterns["faces_without_oracle_id"] += 1
        else:
            face_oracle_patterns["no_faces"] += 1
        variation_of_present += int(bool(card.get("variation_of")))
        all_parts_present += int(bool(card.get("all_parts")))

    duplicate_name_groups = {
        name: count for name, count in sorted(names.items(), key=lambda item: (-item[1], item[0].lower())) if count > 1
    }
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": "read_only_mtg_missing_oracle_probe_v2",
        "status": "pass",
        "bulk_updated_at": metadata.get("updated_at"),
        "count": len(rows),
        "unique_scryfall_ids": len({row["id"] for row in rows}),
        "unique_names": len(names),
        "duplicate_name_groups": duplicate_name_groups,
        "distributions": {
            "layouts": dict(layouts.most_common()),
            "set_types": dict(set_types.most_common()),
            "sets": dict(sets.most_common()),
            "face_oracle_patterns": dict(face_oracle_patterns.most_common()),
            "variation_of_present": variation_of_present,
            "all_parts_present": all_parts_present,
        },
        "rows": sorted(rows, key=lambda row: (str(row.get("set") or ""), str(row.get("collector_number") or ""), str(row.get("id") or ""))),
        "safe_fallback_policy_candidate": {
            "card_key": "mtg:scryfall-object:<scryfall_id>",
            "reason": "Conservative source-backed fallback: never merges missing-oracle objects by display name. Exact finishes of the same Scryfall object still share one fallback Card.",
            "automatic_cross_object_merge": False,
        },
        "database_writes": 0,
    }
    _write(report_path, report)
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report-path", type=Path, default=None)
    args = parser.parse_args()
    run(report_path=args.report_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
