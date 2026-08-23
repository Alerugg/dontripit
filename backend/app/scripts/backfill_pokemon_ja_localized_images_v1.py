from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import requests
from sqlalchemy import create_engine, text


SOURCE_VERSION = "771a8381c57c73182b9776657a15cd1166c66d36"
SOURCE_LABEL = "tcgdex:ja"
EXPECTED_EXISTING_JA = 3297
EXPECTED_MISSING_JA = 4862
EXPECTED_VERIFIED = 705
EXPECTED_HTTP_404 = 4157
EXPECTED_MANIFEST_SHA256 = "9c6a051cd051478c33585eb98d61da20c415fbc31eb6d9ec4cf3656d9f98d5bc"
EXPECTED_VERIFIED_BY_SET = {
    "ja-m1s": 92,
    "ja-m4": 120,
    "ja-sm10": 107,
    "ja-sm11b": 68,
    "ja-sm12": 108,
    "ja-sm12a": 210,
}
OUTPUT = Path(
    os.environ.get(
        "POKEMON_JA_LOCALIZED_IMAGE_BACKFILL_OUTPUT",
        "artifacts/pokemon-ja-localized-images-v1.json",
    )
)
ID_RE = re.compile(r"\bid\s*:\s*[\"']([^\"']+)[\"']")


def _source_root() -> Path:
    return Path(os.environ["TCGDEX_CARDS_REPO"]) / "data-asia"


def _assert_source_pin() -> None:
    repo = Path(os.environ["TCGDEX_CARDS_REPO"])
    head = subprocess.check_output(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True
    ).strip()
    if head != SOURCE_VERSION:
        raise RuntimeError(
            f"Pinned TCGdex checkout drift: expected={SOURCE_VERSION} actual={head}"
        )


def _build_source_index() -> dict[str, str]:
    root = _source_root()
    set_to_asset_series: dict[str, str] = {}

    for path in root.rglob("*.ts"):
        rel = path.relative_to(root)
        if len(rel.parts) < 2:
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if "import { Set }" not in content or "import serie from" not in content:
            continue
        match = ID_RE.search(content)
        if not match:
            continue
        set_id = match.group(1)
        asset_series = rel.parts[0]
        previous = set_to_asset_series.get(set_id)
        if previous and previous != asset_series:
            raise RuntimeError(
                f"Conflicting Asia asset series for set {set_id}: {previous} vs {asset_series}"
            )
        set_to_asset_series[set_id] = asset_series

    if len(set_to_asset_series) != 333:
        raise RuntimeError(
            f"Pinned Asia set index drift: expected=333 actual={len(set_to_asset_series)}"
        )
    return set_to_asset_series


def _load_state(engine) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    with engine.connect() as conn:
        existing = [
            dict(row)
            for row in conn.execute(
                text(
                    """
                    SELECT p.id AS print_id,p.variant,p.collector_number,
                           im.id AS image_id,im.url,im.source,im.is_primary,
                           ids.cnt AS ja_identifier_count,ids.external_id
                    FROM prints p
                    JOIN cards c ON c.id=p.card_id
                    JOIN games g ON g.id=c.game_id
                    JOIN print_images im ON im.print_id=p.id
                    LEFT JOIN LATERAL (
                      SELECT COUNT(*)::int AS cnt,MIN(pi.external_id) AS external_id
                      FROM print_identifiers pi
                      WHERE pi.print_id=p.id AND pi.source='tcgdex:ja'
                    ) ids ON TRUE
                    WHERE g.slug='pokemon' AND p.language='ja' AND im.source='tcgdex:ja'
                    ORDER BY p.id,im.id
                    """
                )
            ).mappings().all()
        ]
        missing = [
            dict(row)
            for row in conn.execute(
                text(
                    """
                    SELECT p.id AS print_id,p.variant,p.collector_number,p.print_key,
                           c.name AS card_name,s.code AS set_code,s.name AS set_name,
                           ids.cnt AS ja_identifier_count,ids.external_id
                    FROM prints p
                    JOIN cards c ON c.id=p.card_id
                    JOIN games g ON g.id=c.game_id
                    LEFT JOIN sets s ON s.id=p.set_id
                    LEFT JOIN LATERAL (
                      SELECT COUNT(*)::int AS cnt,MIN(pi.external_id) AS external_id
                      FROM print_identifiers pi
                      WHERE pi.print_id=p.id AND pi.source='tcgdex:ja'
                    ) ids ON TRUE
                    WHERE g.slug='pokemon' AND p.language='ja'
                      AND NOT EXISTS(SELECT 1 FROM print_images im WHERE im.print_id=p.id)
                    ORDER BY p.id
                    """
                )
            ).mappings().all()
        ]
        conn.rollback()
    return existing, missing


