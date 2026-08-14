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


def _fetch_tcgp_set_ids(language: str) -> set[str]:
    with requests.Session() as http:
        http.headers.update(
            {
                "Accept": "application/json",
                "User-Agent": "Dontripit-Multilingual-Physical-ReadOnly-Audit/1.0",
            }
        )
        base = TCGDEX_BASE.format(language=language)
        payload = _request_json(http, f"{base}/series/tcgp")

    if not isinstance(payload, dict):
        raise RuntimeError(
            f"TCG Pocket exclusion guard failed for {language}: unexpected series payload"
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
    return set_ids


def _physical_remote_catalog(language: str) -> tuple[dict[str, Any], dict[str, Any]]:
    catalog = _fetch_remote_catalog(language)
    pocket_set_ids = _fetch_tcgp_set_ids(language)

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
        "tcgp_set_ids": sorted(pocket_set_ids),
        "remote_sets_before_filter": len(original_sets),
        "remote_cards_before_filter": len(original_cards),
        "excluded_tcgp_sets": len(original_sets) - len(physical_sets),
        "excluded_tcgp_cards": len(original_cards) - len(physical_cards),
        "physical_sets_after_filter": len(physical_sets),
        "physical_cards_after_filter": len(physical_cards),
    }
    return filtered, evidence


def run() -> dict[str, Any]:
    db = _database_snapshot()
    remote: dict[str, dict[str, Any]] = {}
    exclusions: dict[str, dict[str, Any]] = {}
    for language in LANGUAGES:
        catalog, evidence = _physical_remote_catalog(language)
        remote[language] = catalog
        exclusions[language] = evidence

    plan = _plan(remote, db)
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
            "excluded_series": ["tcgp"],
            "database_writes": 0,
            "personal_data_tables_queried": False,
        },
        "tcg_pocket_exclusions": exclusions,
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
        "TCGdex series `tcgp` is excluded before any backfill candidate is counted.",
        "",
        "| Lang | Remote sets | Pocket sets excluded | Physical sets | Remote cards | Pocket cards excluded | Physical cards |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for language in LANGUAGES:
        row = report["tcg_pocket_exclusions"][language]
        lines.append(
            f"| {language} | {row['remote_sets_before_filter']} | {row['excluded_tcgp_sets']} | "
            f"{row['physical_sets_after_filter']} | {row['remote_cards_before_filter']} | "
            f"{row['excluded_tcgp_cards']} | {row['physical_cards_after_filter']} |"
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
