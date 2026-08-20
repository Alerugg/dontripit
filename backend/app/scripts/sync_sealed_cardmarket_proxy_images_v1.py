from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
import json
import os
from pathlib import Path
import socket
import time
from typing import Any
import urllib.error

from sqlalchemy import create_engine, text

from app.routes.product_media import _cardmarket_url, _fetch_exact_image


SOURCE = "cardmarket_exact_proxy_v1"
DEFAULT_PUBLIC_BASE_URL = "https://api.dontripit.com"
CONTROL_URL = "https://product-images.s3.cardmarket.com/19/855004/855004.jpg"


ELIGIBLE_SQL = text(
    """
    WITH latest AS (
      SELECT game_id, MAX(last_seen_at) AS latest_seen
      FROM external_catalog_products
      WHERE source = 'cardmarket'
        AND product_group = 'non_single'
      GROUP BY game_id
    ), strict AS (
      SELECT
        l.product_variant_id,
        l.external_product_id,
        l.mapping_method,
        e.game_id,
        e.external_id,
        e.name AS external_name,
        e.category_id,
        e.category,
        e.website_path,
        COUNT(*) OVER(PARTITION BY l.external_product_id) AS variants_per_external,
        COUNT(*) OVER(PARTITION BY l.product_variant_id) AS externals_per_variant
      FROM external_catalog_product_variant_links l
      JOIN external_catalog_products e ON e.id = l.external_product_id
      JOIN latest x ON x.game_id = e.game_id AND x.latest_seen = e.last_seen_at
      WHERE e.source = 'cardmarket'
        AND e.product_group = 'non_single'
        AND l.link_status IN ('accepted', 'mapped', 'exact')
        AND l.confidence = 'exact'
        AND l.reviewed = TRUE
    )
    SELECT
      s.product_variant_id AS variant_id,
      g.slug AS game,
      p.id AS product_id,
      p.name AS product_name,
      p.product_type,
      st.code AS set_code,
      pv.language,
      pv.region,
      pv.packaging,
      s.external_id,
      s.external_name,
      s.category_id,
      s.category,
      s.website_path,
      s.mapping_method
    FROM strict s
    JOIN product_variants pv ON pv.id = s.product_variant_id
    JOIN products p ON p.id = pv.product_id
    JOIN games g ON g.id = p.game_id
    LEFT JOIN sets st ON st.id = p.set_id
    WHERE s.variants_per_external = 1
      AND s.externals_per_variant = 1
      AND NOT EXISTS (
        SELECT 1
        FROM product_images pi
        WHERE pi.product_variant_id = pv.id
      )
    ORDER BY g.slug, p.product_type, p.name, pv.id
    """
)


CURRENT_IDENTITY_SQL = text(
    """
    WITH latest AS (
      SELECT game_id, MAX(last_seen_at) AS latest_seen
      FROM external_catalog_products
      WHERE source = 'cardmarket'
        AND product_group = 'non_single'
      GROUP BY game_id
    ), strict AS (
      SELECT
        l.product_variant_id,
        e.external_id,
        e.category_id,
        COUNT(*) OVER(PARTITION BY l.external_product_id) AS variants_per_external,
        COUNT(*) OVER(PARTITION BY l.product_variant_id) AS externals_per_variant
      FROM external_catalog_product_variant_links l
      JOIN external_catalog_products e ON e.id = l.external_product_id
      JOIN latest x ON x.game_id = e.game_id AND x.latest_seen = e.last_seen_at
      WHERE e.source = 'cardmarket'
        AND e.product_group = 'non_single'
        AND l.link_status IN ('accepted', 'mapped', 'exact')
        AND l.confidence = 'exact'
        AND l.reviewed = TRUE
    )
    SELECT product_variant_id AS variant_id, external_id, category_id
    FROM strict
    WHERE variants_per_external = 1
      AND externals_per_variant = 1
    """
)


@dataclass(frozen=True)
class ProbeOutcome:
    variant_id: int
    external_id: str
    category_id: str
    status: str
    image_url: str
    sha256: str | None = None
    width: int | None = None
    height: int | None = None
    content_type: str | None = None
    bytes: int | None = None
    attempts: int = 1
    error: str | None = None


