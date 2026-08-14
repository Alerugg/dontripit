from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

from audit_tcgdex_multilingual_backfill import (
    LANGUAGES,
    TCGDEX_BASE,
    _database_snapshot,
    _fetch_remote_catalog,
    _plan,
    _request_json,
    render_markdown as render_base_markdown,
)


OUTPUT_JSON = Path("/tmp/tcgdex-multilingual-physical-backfill-audit.json")
OUTPUT_MD = Path("/tmp/tcgdex-multilingual-physical-backfill-audit.md")


def _fetch_tcgp_set_ids(language: str) -> tuple[set[str], bool]:
    with requests.Session() as http:
        http.headers.update(
            {
                "Accept": "application/json",
                "User-Agent": "Dontripit-Multilingual-Physical-ReadOnly-Audit/1.0",
            }
        )
        base = TCGDEX_BASE.format(language=language)
        series = _request_json(http, f"{base}/series")
        if not isinstance(series, list):
            raise RuntimeError(
                f"TCG Pocket exclusion guard failed for {language}: "
                f"unexpected series list payload {type(series).__name__}"
            )
        available_series_ids = {
            str(item.get("id") or "").strip()
            for item in series
            if isinstance(item, dict) and str(item.get("id") or "").strip()
        }
        if "tcgp" not in available_series_ids:
            return set(), False

        payload = _request_json(http, f"{base}/series/tcgp")

    if not isinstance(payload, dict):
        raise RuntimeError(
            f"TCG Pocket exclusion guard failed for {language}: unexpected tcgp payload"
        )
    set_ids = {
        str(item.get("id") or "").strip()
        for item in (payload.get("sets") or [])
        if isinstance(item, dict) and str(item.get("id") or "").strip()
    }
    if not set_ids:
        raise RuntimeError(
            f"TCG Pocket exclusion guard failed for {language}: tcgp contains no sets"
        )
    return set_ids, True


def _physical_remote_catalog(language: str) -> tuple[dict[str, Any], dict[str, Any]]:
    catalog = _fetch_remote_catalog(language)
    pocket_set_ids, tcgp_published = _fetch_tcgp_set_ids(language)

    original_sets = catalog["sets"]
    original_cards = catalog["cards"]
    physical_sets = {
        external_id: row
        for external_id, row in original_sets.items()
        if external_id not in pocket_set_ids
    }
    physical_cards = {
        external_id: row
        for external_id, row in original_cards.items()
        if row.get("set_id") not in pocket_set_ids
    }

    cards_without_resolved_set = [
        external_id
        for external_id, row in physical_cards.items()
        if not row.get("set_id")
    ]
    if cards_without_resolved_set:
        raise RuntimeError(
            f"Physical TCGdex scope guard failed for {language}: "
            f"{len(cards_without_resolved_set)} card IDs could not be assigned to a set; "
            f"samples={cards_without_resolved_set[:10]}"
        )

    filtered = {
        **catalog,
        "sets": physical_sets,
        "cards": physical_cards,
        "unresolved_set_card_ids": [],
    }
    evidence = {
        "language": language,
        "tcgp_series_published": tcgp_published,
        "tcgp_set_ids": sorted(pocket_set_ids),
        "remote_sets_before_filter": len(original_sets),
        "remote_cards_before_filter": len(original_cards),
        "excluded_tcgp_sets": len(original_sets) - len(physical_sets),
        "excluded_tcgp_cards": len(original_cards) - len(physical_cards),
        "physical_sets_after_filter": len(physical_sets),
        "physical_cards_after_filter": len(physical_cards),
    }
    return filtered, evidence


