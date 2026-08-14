from __future__ import annotations

import json
from collections import Counter

import requests


LANGUAGES = ("en", "es", "ja")
BASE_URL = "https://api.tcgdex.net/v2/{lang}/cards"


def _cards(language: str) -> dict[str, dict]:
    response = requests.get(BASE_URL.format(lang=language), timeout=60)
    response.raise_for_status()
    payload = response.json()
    return {
        str(row.get("id") or "").strip(): row
        for row in payload
        if isinstance(row, dict) and row.get("id")
    }


def _prefix(card_id: str) -> str:
    return card_id.split("-", 1)[0] if "-" in card_id else card_id


def main() -> int:
    maps = {language: _cards(language) for language in LANGUAGES}
    ids = {language: set(maps[language]) for language in LANGUAGES}
    overlaps = {
        "en_es": len(ids["en"] & ids["es"]),
        "en_ja": len(ids["en"] & ids["ja"]),
        "es_ja": len(ids["es"] & ids["ja"]),
        "en_es_ja": len(ids["en"] & ids["es"] & ids["ja"]),
    }
    prefix_samples = {}
    for language in LANGUAGES:
        counts = Counter(_prefix(card_id) for card_id in ids[language])
        prefix_samples[language] = counts.most_common(20)

    examples = {}
    for left, right in (("en", "es"), ("en", "ja"), ("es", "ja")):
        shared = sorted(ids[left] & ids[right])[:10]
        examples[f"{left}_{right}"] = [
            {
                "id": card_id,
                left: maps[left][card_id].get("name"),
                right: maps[right][card_id].get("name"),
            }
            for card_id in shared
        ]

    report = {
        "catalog_sizes": {language: len(maps[language]) for language in LANGUAGES},
        "overlaps": overlaps,
        "top_id_prefixes": prefix_samples,
        "shared_examples": examples,
        "catalog_examples": {
            language: [
                {"id": card_id, "name": maps[language][card_id].get("name")}
                for card_id in list(maps[language])[:10]
            ]
            for language in LANGUAGES
        },
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
