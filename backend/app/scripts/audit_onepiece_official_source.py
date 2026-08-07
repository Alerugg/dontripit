from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from html import unescape
from urllib.parse import urljoin

import requests

from app.ingest.connectors.onepiece import OnePieceConnector


_MODAL_RE = re.compile(
    r'<dl\s+class="modalCol"\s+id="([^"]+)"[^>]*>(.*?)</dl>',
    flags=re.IGNORECASE | re.DOTALL,
)


def _collector_family(raw_print_id: str) -> str:
    base = str(raw_print_id or "").strip().upper().split("_", 1)[0]
    if re.fullmatch(r"OP\d{2}-\d{3}", base):
        return "OP"
    if re.fullmatch(r"ST\d{2}-\d{3}", base):
        return "ST"
    if re.fullmatch(r"EB\d{2}-\d{3}", base):
        return "EB"
    if re.fullmatch(r"P-\d{3}", base):
        return "P"
    prefix = re.match(r"([A-Z]+)", base)
    return prefix.group(1) if prefix else "OTHER"


def _fetch_series(
    *,
    connector: OnePieceConnector,
    base_url: str,
    series_id: str,
    label: str,
    timeout: int,
) -> dict:
    series_url = f"{base_url}?series={series_id}"
    response = requests.get(
        series_url,
        timeout=timeout,
        headers={"User-Agent": "TCGCatalogV2/1.0 (+https://github.com/Alerugg/dontripit)"},
    )
    response.raise_for_status()
    html = response.text

    raw_blocks = _MODAL_RE.findall(html)
    raw_ids = [str(print_id).strip().upper() for print_id, _body in raw_blocks]
    parsed = connector._parse_official_cards_page(html, base_url=base_url)

    parsed_ids = {str(row.get("print_id") or "").strip().upper() for row in parsed}
    raw_families = Counter(_collector_family(print_id) for print_id in raw_ids)
    skipped_ids = [print_id for print_id in raw_ids if print_id not in parsed_ids]
    skipped_families = Counter(_collector_family(print_id) for print_id in skipped_ids)
    parsed_set_codes = Counter(str(row.get("set_code") or "").strip().lower() for row in parsed)

    return {
        "series_id": series_id,
        "label": label,
        "series_url": series_url,
        "raw_prints": len(raw_ids),
        "parsed_prints": len(parsed),
        "skipped_prints": len(skipped_ids),
        "raw_families": dict(sorted(raw_families.items())),
        "skipped_families": dict(sorted(skipped_families.items())),
        "parsed_set_codes": dict(sorted(parsed_set_codes.items())),
        "skipped_samples": skipped_ids[:20],
        "raw_ids": raw_ids,
        "parsed_rows": parsed,
    }