def _production_en_drift(db: dict[str, Any], remote_en: dict[str, Any]) -> dict[str, Any]:
    production_cards = {
        str(row.get("tcgdex_id") or "").strip(): row
        for row in db["cards"]
        if str(row.get("tcgdex_id") or "").strip()
    }
    production_sets = {
        str(row.get("tcgdex_id") or "").strip(): row
        for row in db["sets"]
        if str(row.get("tcgdex_id") or "").strip()
    }
    remote_cards = remote_en["cards"]
    remote_sets = remote_en["sets"]

    production_card_ids = set(production_cards)
    production_set_ids = set(production_sets)
    remote_card_ids = set(remote_cards)
    remote_set_ids = set(remote_sets)

    production_only_cards = sorted(production_card_ids - remote_card_ids)
    remote_only_cards = sorted(remote_card_ids - production_card_ids)
    production_only_sets = sorted(production_set_ids - remote_set_ids)
    remote_only_sets = sorted(remote_set_ids - production_set_ids)

    return {
        "production_global_card_ids": len(production_card_ids),
        "current_remote_physical_card_ids": len(remote_card_ids),
        "shared_card_ids": len(production_card_ids & remote_card_ids),
        "production_only_card_ids": len(production_only_cards),
        "remote_only_card_ids": len(remote_only_cards),
        "production_only_card_samples": [
            {
                "external_id": external_id,
                "production_card_id": int(production_cards[external_id]["id"]),
                "production_name": production_cards[external_id].get("name"),
            }
            for external_id in production_only_cards[:100]
        ],
        "remote_only_card_samples": [
            {
                "external_id": external_id,
                "remote_name": remote_cards[external_id].get("name"),
                "remote_set_id": remote_cards[external_id].get("set_id"),
                "remote_local_id": remote_cards[external_id].get("local_id"),
            }
            for external_id in remote_only_cards[:100]
        ],
        "production_global_set_ids": len(production_set_ids),
        "current_remote_physical_set_ids": len(remote_set_ids),
        "shared_set_ids": len(production_set_ids & remote_set_ids),
        "production_only_set_ids": len(production_only_sets),
        "remote_only_set_ids": len(remote_only_sets),
        "production_only_set_samples": [
            {
                "external_id": external_id,
                "production_set_id": int(production_sets[external_id]["id"]),
                "production_code": production_sets[external_id].get("code"),
                "production_name": production_sets[external_id].get("name"),
            }
            for external_id in production_only_sets[:100]
        ],
        "remote_only_set_samples": [
            {
                "external_id": external_id,
                "remote_name": remote_sets[external_id].get("name"),
            }
            for external_id in remote_only_sets[:100]
        ],
    }


def run() -> dict[str, Any]:
    db = _database_snapshot()
    remote: dict[str, dict[str, Any]] = {}
    exclusions: dict[str, dict[str, Any]] = {}
    for language in LANGUAGES:
        catalog, evidence = _physical_remote_catalog(language)
        remote[language] = catalog
        exclusions[language] = evidence

    plan = _plan(remote, db)
    en_drift = _production_en_drift(db, remote["en"])
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": "strict-read-only-physical-backfill-plan",
        "database_transaction_read_only": db["transaction_read_only"],
        "database_identity": db["database_identity"],
        "scope": {
            "game": "pokemon",
            "catalog": "physical-tcg-only",
            "languages": list(LANGUAGES),
            "remote_source": "TCGdex REST v2",
            "excluded_series": ["tcgp when published by the language endpoint"],
            "database_writes": 0,
            "personal_data_tables_queried": False,
        },
        "tcg_pocket_exclusions": exclusions,
        "production_en_source_drift": en_drift,
        **plan,
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Don’tRipIt TCGdex multilingual physical backfill dry-run",
        "",
        f"Generated: `{report['generated_at']}`",
        "",
        "## Physical-scope guard",
        "",
        "For each language, the auditor first discovers available TCGdex series. "
        "When `tcgp` is published, all of its sets are excluded before any backfill candidate is counted.",
        "",
        "| Lang | tcgp published | Remote sets | Pocket sets excluded | Physical sets | Remote cards | Pocket cards excluded | Physical cards |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for language in LANGUAGES:
        row = report["tcg_pocket_exclusions"][language]
        lines.append(
            f"| {language} | {str(row['tcgp_series_published']).lower()} | "
            f"{row['remote_sets_before_filter']} | {row['excluded_tcgp_sets']} | "
            f"{row['physical_sets_after_filter']} | {row['remote_cards_before_filter']} | "
            f"{row['excluded_tcgp_cards']} | {row['physical_cards_after_filter']} |"
        )

    drift = report["production_en_source_drift"]
    lines.extend(
        [
            "",
            "## Production EN vs current physical TCGdex drift",
            "",
            f"- Production global TCGdex card IDs: **{drift['production_global_card_ids']}**",
            f"- Current remote physical EN card IDs: **{drift['current_remote_physical_card_ids']}**",
            f"- Shared card IDs: **{drift['shared_card_ids']}**",
            f"- Production-only card IDs: **{drift['production_only_card_ids']}**",
            f"- Remote-only card IDs: **{drift['remote_only_card_ids']}**",
            f"- Production global TCGdex set IDs: **{drift['production_global_set_ids']}**",
            f"- Current remote physical EN set IDs: **{drift['current_remote_physical_set_ids']}**",
            f"- Production-only set IDs: **{drift['production_only_set_ids']}**",
            f"- Remote-only set IDs: **{drift['remote_only_set_ids']}**",
        ]
    )

    base = render_base_markdown(report)
    base_lines = base.splitlines()
    if base_lines and base_lines[0].startswith("# "):
        base_lines = base_lines[1:]
    lines.extend(["", "## Physical backfill plan", *base_lines])
    return "\n".join(lines)


def main() -> int:
    report = run()
    OUTPUT_JSON.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    markdown = render_markdown(report)
    OUTPUT_MD.write_text(markdown + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
