#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import time
from collections import Counter, defaultdict
from pathlib import Path

import requests


BASE = "https://api.tcgdex.net/v2/en"
HEADERS = {
    "Accept": "application/json",
    "User-Agent": "DontRipIt-PhysicalIdentityV2/1.0",
}


def get_json(session: requests.Session, url: str):
    wait = 0.25
    for attempt in range(1, 6):
        response = session.get(url, headers=HEADERS, timeout=45)
        if response.status_code in (429, 500, 502, 503, 504):
            if attempt == 5:
                response.raise_for_status()
            time.sleep(wait)
            wait *= 2
            continue
        response.raise_for_status()
        return response.json()
    raise RuntimeError(f"TCGdex request failed: {url}")


def deterministic_sample(rows: list[dict], n: int) -> list[dict]:
    rows = [r for r in rows if isinstance(r, dict) and str(r.get("id") or "").strip()]
    rows.sort(key=lambda r: str(r.get("id")))
    if n <= 0 or n >= len(rows):
        return rows
    indexes = sorted({round(i * (len(rows) - 1) / (n - 1)) for i in range(n)}) if n > 1 else [0]
    return [rows[i] for i in indexes]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", type=int, default=200)
    ap.add_argument("--report", type=Path, default=Path("/tmp/tcgdex-pokemon-variant-detail-v2.json"))
    args = ap.parse_args()

    session = requests.Session()
    card_briefs = get_json(session, f"{BASE}/cards")
    if not isinstance(card_briefs, list):
        raise RuntimeError(f"unexpected TCGdex /cards payload: {type(card_briefs).__name__}")
    selected = deterministic_sample(card_briefs, args.sample)

    # Always include a known old-card stress case when present.
    by_id = {str(r.get("id")): r for r in card_briefs if isinstance(r, dict)}
    for forced in ("base1-4", "sv10.5b-001"):
        if forced in by_id and all(str(r.get("id")) != forced for r in selected):
            selected.append(by_id[forced])

    cards = []
    errors = []
    for index, brief in enumerate(selected, start=1):
        card_id = str(brief.get("id"))
        try:
            card = get_json(session, f"{BASE}/cards/{card_id}")
            if isinstance(card, dict):
                cards.append(card)
            else:
                errors.append({"id": card_id, "error": f"unexpected {type(card).__name__}"})
        except Exception as exc:
            errors.append({"id": card_id, "error": f"{type(exc).__name__}: {exc}"})
        if index % 25 == 0:
            time.sleep(0.1)

    cards_with_rarity = 0
    cards_with_basic_variants = 0
    cards_with_detailed_variants = 0
    cards_with_any_direct_cm = 0
    detailed_variants = 0
    detailed_with_cm = 0
    detailed_with_variant_id = 0
    detailed_with_stamp = 0
    detailed_with_foil = 0
    detailed_with_subtype = 0
    cm_to_variants: dict[str, set[str]] = defaultdict(set)
    variant_types = Counter()
    foil_types = Counter()
    subtype_types = Counter()
    stamp_types = Counter()
    samples = []

    for card in cards:
        if str(card.get("rarity") or "").strip():
            cards_with_rarity += 1
        if isinstance(card.get("variants"), dict):
            cards_with_basic_variants += 1
        variants = card.get("variants_detailed")
        if not isinstance(variants, list) or not variants:
            continue
        cards_with_detailed_variants += 1
        card_has_cm = False
        for variant in variants:
            if not isinstance(variant, dict):
                continue
            detailed_variants += 1
            variant_id = str(variant.get("variantId") or "").strip()
            if variant_id:
                detailed_with_variant_id += 1
            variant_type = str(variant.get("type") or "").strip()
            if variant_type:
                variant_types[variant_type] += 1
            foil = str(variant.get("foil") or "").strip()
            if foil:
                detailed_with_foil += 1
                foil_types[foil] += 1
            subtype = str(variant.get("subtype") or "").strip()
            if subtype:
                detailed_with_subtype += 1
                subtype_types[subtype] += 1
            stamps = variant.get("stamp") or []
            if stamps:
                detailed_with_stamp += 1
                for stamp in stamps:
                    stamp_types[str(stamp)] += 1
            third_party = variant.get("thirdParty") or {}
            cm_id = str(third_party.get("cardmarket") or "").strip() if isinstance(third_party, dict) else ""
            if cm_id:
                detailed_with_cm += 1
                card_has_cm = True
                cm_to_variants[cm_id].add(variant_id or f"{card.get('id')}:{variant_type}:{foil}:{subtype}:{','.join(map(str, stamps))}")
        if card_has_cm:
            cards_with_any_direct_cm += 1
        if len(samples) < 30 and (card_has_cm or any((v.get("stamp") or v.get("foil") or v.get("subtype")) for v in variants if isinstance(v, dict))):
            samples.append({
                "id": card.get("id"),
                "name": card.get("name"),
                "rarity": card.get("rarity"),
                "variants": card.get("variants"),
                "variants_detailed": variants,
            })

    cm_conflicts = {cm_id: sorted(variant_ids) for cm_id, variant_ids in cm_to_variants.items() if len(variant_ids) > 1}
    summary = {
        "mode": "read_only",
        "audit": "tcgdex_pokemon_variant_detail_v2",
        "card_briefs_available": len(card_briefs),
        "cards_requested": len(selected),
        "cards_loaded": len(cards),
        "request_errors": len(errors),
        "cards_with_rarity": cards_with_rarity,
        "cards_with_basic_variants": cards_with_basic_variants,
        "cards_with_detailed_variants": cards_with_detailed_variants,
        "cards_with_any_direct_cardmarket_id": cards_with_any_direct_cm,
        "detailed_variants": detailed_variants,
        "detailed_variants_with_variant_id": detailed_with_variant_id,
        "detailed_variants_with_direct_cardmarket_id": detailed_with_cm,
        "detailed_variants_with_stamp": detailed_with_stamp,
        "detailed_variants_with_foil": detailed_with_foil,
        "detailed_variants_with_subtype": detailed_with_subtype,
        "distinct_direct_cardmarket_ids": len(cm_to_variants),
        "direct_cardmarket_ids_claimed_by_multiple_variant_ids": len(cm_conflicts),
        "production_writes": 0,
    }
    payload = {
        "summary": summary,
        "variant_types": variant_types.most_common(),
        "foil_types": foil_types.most_common(),
        "subtype_types": subtype_types.most_common(),
        "stamp_types": stamp_types.most_common(),
        "cardmarket_conflicts": dict(list(cm_conflicts.items())[:50]),
        "errors": errors[:50],
        "samples": samples,
    }
    print("TCGDEX_POKEMON_VARIANT_DETAIL_V2=" + json.dumps(summary, separators=(",", ":")))
    args.report.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