def _validate_existing(
    existing: list[dict[str, Any]], set_to_asset_series: dict[str, str]
) -> None:
    set_ids = sorted(set_to_asset_series, key=len, reverse=True)
    errors: list[dict[str, Any]] = []

    for row in existing:
        if (
            row.get("variant") != "default"
            or int(row.get("ja_identifier_count") or 0) != 1
            or not row.get("external_id")
            or not bool(row.get("is_primary"))
        ):
            errors.append({"print_id": row["print_id"], "reason": "identity-shape"})
            continue
        external_id = str(row["external_id"])
        set_id = next((sid for sid in set_ids if external_id.startswith(sid + "-")), None)
        if not set_id:
            errors.append(
                {
                    "print_id": row["print_id"],
                    "external_id": external_id,
                    "reason": "no-pinned-set-match",
                }
            )
            continue
        local_id = external_id[len(set_id) + 1 :]
        series = set_to_asset_series[set_id]
        expected = f"https://assets.tcgdex.net/ja/{series}/{set_id}/{local_id}/high.webp"
        if str(row.get("url") or "") != expected:
            errors.append(
                {
                    "print_id": row["print_id"],
                    "external_id": external_id,
                    "actual": row.get("url"),
                    "expected": expected,
                }
            )

    if errors:
        raise RuntimeError(
            f"Existing JA localized-image integrity violations: {errors[:20]} total={len(errors)}"
        )


def _resolve_candidates(
    missing: list[dict[str, Any]], set_to_asset_series: dict[str, str]
) -> list[dict[str, Any]]:
    set_ids = sorted(set_to_asset_series, key=len, reverse=True)
    candidates: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []

    for row in missing:
        if (
            row.get("variant") != "default"
            or int(row.get("ja_identifier_count") or 0) != 1
            or not row.get("external_id")
        ):
            errors.append({"print_id": row["print_id"], "reason": "identity-shape"})
            continue

        external_id = str(row["external_id"])
        set_id = next((sid for sid in set_ids if external_id.startswith(sid + "-")), None)
        if not set_id:
            errors.append(
                {
                    "print_id": row["print_id"],
                    "external_id": external_id,
                    "set_code": row.get("set_code"),
                    "reason": "no-pinned-set-match",
                }
            )
            continue
        local_id = external_id[len(set_id) + 1 :]
        if not local_id:
            errors.append(
                {
                    "print_id": row["print_id"],
                    "external_id": external_id,
                    "reason": "empty-local-id",
                }
            )
            continue

        series = set_to_asset_series[set_id]
        candidates.append(
            {
                "print_id": int(row["print_id"]),
                "external_id": external_id,
                "collector_number": str(row.get("collector_number") or ""),
                "set_code": row.get("set_code"),
                "card_name": row.get("card_name"),
                "canonical_set_id": set_id,
                "asset_series": series,
                "local_id": local_id,
                "url": f"https://assets.tcgdex.net/ja/{series}/{set_id}/{local_id}/high.webp",
            }
        )

    if errors:
        raise RuntimeError(f"JA exact source mapping violations: {errors[:20]} total={len(errors)}")
    return candidates


def _probe_once(item: dict[str, Any]) -> dict[str, Any]:
    try:
        response = requests.get(
            item["url"],
            headers={
                "User-Agent": "Dontripit Pokemon JA localized image writer/1.0",
                "Accept": "image/webp,image/*;q=0.9,*/*;q=0.1",
            },
            timeout=18,
            stream=True,
        )
        content_type = str(response.headers.get("Content-Type") or "").lower()
        prefix = next(response.iter_content(32), b"") if response.status_code == 200 else b""
        return {
            **item,
            "status": response.status_code,
            "content_type": content_type,
            "prefix_hex": prefix[:12].hex(),
            "image_ok": response.status_code == 200
            and content_type.startswith("image/")
            and bool(prefix),
        }
    except Exception as exc:  # pragma: no cover - network evidence
        return {
            **item,
            "status": type(exc).__name__,
            "error": str(exc),
            "image_ok": False,
        }


