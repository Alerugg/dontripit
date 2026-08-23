from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import requests
from sqlalchemy import create_engine, text


SOURCE_VERSION = "771a8381c57c73182b9776657a15cd1166c66d36"
SOURCE_LABEL = "tcgdex:es"
EXPECTED_EXISTING_ES = 13113
EXPECTED_MISSING_ES = 933
EXPECTED_DIRECT = 813
EXPECTED_ALIASES = 120
EXPECTED_VERIFIED = 127
EXPECTED_MANIFEST_SHA256 = "61bed92fa78e6bfedd695e1d92ddd4199dedbb248df165af7d7bc4897d5428ff"
EXPECTED_VERIFIED_BY_SET = {"SVP": 20, "swshp": 107}
OUTPUT = Path(
    os.environ.get(
        "POKEMON_ES_LOCALIZED_IMAGE_BACKFILL_OUTPUT",
        "artifacts/pokemon-es-localized-images-v1.json",
    )
)

ID_RE = re.compile(r"\bid\s*:\s*[\"']([^\"']+)[\"']")
OFFICIAL_RE = re.compile(
    r"abbreviations\s*:\s*\{.*?official\s*:\s*[\"']([^\"']+)[\"']",
    re.S,
)


def _source_root() -> Path:
    root = Path(os.environ["TCGDEX_CARDS_REPO"])
    return root / "data"


def _build_source_index() -> tuple[dict[str, str], dict[str, set[str]]]:
    root = _source_root()
    set_to_series: dict[str, str] = {}
    official_to_sets: dict[str, set[str]] = defaultdict(set)

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
        series_file = root / f"{rel.parts[0]}.ts"
        if not series_file.exists():
            continue
        try:
            series_content = series_file.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        series_match = ID_RE.search(series_content)
        if not series_match:
            continue
        series_id = series_match.group(1)

        previous = set_to_series.get(set_id)
        if previous and previous != series_id:
            raise RuntimeError(
                f"Conflicting pinned-source series for set {set_id}: {previous} vs {series_id}"
            )
        set_to_series[set_id] = series_id

        official_match = OFFICIAL_RE.search(content)
        if official_match:
            official_to_sets[official_match.group(1)].add(set_id)

    if len(set_to_series) < 200:
        raise RuntimeError(f"Implausibly small pinned set index: {len(set_to_series)}")
    return set_to_series, official_to_sets


def _load_state(engine) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    with engine.connect() as conn:
        existing = [
            dict(row)
            for row in conn.execute(
                text(
                    """
                    SELECT p.id AS print_id,p.variant,im.url,im.is_primary,
                           ids.cnt AS es_identifier_count,ids.external_id
                    FROM prints p
                    JOIN cards c ON c.id=p.card_id
                    JOIN games g ON g.id=c.game_id
                    JOIN print_images im ON im.print_id=p.id
                    LEFT JOIN LATERAL (
                      SELECT COUNT(*)::int AS cnt,MIN(pi.external_id) AS external_id
                      FROM print_identifiers pi
                      WHERE pi.print_id=p.id AND pi.source='tcgdex:es'
                    ) ids ON TRUE
                    WHERE g.slug='pokemon' AND p.language='es' AND im.source='tcgdex:es'
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
                           ids.cnt AS es_identifier_count,ids.external_id
                    FROM prints p
                    JOIN cards c ON c.id=p.card_id
                    JOIN games g ON g.id=c.game_id
                    LEFT JOIN sets s ON s.id=p.set_id
                    LEFT JOIN LATERAL (
                      SELECT COUNT(*)::int AS cnt,MIN(pi.external_id) AS external_id
                      FROM print_identifiers pi
                      WHERE pi.print_id=p.id AND pi.source='tcgdex:es'
                    ) ids ON TRUE
                    WHERE g.slug='pokemon' AND p.language='es'
                      AND NOT EXISTS(SELECT 1 FROM print_images im WHERE im.print_id=p.id)
                    ORDER BY p.id
                    """
                )
            ).mappings().all()
        ]
        conn.rollback()
    return existing, missing


