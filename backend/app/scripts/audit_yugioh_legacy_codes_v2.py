from __future__ import annotations

import argparse
import json
import re
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import requests

from app.ingest.connectors.ygoprodeck_yugioh import YgoProDeckYugiohConnector


CARDSETS_URL = "https://db.ygoprodeck.com/api/v7/cardsets.php"
DB_VERSION_URL = "https://db.ygoprodeck.com/api/v7/checkDBVer.php"
PAGE_SIZE = 500


def _clean(value: object) -> str:
    return str(value or "").strip()


def _fold(value: object) -> str:
    text = unicodedata.normalize("NFKD", _clean(value))
    text = "".join(ch for ch in text if not unicodedata.combining(ch)).casefold()
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")


def _family_from_hyphenated(code: str) -> str:
    value = _clean(code).upper()
    if "-" not in value:
        return ""
    return value.split("-", 1)[0].strip()


def _shape(code: str) -> str:
    value = _clean(code).upper()
    if not value:
        return "empty"
    if "-" in value:
        return "hyphenated"
    if re.fullmatch(r"[A-Z]+", value):
        return "letters_only"
    if re.fullmatch(r"[A-Z]+\d+", value):
        return "letters_then_digits"
    if re.fullmatch(r"[A-Z0-9]+", value):
        return "compact_alphanumeric"
    return "other"


def _write(path: Path | None, payload: dict) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def _fetch_json(http: requests.Session, url: str):
    response = http.get(url, timeout=60)
    response.raise_for_status()
    return response.json()