def _database_url() -> str:
    value = os.getenv("DATABASE_URL_UNPOOLED") or os.getenv("DATABASE_URL")
    if not value:
        raise RuntimeError("DATABASE_URL_UNPOOLED or DATABASE_URL is required")
    return value


def _engine():
    return create_engine(_database_url(), pool_pre_ping=True)


def _eligible_rows(engine) -> list[dict[str, Any]]:
    with engine.connect() as conn:
        return [dict(row) for row in conn.execute(ELIGIBLE_SQL).mappings().all()]


def _source_path(row: dict[str, Any]) -> tuple[str, str]:
    external_id = str(row.get("external_id") or "").strip()
    category_id = str(row.get("category_id") or "").strip()
    if not external_id.isdigit() or not category_id.isdigit():
        raise ValueError("exact Cardmarket sealed mapping lacks numeric category_id/idProduct")
    return category_id, external_id


def _probe_row(row: dict[str, Any], *, timeout: int, retries: int) -> ProbeOutcome:
    variant_id = int(row["variant_id"])
    try:
        category_id, external_id = _source_path(row)
    except ValueError as exc:
        return ProbeOutcome(
            variant_id=variant_id,
            external_id=str(row.get("external_id") or ""),
            category_id=str(row.get("category_id") or ""),
            status="invalid_source_identity",
            image_url="",
            error=str(exc),
        )

    image_url = _cardmarket_url(category_id, external_id)
    attempts = 0
    for attempt in range(retries + 1):
        attempts = attempt + 1
        try:
            body, content_type, width, height, digest = _fetch_exact_image(
                image_url,
                timeout=timeout,
            )
            return ProbeOutcome(
                variant_id=variant_id,
                external_id=external_id,
                category_id=category_id,
                status="recoverable_exact",
                image_url=image_url,
                sha256=digest,
                width=width,
                height=height,
                content_type=content_type,
                bytes=len(body),
                attempts=attempts,
            )
        except urllib.error.HTTPError as exc:
            if exc.code in {401, 403}:
                return ProbeOutcome(
                    variant_id=variant_id,
                    external_id=external_id,
                    category_id=category_id,
                    status="blocked",
                    image_url=image_url,
                    attempts=attempts,
                    error=f"HTTP {exc.code}",
                )
            if exc.code == 404:
                return ProbeOutcome(
                    variant_id=variant_id,
                    external_id=external_id,
                    category_id=category_id,
                    status="not_found",
                    image_url=image_url,
                    attempts=attempts,
                    error="HTTP 404",
                )
            retryable = exc.code == 429 or 500 <= exc.code <= 599
            if not retryable or attempt >= retries:
                return ProbeOutcome(
                    variant_id=variant_id,
                    external_id=external_id,
                    category_id=category_id,
                    status="transient_error" if retryable else "unexpected_http_error",
                    image_url=image_url,
                    attempts=attempts,
                    error=f"HTTP {exc.code}",
                )
        except (urllib.error.URLError, TimeoutError, socket.timeout) as exc:
            if attempt >= retries:
                return ProbeOutcome(
                    variant_id=variant_id,
                    external_id=external_id,
                    category_id=category_id,
                    status="transient_error",
                    image_url=image_url,
                    attempts=attempts,
                    error=f"{type(exc).__name__}: {exc}",
                )
        except ValueError as exc:
            return ProbeOutcome(
                variant_id=variant_id,
                external_id=external_id,
                category_id=category_id,
                status="invalid_media",
                image_url=image_url,
                attempts=attempts,
                error=str(exc),
            )

        time.sleep(min(2.0, 0.35 * (2 ** attempt)))

    raise AssertionError("probe retry loop exhausted unexpectedly")