def _validate_existing(
    existing: list[dict[str, Any]], set_to_series: dict[str, str]
) -> None:
    set_ids = sorted(set_to_series, key=len, reverse=True)
    errors: list[dict[str, Any]] = []

    for row in existing:
        if (
            row.get("variant") != "default"
            or int(row.get("es_identifier_count") or 0) != 1
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
        expected = (
            f"https://assets.tcgdex.net/es/{set_to_series[set_id]}/"
            f"{set_id}/{local_id}/high.webp"
        )
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
            f"Existing ES localized-image rule violations: {errors[:20]} total={len(errors)}"
        )


def _resolve_candidates(
    missing: list[dict[str, Any]],
    set_to_series: dict[str, str],
    official_to_sets: dict[str, set[str]],
) -> tuple[list[dict[str, Any]], int, int]:
    set_ids = sorted(set_to_series, key=len, reverse=True)
    candidates: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    direct = 0
    aliases = 0

    for row in missing:
        if (
            row.get("variant") != "default"
            or int(row.get("es_identifier_count") or 0) != 1
            or not row.get("external_id")
        ):
            errors.append({"print_id": row["print_id"], "reason": "identity-shape"})
            continue

        external_id = str(row["external_id"])
        collector = str(row.get("collector_number") or "")
        set_id = next((sid for sid in set_ids if external_id.startswith(sid + "-")), None)
        resolution = "direct-id-prefix"

        if set_id:
            local_id = external_id[len(set_id) + 1 :]
            direct += 1
        else:
            set_code = str(row.get("set_code") or "")
            matches = sorted(official_to_sets.get(set_code, set()))
            if len(matches) != 1:
                errors.append(
                    {
                        "print_id": row["print_id"],
                        "reason": "official-set-not-unique",
                        "external_id": external_id,
                        "set_code": set_code,
                        "matches": matches,
                    }
                )
                continue
            set_id = matches[0]
            local_id = external_id.rsplit("-", 1)[-1]
            if local_id != collector:
                errors.append(
                    {
                        "print_id": row["print_id"],
                        "reason": "alias-local-vs-collector",
                        "external_id": external_id,
                        "collector": collector,
                        "local_id": local_id,
                    }
                )
                continue
            aliases += 1
            resolution = "official-set-code-alias"

        if not local_id:
            errors.append(
                {
                    "print_id": row["print_id"],
                    "reason": "empty-local-id",
                    "external_id": external_id,
                }
            )
            continue

        series_id = set_to_series[set_id]
        candidates.append(
            {
                "print_id": int(row["print_id"]),
                "external_id": external_id,
                "collector_number": collector,
                "set_code": row.get("set_code"),
                "card_name": row.get("card_name"),
                "canonical_set_id": set_id,
                "series_id": series_id,
                "local_id": local_id,
                "resolution": resolution,
                "url": (
                    f"https://assets.tcgdex.net/es/{series_id}/"
                    f"{set_id}/{local_id}/high.webp"
                ),
            }
        )

    if errors:
        raise RuntimeError(f"ES candidate mapping violations: {errors[:20]} total={len(errors)}")
    return candidates, direct, aliases