def run(*, report_path: Path | None = None) -> dict:
    connector = YgoProDeckYugiohConnector()
    cards = connector._load_remote(limit=None, page_size=PAGE_SIZE)

    http = requests.Session()
    http.headers.update({"User-Agent": "dontripit-catalog-v2-ygo-legacy-code-audit/1.0"})
    official_releases = _fetch_json(http, CARDSETS_URL)
    db_version = _fetch_json(http, DB_VERSION_URL)

    release_by_name: dict[str, dict] = {}
    release_by_fold: dict[str, list[dict]] = defaultdict(list)
    for row in official_releases:
        name = _clean(row.get("set_name"))
        if not name:
            continue
        release_by_name[name] = row
        release_by_fold[_fold(name)].append(row)

    # Build release-level evidence only from codes that have an explicit separator.
    release_hyphen_families: dict[str, Counter[str]] = defaultdict(Counter)
    release_all_codes: dict[str, Counter[str]] = defaultdict(Counter)
    release_card_count: Counter[str] = Counter()
    total_print_rows = 0
    no_hyphen_rows: list[dict] = []
    unmatched_release_rows = 0

    for card in cards:
        card_id = _clean(card.get("id"))
        for printing in card.get("card_sets") or []:
            total_print_rows += 1
            release_name = _clean(printing.get("set_name"))
            release = release_by_name.get(release_name)
            if release is None:
                candidates = release_by_fold.get(_fold(release_name)) or []
                release = candidates[0] if len(candidates) == 1 else None
            if release is None:
                unmatched_release_rows += 1
                continue

            authoritative_name = _clean(release.get("set_name"))
            authoritative_code = _clean(release.get("set_code")).upper()
            full_code = _clean(printing.get("set_code")).upper()
            release_card_count[authoritative_name] += 1
            release_all_codes[authoritative_name][full_code or "<missing>"] += 1
            family = _family_from_hyphenated(full_code)
            if family:
                release_hyphen_families[authoritative_name][family] += 1
                continue

            no_hyphen_rows.append({
                "card_id": card_id,
                "card_name": card.get("name"),
                "release_name": authoritative_name,
                "official_release_code": authoritative_code,
                "full_code": full_code,
                "shape": _shape(full_code),
                "rarity": _clean(printing.get("set_rarity")),
                "rarity_code": _clean(printing.get("set_rarity_code") or printing.get("set_rarity_short")).upper(),
            })

    decisions = Counter()
    no_hyphen_by_release = Counter()
    distinct_no_hyphen_codes = Counter()
    enriched_rows: list[dict] = []
    unresolved_rows: list[dict] = []
    inferred_rows: list[dict] = []
    exact_official_prefix_rows: list[dict] = []

    for row in no_hyphen_rows:
        release_name = row["release_name"]
        code = row["full_code"]
        official_code = row["official_release_code"]
        family_counts = release_hyphen_families.get(release_name, Counter())
        total_hyphen = sum(family_counts.values())
        ranked = family_counts.most_common()
        dominant_family = ranked[0][0] if ranked else None
        dominant_count = ranked[0][1] if ranked else 0
        dominant_ratio = (dominant_count / total_hyphen) if total_hyphen else 0.0
        unique_family = ranked[0][0] if len(ranked) == 1 else None

        official_prefix = False
        official_remainder = ""
        if official_code and code.startswith(official_code) and len(code) > len(official_code):
            official_remainder = code[len(official_code):]
            official_prefix = bool(re.fullmatch(r"[A-Z0-9]+", official_remainder))

        # This classification is deliberately evidence-only. It does not mutate the code.
        if official_prefix and unique_family == official_code:
            decision = "strong_official_prefix_and_same_explicit_family"
            candidate_family = official_code
        elif unique_family:
            decision = "single_explicit_family_in_same_release"
            candidate_family = unique_family
        elif dominant_family and total_hyphen >= 10 and dominant_ratio >= 0.95:
            decision = "dominant_explicit_family_in_same_release"
            candidate_family = dominant_family
        elif official_prefix:
            decision = "official_code_prefix_only"
            candidate_family = official_code
        else:
            decision = "unresolved_no_hyphen_code"
            candidate_family = None

        decisions[decision] += 1
        no_hyphen_by_release[release_name] += 1
        distinct_no_hyphen_codes[code or "<missing>"] += 1
        enriched = {
            **row,
            "candidate_family": candidate_family,
            "decision": decision,
            "official_prefix_remainder": official_remainder or None,
            "same_release_hyphen_rows": total_hyphen,
            "same_release_hyphen_families": dict(ranked),
            "dominant_family": dominant_family,
            "dominant_ratio": round(dominant_ratio, 6),
        }
        enriched_rows.append(enriched)
        if decision == "unresolved_no_hyphen_code":
            unresolved_rows.append(enriched)
        elif decision == "official_code_prefix_only":
            exact_official_prefix_rows.append(enriched)
        else:
            inferred_rows.append(enriched)

    release_summaries = []
    for release_name, count in no_hyphen_by_release.most_common():
        official = release_by_name.get(release_name)
        if official is None:
            candidates = release_by_fold.get(_fold(release_name)) or []
            official = candidates[0] if len(candidates) == 1 else {}
        families = release_hyphen_families.get(release_name, Counter())
        release_summaries.append({
            "release_name": release_name,
            "official_release_code": _clean((official or {}).get("set_code")).upper(),
            "no_hyphen_rows": count,
            "total_card_set_rows": release_card_count[release_name],
            "explicit_hyphen_families": dict(families.most_common()),
            "no_hyphen_codes": dict(
                Counter(row["full_code"] or "<missing>" for row in enriched_rows if row["release_name"] == release_name).most_common()
            ),
            "decision_counts": dict(
                Counter(row["decision"] for row in enriched_rows if row["release_name"] == release_name).most_common()
            ),
        })

    # Safe automation gate: only exact physical full code is trusted unconditionally.
    # Set-family assignment remains blocked while any no-hyphen row is unresolved or inference-only.
    inferred_count = len(inferred_rows) + len(exact_official_prefix_rows)
    hard_blockers = []
    if unmatched_release_rows:
        hard_blockers.append(f"{unmatched_release_rows} rows cannot map to an official release")
    if unresolved_rows:
        hard_blockers.append(f"{len(unresolved_rows)} no-hyphen codes have no defensible family candidate")
    if inferred_count:
        hard_blockers.append(
            f"{inferred_count} no-hyphen rows require inferred rather than directly parsed Set-family assignment"
        )

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": "read_only_yugioh_legacy_collector_code_audit",
        "status": "review_required" if hard_blockers else "pass",
        "source": {
            "database_version": db_version,
            "cards": len(cards),
            "official_release_rows": len(official_releases),
            "card_set_rows": total_print_rows,
        },
        "code_shape": {
            "no_hyphen_rows": len(no_hyphen_rows),
            "distinct_no_hyphen_codes": len(distinct_no_hyphen_codes),
            "no_hyphen_shapes": dict(Counter(row["shape"] for row in no_hyphen_rows).most_common()),
            "decision_counts": dict(decisions.most_common()),
        },
        "policy": {
            "exact_print_collector_number": "always preserve the raw full card_sets.set_code exactly as source evidence",
            "direct_set_family": "only parse prefix before '-' when the separator is explicitly present",
            "legacy_no_hyphen": "never silently repair. Candidate families from same-release evidence are review/inference until explicitly accepted or corroborated by another source.",
        },
        "hard_blockers_for_automatic_family_rebuild": hard_blockers,
        "release_summaries": release_summaries,
        "unresolved_rows": unresolved_rows[:250],
        "inferred_rows": inferred_rows[:250],
        "official_prefix_only_rows": exact_official_prefix_rows[:250],
        "all_no_hyphen_rows": enriched_rows[:1000],
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