def run_audit(*, workers: int = 8, timeout: int = 30) -> dict:
    connector = OnePieceConnector()
    base_url = connector._DEFAULT_OFFICIAL_CARDLIST_URL

    index_response = requests.get(
        base_url,
        timeout=timeout,
        headers={"User-Agent": "TCGCatalogV2/1.0 (+https://github.com/Alerugg/dontripit)"},
    )
    index_response.raise_for_status()
    series_options = connector._parse_official_series_options(index_response.text)
    if not series_options:
        raise RuntimeError("Official One Piece card list returned zero series options")

    series_results: list[dict] = []
    errors: list[dict] = []
    with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        futures = {
            executor.submit(
                _fetch_series,
                connector=connector,
                base_url=base_url,
                series_id=series_id,
                label=label,
                timeout=timeout,
            ): (series_id, label)
            for series_id, label in series_options
        }
        for future in as_completed(futures):
            series_id, label = futures[future]
            try:
                series_results.append(future.result())
            except Exception as exc:  # noqa: BLE001
                errors.append(
                    {
                        "series_id": series_id,
                        "label": label,
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )

    series_results.sort(key=lambda row: int(row["series_id"]))

    raw_family_counts: Counter[str] = Counter()
    skipped_family_counts: Counter[str] = Counter()
    set_code_series: defaultdict[str, set[str]] = defaultdict(set)
    raw_id_series: defaultdict[str, set[str]] = defaultdict(set)
    parsed_identity_series: defaultdict[tuple[str, str, str], set[str]] = defaultdict(set)

    total_raw = 0
    total_parsed = 0
    total_skipped = 0
    for row in series_results:
        total_raw += int(row["raw_prints"])
        total_parsed += int(row["parsed_prints"])
        total_skipped += int(row["skipped_prints"])
        raw_family_counts.update(row["raw_families"])
        skipped_family_counts.update(row["skipped_families"])

        for set_code in row["parsed_set_codes"]:
            if set_code:
                set_code_series[set_code].add(row["label"])

        for raw_id in row["raw_ids"]:
            raw_id_series[raw_id].add(row["label"])

        for parsed in row["parsed_rows"]:
            key = (
                str(parsed.get("set_code") or "").strip().lower(),
                str(parsed.get("collector_number") or "").strip().upper(),
                str(parsed.get("variant") or "default").strip().lower(),
            )
            parsed_identity_series[key].add(row["label"])

    multi_series_set_codes = [
        {"set_code": code, "series_count": len(labels), "series": sorted(labels)}
        for code, labels in set_code_series.items()
        if len(labels) > 1
    ]
    multi_series_set_codes.sort(key=lambda row: (-row["series_count"], row["set_code"]))

    duplicate_raw_ids = [
        {"print_id": print_id, "series_count": len(labels), "series": sorted(labels)}
        for print_id, labels in raw_id_series.items()
        if len(labels) > 1
    ]
    duplicate_raw_ids.sort(key=lambda row: (-row["series_count"], row["print_id"]))

    cross_series_canonical_identities = [
        {
            "set_code": key[0],
            "collector_number": key[1],
            "variant": key[2],
            "series_count": len(labels),
            "series": sorted(labels),
        }
        for key, labels in parsed_identity_series.items()
        if len(labels) > 1
    ]
    cross_series_canonical_identities.sort(
        key=lambda row: (-row["series_count"], row["set_code"], row["collector_number"], row["variant"])
    )

    series_with_skips = [
        {
            "series_id": row["series_id"],
            "label": row["label"],
            "raw_prints": row["raw_prints"],
            "parsed_prints": row["parsed_prints"],
            "skipped_prints": row["skipped_prints"],
            "skipped_families": row["skipped_families"],
            "skipped_samples": row["skipped_samples"],
        }
        for row in series_results
        if row["skipped_prints"]
    ]

    compact_series = [
        {
            "series_id": row["series_id"],
            "label": row["label"],
            "raw_prints": row["raw_prints"],
            "parsed_prints": row["parsed_prints"],
            "skipped_prints": row["skipped_prints"],
            "raw_families": row["raw_families"],
            "parsed_set_codes": row["parsed_set_codes"],
        }
        for row in series_results
    ]

    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": base_url,
        "read_only": True,
        "series_options": len(series_options),
        "series_fetched": len(series_results),
        "series_errors": errors,
        "totals": {
            "raw_print_entries": total_raw,
            "parsed_print_entries": total_parsed,
            "skipped_print_entries": total_skipped,
            "parse_coverage_percent": round((total_parsed / total_raw * 100), 3) if total_raw else 0.0,
        },
        "raw_collector_families": dict(sorted(raw_family_counts.items())),
        "skipped_collector_families": dict(sorted(skipped_family_counts.items())),
        "series_with_skipped_prints": series_with_skips,
        "commercial_set_codes": len(set_code_series),
        "commercial_set_codes_used_by_multiple_series": multi_series_set_codes,
        "raw_print_ids_seen_in_multiple_series": duplicate_raw_ids[:200],
        "canonical_print_identities_seen_in_multiple_series": cross_series_canonical_identities[:200],
        "series": compact_series,
        "interpretation": [
            "Parser coverage below 100% means official modal card entries exist that the canonical parser currently discards.",
            "A commercial set code appearing in multiple source series indicates product/series provenance is being collapsed into the collector-number set code.",
            "A canonical print identity appearing in multiple source series means set+collector+variant alone cannot preserve the commercial release context for that record.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit One Piece official card-list coverage without database writes")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--timeout", type=int, default=30)
    args = parser.parse_args()

    payload = run_audit(workers=args.workers, timeout=args.timeout)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 1 if payload["series_errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