def _probe_control(*, timeout: int, retries: int) -> dict[str, Any]:
    attempts = 0
    for attempt in range(retries + 1):
        attempts = attempt + 1
        try:
            body, content_type, width, height, digest = _fetch_exact_image(
                CONTROL_URL,
                timeout=timeout,
            )
            return {
                "ok": True,
                "url": CONTROL_URL,
                "bytes": len(body),
                "content_type": content_type,
                "width": width,
                "height": height,
                "sha256": digest,
                "attempts": attempts,
            }
        except Exception as exc:
            if attempt >= retries:
                return {
                    "ok": False,
                    "url": CONTROL_URL,
                    "attempts": attempts,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            time.sleep(min(2.0, 0.35 * (2 ** attempt)))
    raise AssertionError("control retry loop exhausted unexpectedly")


def _compact_row(row: dict[str, Any], outcome: ProbeOutcome) -> dict[str, Any]:
    return {
        "variant_id": int(row["variant_id"]),
        "game": str(row["game"]),
        "product_id": int(row["product_id"]),
        "product_name": str(row["product_name"]),
        "product_type": str(row["product_type"]),
        "set_code": row.get("set_code"),
        "language": row.get("language"),
        "region": row.get("region"),
        "packaging": row.get("packaging"),
        "external_id": outcome.external_id,
        "category_id": outcome.category_id,
        "mapping_method": row.get("mapping_method"),
        "status": outcome.status,
        "source_image_url": outcome.image_url,
        "sha256": outcome.sha256,
        "width": outcome.width,
        "height": outcome.height,
        "content_type": outcome.content_type,
        "bytes": outcome.bytes,
        "attempts": outcome.attempts,
        "error": outcome.error,
    }


def certify_snapshot(
    engine,
    *,
    max_workers: int,
    timeout: int,
    retries: int,
) -> dict[str, Any]:
    control = _probe_control(timeout=timeout, retries=retries)
    if not control.get("ok"):
        return {
            "gate": "FAIL",
            "control": control,
            "eligible": 0,
            "counts": {},
            "recoverable": [],
            "unavailable": [],
            "failures": [{"control": control}],
            "duplicate_hash_groups": {},
        }

    rows = _eligible_rows(engine)
    row_by_variant = {int(row["variant_id"]): row for row in rows}
    outcomes: dict[int, ProbeOutcome] = {}

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {
            pool.submit(_probe_row, row, timeout=timeout, retries=retries): int(row["variant_id"])
            for row in rows
        }
        for future in as_completed(futures):
            variant_id = futures[future]
            try:
                outcomes[variant_id] = future.result()
            except Exception as exc:
                row = row_by_variant[variant_id]
                outcomes[variant_id] = ProbeOutcome(
                    variant_id=variant_id,
                    external_id=str(row.get("external_id") or ""),
                    category_id=str(row.get("category_id") or ""),
                    status="transient_error",
                    image_url="",
                    error=f"worker_failure: {type(exc).__name__}: {exc}",
                )

    records = [
        _compact_row(row_by_variant[variant_id], outcomes[variant_id])
        for variant_id in sorted(outcomes)
    ]
    counts = Counter(str(record["status"]) for record in records)
    recoverable = [record for record in records if record["status"] == "recoverable_exact"]
    unavailable = [
        record
        for record in records
        if record["status"] in {"blocked", "not_found"}
    ]
    hard_failures = [
        record
        for record in records
        if record["status"] not in {"recoverable_exact", "blocked", "not_found"}
    ]

    hashes: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in recoverable:
        digest = str(record.get("sha256") or "")
        if digest:
            hashes[digest].append(record)
    duplicate_hash_groups = {
        digest: [
            {
                "variant_id": int(item["variant_id"]),
                "external_id": str(item["external_id"]),
                "product_name": str(item["product_name"]),
                "game": str(item["game"]),
            }
            for item in items
        ]
        for digest, items in hashes.items()
        if len({str(item["external_id"]) for item in items}) > 1
    }

    gate = "PASS"
    failures: list[dict[str, Any]] = []
    if hard_failures:
        gate = "FAIL"
        failures.append({"hard_probe_failures": len(hard_failures)})
    if duplicate_hash_groups:
        gate = "FAIL"
        failures.append({"duplicate_hash_groups": len(duplicate_hash_groups)})
    if rows and not recoverable:
        gate = "FAIL"
        failures.append({"recoverable_exact": 0})

    by_game = defaultdict(Counter)
    by_game_type = defaultdict(Counter)
    for record in records:
        by_game[str(record["game"])][str(record["status"])] += 1
        by_game_type[f"{record['game']}|{record['product_type']}"][str(record["status"])] += 1

    return {
        "gate": gate,
        "control": control,
        "eligible": len(rows),
        "counts": dict(sorted(counts.items())),
        "recoverable_count": len(recoverable),
        "unavailable_count": len(unavailable),
        "hard_failure_count": len(hard_failures),
        "unique_recoverable_hashes": len({str(row.get("sha256") or "") for row in recoverable}),
        "unique_recoverable_urls": len({str(row.get("source_image_url") or "") for row in recoverable}),
        "by_game": {key: dict(sorted(value.items())) for key, value in sorted(by_game.items())},
        "by_game_product_type": {
            key: dict(sorted(value.items())) for key, value in sorted(by_game_type.items())
        },
        "duplicate_hash_groups": duplicate_hash_groups,
        "failures": failures,
        "recoverable": recoverable,
        "unavailable": unavailable,
        "hard_failures": hard_failures,
    }


def _current_exact_identity(engine) -> dict[int, tuple[str, str]]:
    with engine.connect() as conn:
        rows = conn.execute(CURRENT_IDENTITY_SQL).mappings().all()
    return {
        int(row["variant_id"]): (
            str(row["external_id"] or "").strip(),
            str(row["category_id"] or "").strip(),
        )
        for row in rows
    }


def _proxy_url(public_base_url: str, variant_id: int) -> str:
    return (
        public_base_url.rstrip("/")
        + f"/media/product-variants/{variant_id}/cardmarket-image"
    )


def apply_manifest(
    engine,
    recoverable: list[dict[str, Any]],
    *,
    public_base_url: str,
) -> dict[str, Any]:
    exact_identity = _current_exact_identity(engine)
    inserted = 0
    unchanged = 0
    identity_changed = 0

    insert_sql = text(
        """
        INSERT INTO product_images (product_variant_id, url, is_primary, source)
        SELECT :variant_id, :url, TRUE, :source
        WHERE NOT EXISTS (
          SELECT 1 FROM product_images WHERE product_variant_id = :variant_id
        )
        """
    )

    with engine.begin() as conn:
        for record in recoverable:
            variant_id = int(record["variant_id"])
            expected = (
                str(record["external_id"]),
                str(record["category_id"]),
            )
            if exact_identity.get(variant_id) != expected:
                identity_changed += 1
                continue

            result = conn.execute(
                insert_sql,
                {
                    "variant_id": variant_id,
                    "url": _proxy_url(public_base_url, variant_id),
                    "source": SOURCE,
                },
            )
            if int(result.rowcount or 0) == 1:
                inserted += 1
            else:
                unchanged += 1

    return {
        "manifest_rows": len(recoverable),
        "inserted": inserted,
        "unchanged": unchanged,
        "identity_changed": identity_changed,
        "material_writes": inserted,
    }


def verify_materialized(engine, *, public_base_url: str) -> dict[str, Any]:
    query = text(
        """
        WITH latest AS (
          SELECT game_id, MAX(last_seen_at) AS latest_seen
          FROM external_catalog_products
          WHERE source = 'cardmarket'
            AND product_group = 'non_single'
          GROUP BY game_id
        ), strict AS (
          SELECT
            l.product_variant_id,
            e.external_id,
            e.category_id,
            COUNT(*) OVER(PARTITION BY l.external_product_id) AS variants_per_external,
            COUNT(*) OVER(PARTITION BY l.product_variant_id) AS externals_per_variant
          FROM external_catalog_product_variant_links l
          JOIN external_catalog_products e ON e.id = l.external_product_id
          JOIN latest x ON x.game_id = e.game_id AND x.latest_seen = e.last_seen_at
          WHERE e.source = 'cardmarket'
            AND e.product_group = 'non_single'
            AND l.link_status IN ('accepted', 'mapped', 'exact')
            AND l.confidence = 'exact'
            AND l.reviewed = TRUE
        )
        SELECT
          pi.id,
          pi.product_variant_id AS variant_id,
          pi.url,
          s.external_id,
          s.category_id,
          s.variants_per_external,
          s.externals_per_variant
        FROM product_images pi
        LEFT JOIN strict s ON s.product_variant_id = pi.product_variant_id
        WHERE pi.source = :source
        ORDER BY pi.product_variant_id
        """
    )
    with engine.connect() as conn:
        rows = [dict(row) for row in conn.execute(query, {"source": SOURCE}).mappings().all()]

    invalid: list[dict[str, Any]] = []
    for row in rows:
        variant_id = int(row["variant_id"])
        expected_url = _proxy_url(public_base_url, variant_id)
        if (
            row.get("external_id") is None
            or row.get("category_id") is None
            or int(row.get("variants_per_external") or 0) != 1
            or int(row.get("externals_per_variant") or 0) != 1
            or str(row.get("url") or "") != expected_url
        ):
            invalid.append(
                {
                    "variant_id": variant_id,
                    "url": row.get("url"),
                    "external_id": row.get("external_id"),
                    "category_id": row.get("category_id"),
                }
            )

    return {
        "gate": "PASS" if not invalid else "FAIL",
        "materialized_rows": len(rows),
        "invalid_rows": len(invalid),
        "invalid_sample": invalid[:25],
    }


def _report_without_full_lists(certification: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in certification.items()
        if key not in {"recoverable", "unavailable", "hard_failures"}
    }


def _write_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False, default=str) + "\n",
        encoding="utf-8",
    )
    summary = {
        "mode": report.get("mode"),
        "gate": report.get("gate"),
        "eligible": report.get("certification", report).get("eligible"),
        "counts": report.get("certification", report).get("counts"),
        "first_pass": report.get("first_pass"),
        "second_pass": report.get("second_pass"),
        "verification": report.get("verification"),
        "production_writes": report.get("production_writes"),
    }
    print(json.dumps(summary, indent=2, sort_keys=True, default=str))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Certify and materialize exact Cardmarket sealed images through the Don’tRipIt proxy."
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--audit", action="store_true")
    mode.add_argument("--apply", action="store_true")
    parser.add_argument("--certify-two-pass", action="store_true")
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--max-workers", type=int, default=8)
    parser.add_argument("--timeout", type=int, default=12)
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument(
        "--public-base-url",
        default=os.getenv("PRODUCT_MEDIA_PUBLIC_BASE_URL", DEFAULT_PUBLIC_BASE_URL),
    )
    args = parser.parse_args()

    if args.certify_two_pass and not args.apply:
        parser.error("--certify-two-pass requires --apply")
    if not 1 <= args.max_workers <= 16:
        parser.error("--max-workers must be between 1 and 16")
    if not 5 <= args.timeout <= 30:
        parser.error("--timeout must be between 5 and 30 seconds")
    if not 0 <= args.retries <= 3:
        parser.error("--retries must be between 0 and 3")
    if not args.public_base_url.startswith("https://"):
        parser.error("--public-base-url must use https")

    engine = _engine()
    certification = certify_snapshot(
        engine,
        max_workers=args.max_workers,
        timeout=args.timeout,
        retries=args.retries,
    )

    if args.audit:
        report = {
            "mode": "audit",
            "gate": certification["gate"],
            "production_writes": 0,
            **_report_without_full_lists(certification),
            "recoverable": certification.get("recoverable", []),
            "unavailable": certification.get("unavailable", []),
            "hard_failures": certification.get("hard_failures", []),
        }
        _write_report(args.report, report)
        return 0 if report["gate"] == "PASS" else 1

    report: dict[str, Any] = {
        "mode": "apply",
        "gate": certification["gate"],
        "certification": _report_without_full_lists(certification),
        "production_writes": 0,
    }
    if certification["gate"] != "PASS":
        report["recoverable"] = certification.get("recoverable", [])
        report["unavailable"] = certification.get("unavailable", [])
        report["hard_failures"] = certification.get("hard_failures", [])
        _write_report(args.report, report)
        return 1

    first = apply_manifest(
        engine,
        certification["recoverable"],
        public_base_url=args.public_base_url,
    )
    report["first_pass"] = first
    report["production_writes"] = first["material_writes"]
    if first["identity_changed"] != 0:
        report["gate"] = "FAIL"

    if args.certify_two_pass:
        second = apply_manifest(
            engine,
            certification["recoverable"],
            public_base_url=args.public_base_url,
        )
        report["second_pass"] = second
        if second["inserted"] != 0 or second["material_writes"] != 0:
            report["gate"] = "FAIL"

    verification = verify_materialized(
        engine,
        public_base_url=args.public_base_url,
    )
    report["verification"] = verification
    if verification["gate"] != "PASS":
        report["gate"] = "FAIL"

    _write_report(args.report, report)
    return 0 if report["gate"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