def _probe_reliable(item: dict[str, Any]) -> dict[str, Any]:
    last: dict[str, Any] = {}
    for attempt in range(3):
        last = _probe_once(item)
        last["attempt"] = attempt + 1
        if last.get("image_ok"):
            return last
        if last.get("status") not in {429, 500, 502, 503, 504}:
            return last
        time.sleep(attempt + 1)
    return last


def _manifest_hash(items: list[dict[str, Any]]) -> str:
    lines = [
        f"{item['print_id']}|{item['external_id']}|{item['url']}"
        for item in sorted(items, key=lambda row: int(row["print_id"]))
    ]
    return hashlib.sha256(("\n".join(lines) + "\n").encode("utf-8")).hexdigest()


def _build_verified(engine, require_baseline: bool) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    set_to_asset_series = _build_source_index()
    existing, missing = _load_state(engine)

    if require_baseline:
        if len(existing) != EXPECTED_EXISTING_JA:
            raise RuntimeError(
                f"Existing JA image baseline drift: expected={EXPECTED_EXISTING_JA} actual={len(existing)}"
            )
        if len(missing) != EXPECTED_MISSING_JA:
            raise RuntimeError(
                f"JA missing-image baseline drift: expected={EXPECTED_MISSING_JA} actual={len(missing)}"
            )

    _validate_existing(existing, set_to_asset_series)
    candidates = _resolve_candidates(missing, set_to_asset_series)
    if require_baseline and len(candidates) != EXPECTED_MISSING_JA:
        raise RuntimeError(
            f"JA exact candidate coverage drift: expected={EXPECTED_MISSING_JA} actual={len(candidates)}"
        )

    results: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=24) as pool:
        futures = [pool.submit(_probe_reliable, item) for item in candidates]
        for future in as_completed(futures):
            results.append(future.result())
    results.sort(key=lambda row: int(row["print_id"]))
    verified = [row for row in results if row.get("image_ok")]

    status_counts = dict(Counter(str(row.get("status")) for row in results))
    verified_by_set = dict(Counter(str(row.get("set_code")) for row in verified))
    manifest_sha256 = _manifest_hash(verified)

    if require_baseline:
        expected_status = {"200": EXPECTED_VERIFIED, "404": EXPECTED_HTTP_404}
        if status_counts != expected_status:
            raise RuntimeError(
                f"JA probe status drift: expected={expected_status} actual={status_counts}"
            )
        if len(verified) != EXPECTED_VERIFIED:
            raise RuntimeError(
                f"Verified JA image count drift: expected={EXPECTED_VERIFIED} actual={len(verified)}"
            )
        if verified_by_set != EXPECTED_VERIFIED_BY_SET:
            raise RuntimeError(
                f"Verified JA set distribution drift: expected={EXPECTED_VERIFIED_BY_SET} actual={verified_by_set}"
            )
        if manifest_sha256 != EXPECTED_MANIFEST_SHA256:
            raise RuntimeError(
                f"Certified JA manifest drift: expected={EXPECTED_MANIFEST_SHA256} actual={manifest_sha256}"
            )

    metrics = {
        "source_version": SOURCE_VERSION,
        "asia_sets_indexed": len(set_to_asset_series),
        "existing_ja_image_rows_validated": len(existing),
        "residual_total": len(missing),
        "exact_ja_candidates": len(candidates),
        "probe_status_counts": status_counts,
        "verified_images": len(verified),
        "verified_by_set": verified_by_set,
        "verified_manifest_sha256": manifest_sha256,
    }
    return verified, metrics