def _probe_once(item: dict[str, Any]) -> dict[str, Any]:
    try:
        response = requests.get(
            item["url"],
            headers={
                "User-Agent": "Dontripit Pokemon ES localized image writer/1.0",
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
    set_to_series, official_to_sets = _build_source_index()
    existing, missing = _load_state(engine)

    if require_baseline:
        if len(existing) != EXPECTED_EXISTING_ES:
            raise RuntimeError(
                f"Existing ES localized-image baseline drift: "
                f"expected={EXPECTED_EXISTING_ES} actual={len(existing)}"
            )
        if len(missing) != EXPECTED_MISSING_ES:
            raise RuntimeError(
                f"ES missing-image baseline drift: expected={EXPECTED_MISSING_ES} actual={len(missing)}"
            )

    _validate_existing(existing, set_to_series)
    candidates, direct, aliases = _resolve_candidates(missing, set_to_series, official_to_sets)

    if require_baseline:
        if len(candidates) != EXPECTED_MISSING_ES:
            raise RuntimeError(
                f"ES candidate coverage drift: expected={EXPECTED_MISSING_ES} actual={len(candidates)}"
            )
        if direct != EXPECTED_DIRECT or aliases != EXPECTED_ALIASES:
            raise RuntimeError(
                f"ES resolution-shape drift: direct={direct} aliases={aliases} "
                f"expected={EXPECTED_DIRECT}/{EXPECTED_ALIASES}"
            )

    results: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=18) as pool:
        futures = [pool.submit(_probe_reliable, item) for item in candidates]
        for future in as_completed(futures):
            results.append(future.result())
    results.sort(key=lambda row: int(row["print_id"]))
    verified = [row for row in results if row.get("image_ok")]

    manifest_sha256 = _manifest_hash(verified)
    verified_by_set = dict(Counter(str(row.get("set_code")) for row in verified))

    if require_baseline:
        if len(verified) != EXPECTED_VERIFIED:
            raise RuntimeError(
                f"Verified ES image count drift: expected={EXPECTED_VERIFIED} actual={len(verified)}"
            )
        if manifest_sha256 != EXPECTED_MANIFEST_SHA256:
            raise RuntimeError(
                "Certified ES manifest drift: "
                f"expected={EXPECTED_MANIFEST_SHA256} actual={manifest_sha256}"
            )
        if verified_by_set != EXPECTED_VERIFIED_BY_SET:
            raise RuntimeError(
                f"Verified ES set distribution drift: "
                f"expected={EXPECTED_VERIFIED_BY_SET} actual={verified_by_set}"
            )

    metrics = {
        "source_version": SOURCE_VERSION,
        "sets_indexed": len(set_to_series),
        "existing_es_image_rows_validated": len(existing),
        "residual_total": len(missing),
        "direct_identifier_candidates": direct,
        "official_set_alias_candidates": aliases,
        "exact_localized_candidates": len(candidates),
        "probe_status_counts": dict(Counter(str(row.get("status")) for row in results)),
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
                           ids.cnt AS es_identifier_count,ids.external_id
                    FROM prints p
                    JOIN cards c ON c.id=p.card_id
                    JOIN games g ON g.id=c.game_id
                    LEFT JOIN sets s ON s.id=p.set_id
                    LEFT JOIN LATERAL (
                      SELECT COUNT(*)::int AS cnt,MIN(pi.external_id) AS external_id
                      FROM print_identifiers pi
                      WHERE pi.print_id=p.id AND pi.source='tcgdex:es'
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
            if row.get("game_slug") != "pokemon" or row.get("language") != "es":
                raise RuntimeError(f"Print scope changed during apply: {row}")
            if row.get("variant") != "default":
                raise RuntimeError(f"Print variant changed during apply: {row}")
            if int(row.get("es_identifier_count") or 0) != 1:
                raise RuntimeError(f"Localized identifier count changed during apply: {row}")
            if str(row.get("external_id") or "") != str(item["external_id"]):
                raise RuntimeError(
                    f"Localized identity changed for print {item['print_id']}: "
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
                    f"Unexpected existing image appeared for print {item['print_id']}: {image_rows}"
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
            f"Apply cardinality mismatch: inserted={inserted} skipped_existing={skipped_existing}"
        )
    return inserted, skipped_existing


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--require-baseline", action="store_true")
    args = parser.parse_args()

    repo = Path(os.environ["TCGDEX_CARDS_REPO"])
    source_head = os.popen(f"git -C '{repo}' rev-parse HEAD").read().strip()
    if source_head != SOURCE_VERSION:
        raise RuntimeError(
            f"Pinned TCGdex checkout drift: expected={SOURCE_VERSION} actual={source_head}"
        )

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
                "resolution": row.get("resolution"),
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