def _apply(engine, verified: list[dict[str, Any]]) -> tuple[int, int]:
    inserted = 0
    skipped_existing = 0

    with engine.begin() as conn:
        for item in sorted(verified, key=lambda row: int(row["print_id"])):
            locked = conn.execute(
                text(
                    """
                    SELECT p.id,p.variant,p.language,p.collector_number,
                           g.slug AS game_slug,s.code AS set_code,
                           ids.cnt AS ja_identifier_count,ids.external_id
                    FROM prints p
                    JOIN cards c ON c.id=p.card_id
                    JOIN games g ON g.id=c.game_id
                    LEFT JOIN sets s ON s.id=p.set_id
                    LEFT JOIN LATERAL (
                      SELECT COUNT(*)::int AS cnt,MIN(pi.external_id) AS external_id
                      FROM print_identifiers pi
                      WHERE pi.print_id=p.id AND pi.source='tcgdex:ja'
                    ) ids ON TRUE
                    WHERE p.id=:print_id
                    FOR UPDATE OF p
                    """
                ),
                {"print_id": int(item["print_id"])},
            ).mappings().one_or_none()
            if locked is None:
                raise RuntimeError(f"Print disappeared during apply: {item['print_id']}")

            row = dict(locked)
            if row.get("game_slug") != "pokemon" or row.get("language") != "ja":
                raise RuntimeError(f"Print scope changed during apply: {row}")
            if row.get("variant") != "default":
                raise RuntimeError(f"Print variant changed during apply: {row}")
            if int(row.get("ja_identifier_count") or 0) != 1:
                raise RuntimeError(f"JA identifier cardinality changed during apply: {row}")
            if str(row.get("external_id") or "") != str(item["external_id"]):
                raise RuntimeError(
                    f"JA identity changed for print {item['print_id']}: "
                    f"expected={item['external_id']} actual={row.get('external_id')}"
                )
            if str(row.get("collector_number") or "") != str(item["collector_number"]):
                raise RuntimeError(
                    f"Collector number changed for print {item['print_id']}: "
                    f"expected={item['collector_number']} actual={row.get('collector_number')}"
                )
            if str(row.get("set_code") or "") != str(item.get("set_code") or ""):
                raise RuntimeError(
                    f"Set code changed for print {item['print_id']}: "
                    f"expected={item.get('set_code')} actual={row.get('set_code')}"
                )

            image_rows = [
                dict(r)
                for r in conn.execute(
                    text(
                        "SELECT id,url,source,is_primary FROM print_images "
                        "WHERE print_id=:print_id ORDER BY id"
                    ),
                    {"print_id": int(item["print_id"])},
                ).mappings().all()
            ]
            if image_rows:
                if (
                    len(image_rows) == 1
                    and str(image_rows[0].get("url") or "") == str(item["url"])
                    and str(image_rows[0].get("source") or "") == SOURCE_LABEL
                    and bool(image_rows[0].get("is_primary"))
                ):
                    skipped_existing += 1
                    continue
                raise RuntimeError(
                    f"Unexpected existing image for JA print {item['print_id']}: {image_rows}"
                )

            created = conn.execute(
                text(
                    """
                    INSERT INTO print_images (print_id,url,is_primary,source)
                    VALUES (:print_id,:url,true,:source)
                    RETURNING id
                    """
                ),
                {
                    "print_id": int(item["print_id"]),
                    "url": str(item["url"]),
                    "source": SOURCE_LABEL,
                },
            ).scalar_one()
            if not created:
                raise RuntimeError(f"Image insert returned no id for print {item['print_id']}")
            inserted += 1

    if inserted + skipped_existing != EXPECTED_VERIFIED:
        raise RuntimeError(
            f"JA apply cardinality mismatch: inserted={inserted} skipped_existing={skipped_existing}"
        )
    return inserted, skipped_existing


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--require-baseline", action="store_true")
    args = parser.parse_args()

    _assert_source_pin()
    engine = create_engine(os.environ["DATABASE_URL_UNPOOLED"], pool_pre_ping=True)

    if not args.apply:
        with engine.connect() as conn:
            conn.execute(text("SET TRANSACTION READ ONLY"))
            if conn.execute(text("SHOW transaction_read_only")).scalar_one() != "on":
                raise RuntimeError("Dry-run connection is not read-only")
            conn.rollback()

    verified, metrics = _build_verified(engine, args.require_baseline)

    inserted = 0
    skipped_existing = 0
    if args.apply:
        inserted, skipped_existing = _apply(engine, verified)

    report = {
        "status": "pass",
        "mode": "apply" if args.apply else "dry-run",
        "source_label": SOURCE_LABEL,
        **metrics,
        "production_writes": inserted,
        "skipped_existing_during_apply": skipped_existing,
        "manifest": [
            {
                "print_id": int(row["print_id"]),
                "external_id": row["external_id"],
                "card_name": row.get("card_name"),
                "set_code": row.get("set_code"),
                "collector_number": row.get("collector_number"),
                "canonical_set_id": row.get("canonical_set_id"),
                "asset_series": row.get("asset_series"),
                "url": row["url"],
                "source": SOURCE_LABEL,
                "probe_status": row.get("status"),
                "content_type": row.get("content_type"),
            }
            for row in sorted(verified, key=lambda item: int(item["print_id"]))
        ],
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(
        json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False, default=str) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {key: value for key, value in report.items() if key != "manifest"},
            indent=2,
            sort_keys=True,
            ensure_ascii=False,
            default=str,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
