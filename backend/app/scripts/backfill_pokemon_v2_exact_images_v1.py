from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import requests
from sqlalchemy import create_engine, text


SOURCE_VERSION = "771a8381c57c73182b9776657a15cd1166c66d36"
SOURCE_LABEL = "tcgplayer:tcgdex-variant-v2"
EXPECTED_ALL_V2 = 27241
EXPECTED_RESIDUAL = 1296
EXPECTED_WITH_TCGPLAYER = 1231
EXPECTED_UNIQUE_EXACT = 1108
OUTPUT = Path(
    os.environ.get(
        "POKEMON_V2_IMAGE_BACKFILL_OUTPUT",
        "artifacts/pokemon-v2-exact-images-v1.json",
    )
)


def _load_v2_rows(engine) -> list[dict[str, Any]]:
    with engine.connect() as conn:
        rows = [
            dict(row)
            for row in conn.execute(
                text(
                    """
                    SELECT p.id AS print_id,p.variant,p.language,p.is_foil,p.tcgdex_id,
                           p.collector_number,p.rarity,p.print_key,
                           c.name AS card_name,s.code AS set_code,
                           pa.source AS attributes_source,
                           pa.source_version,pa.attributes_json,
                           (SELECT pi.external_id
                            FROM print_identifiers pi
                            WHERE pi.print_id=p.id AND pi.source='tcgdex-variant-v2'
                            LIMIT 1) AS exact_variant_id,
                           EXISTS(SELECT 1 FROM print_images im WHERE im.print_id=p.id) AS has_image
                    FROM prints p
                    JOIN cards c ON c.id=p.card_id
                    JOIN games g ON g.id=c.game_id
                    LEFT JOIN sets s ON s.id=p.set_id
                    JOIN print_attributes pa ON pa.print_id=p.id
                    WHERE g.slug='pokemon' AND p.language='en'
                      AND COALESCE(p.variant,'') LIKE 'v2-%'
                    ORDER BY p.id
                    """
                )
            ).mappings().all()
        ]
        conn.rollback()
    return rows


def _normalize_exact_identity(row: dict[str, Any]) -> tuple[str, str, dict[str, Any], str | None]:
    if str(row.get("attributes_source") or "") != "tcgdex/cards-database":
        raise RuntimeError(f"Unexpected attributes source for print {row['print_id']}: {row.get('attributes_source')}")
    if str(row.get("source_version") or "") != SOURCE_VERSION:
        raise RuntimeError(
            f"Unexpected source version for print {row['print_id']}: "
            f"expected={SOURCE_VERSION} actual={row.get('source_version')}"
        )
    attrs = row.get("attributes_json")
    if not isinstance(attrs, dict):
        raise RuntimeError(f"Missing attributes_json for print {row['print_id']}")
    physical = attrs.get("physical_variant")
    if not isinstance(physical, dict):
        raise RuntimeError(f"Missing physical_variant for print {row['print_id']}")
    source_id = str(attrs.get("source_id") or "").strip()
    variant_hash = str(physical.get("variant_hash") or "").strip()
    if not source_id or not variant_hash:
        raise RuntimeError(f"Incomplete exact physical identity for print {row['print_id']}")
    exact_identity = f"{source_id}#{variant_hash}"
    if str(row.get("exact_variant_id") or "") != exact_identity:
        raise RuntimeError(
            f"tcgdex-variant-v2 mismatch for print {row['print_id']}: "
            f"expected={exact_identity} actual={row.get('exact_variant_id')}"
        )
    if str(row.get("variant") or "") != f"v2-{variant_hash}":
        raise RuntimeError(
            f"Print.variant mismatch for print {row['print_id']}: "
            f"expected=v2-{variant_hash} actual={row.get('variant')}"
        )
    third_party = physical.get("third_party") or {}
    tcgplayer = None
    if isinstance(third_party, dict) and third_party.get("tcgplayer") not in (None, ""):
        tcgplayer = str(third_party["tcgplayer"])
    return exact_identity, source_id, physical, tcgplayer


def _probe_product_once(product_id: str) -> dict[str, Any]:
    url = f"https://tcgplayer-cdn.tcgplayer.com/product/{product_id}_in_1000x1000.jpg"
    try:
        response = requests.get(
            url,
            headers={
                "User-Agent": "Dontripit Pokemon v2 exact image writer/1.0",
                "Accept": "image/*",
            },
            timeout=18,
            stream=True,
        )
        content_type = str(response.headers.get("Content-Type") or "").lower()
        prefix = next(response.iter_content(32), b"") if response.status_code == 200 else b""
        return {
            "product_id": product_id,
            "url": url,
            "status": response.status_code,
            "content_type": content_type,
            "prefix_hex": prefix[:12].hex(),
            "image_ok": response.status_code == 200
            and content_type.startswith("image/")
            and bool(prefix),
        }
    except Exception as exc:  # pragma: no cover - network evidence
        return {
            "product_id": product_id,
            "url": url,
            "status": type(exc).__name__,
            "error": str(exc),
            "image_ok": False,
        }


def _probe_product_reliable(product_id: str) -> dict[str, Any]:
    last: dict[str, Any] = {}
    for attempt in range(4):
        result = _probe_product_once(product_id)
        result["attempt"] = attempt + 1
        last = result
        if result.get("image_ok"):
            return result
        if result.get("status") not in {403, 429, 500, 502, 503, 504}:
            return result
        time.sleep(0.35 * (attempt + 1))
    return last


def _build_candidates(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if len(rows) != EXPECTED_ALL_V2:
        raise RuntimeError(f"Expected {EXPECTED_ALL_V2} EN v2 Prints, found {len(rows)}")

    product_to_exact: dict[str, set[str]] = defaultdict(set)
    normalized: list[dict[str, Any]] = []
    for row in rows:
        exact_identity, source_id, physical, tcgplayer = _normalize_exact_identity(row)
        item = dict(row)
        item.update(
            {
                "exact_identity": exact_identity,
                "source_id": source_id,
                "physical_variant": physical,
                "tcgplayer_product_id": tcgplayer,
            }
        )
        normalized.append(item)
        if tcgplayer:
            product_to_exact[tcgplayer].add(exact_identity)

    residual = [item for item in normalized if not item["has_image"]]
    with_tcgplayer = [item for item in residual if item["tcgplayer_product_id"]]
    unique_exact = [
        item
        for item in with_tcgplayer
        if len(product_to_exact[item["tcgplayer_product_id"]]) == 1
    ]
    shared = [
        item
        for item in with_tcgplayer
        if len(product_to_exact[item["tcgplayer_product_id"]]) > 1
    ]

    product_ids = sorted({str(item["tcgplayer_product_id"]) for item in unique_exact})
    probes: dict[str, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=12) as pool:
        futures = {pool.submit(_probe_product_reliable, product_id): product_id for product_id in product_ids}
        for future in as_completed(futures):
            probes[futures[future]] = future.result()

    verified: list[dict[str, Any]] = []
    for item in unique_exact:
        product_id = str(item["tcgplayer_product_id"])
        probe = probes[product_id]
        item["probe"] = probe
        item["image_url"] = probe["url"]
        item["product_exact_identity_count"] = len(product_to_exact[product_id])
        if probe.get("image_ok"):
            verified.append(item)

    metrics = {
        "all_v2_prints": len(normalized),
        "residual_v2_missing_images": len(residual),
        "residual_with_tcgplayer": len(with_tcgplayer),
        "residual_without_tcgplayer": len(residual) - len(with_tcgplayer),
        "unique_exact_identity_candidates": len(unique_exact),
        "shared_product_rejected": len(shared),
        "unique_products_probed": len(product_ids),
        "probe_status_counts": dict(Counter(str(probes[pid].get("status")) for pid in product_ids)),
        "verified_images": len(verified),
    }
    return verified, metrics


def _manifest_hash(items: list[dict[str, Any]]) -> str:
    lines = [
        "|".join(
            [
                str(item["print_id"]),
                str(item["exact_identity"]),
                str(item["tcgplayer_product_id"]),
                str(item["image_url"]),
            ]
        )
        for item in sorted(items, key=lambda row: int(row["print_id"]))
    ]
    return hashlib.sha256(("\n".join(lines) + "\n").encode("utf-8")).hexdigest()


def _apply(engine, verified: list[dict[str, Any]]) -> tuple[int, int]:
    inserted = 0
    skipped_existing = 0
    with engine.begin() as conn:
        for item in sorted(verified, key=lambda row: int(row["print_id"])):
            locked = conn.execute(
                text(
                    """
                    SELECT p.id,p.variant,p.language,p.tcgdex_id,g.slug AS game_slug,
                           pa.source AS attributes_source,pa.source_version,pa.attributes_json,
                           (SELECT pi.external_id
                            FROM print_identifiers pi
                            WHERE pi.print_id=p.id AND pi.source='tcgdex-variant-v2'
                            LIMIT 1) AS exact_variant_id
                    FROM prints p
                    JOIN cards c ON c.id=p.card_id
                    JOIN games g ON g.id=c.game_id
                    JOIN print_attributes pa ON pa.print_id=p.id
                    WHERE p.id=:print_id
                    FOR UPDATE OF p
                    """
                ),
                {"print_id": int(item["print_id"])},
            ).mappings().one_or_none()
            if locked is None:
                raise RuntimeError(f"Print disappeared during apply: {item['print_id']}")
            locked_dict = dict(locked)
            if locked_dict["game_slug"] != "pokemon" or locked_dict["language"] != "en":
                raise RuntimeError(f"Print scope changed during apply: {locked_dict}")
            exact_identity, _source_id, _physical, product_id = _normalize_exact_identity(locked_dict)
            if exact_identity != item["exact_identity"]:
                raise RuntimeError(
                    f"Exact physical identity changed for print {item['print_id']}: "
                    f"expected={item['exact_identity']} actual={exact_identity}"
                )
            if str(product_id or "") != str(item["tcgplayer_product_id"]):
                raise RuntimeError(
                    f"TCGplayer reference changed for print {item['print_id']}: "
                    f"expected={item['tcgplayer_product_id']} actual={product_id}"
                )

            existing = conn.execute(
                text("SELECT id FROM print_images WHERE print_id=:print_id LIMIT 1"),
                {"print_id": int(item["print_id"])},
            ).scalar_one_or_none()
            if existing is not None:
                skipped_existing += 1
                continue

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
                    "url": item["image_url"],
                    "source": SOURCE_LABEL,
                },
            ).scalar_one()
            if not created:
                raise RuntimeError(f"PrintImage insert returned no id for print {item['print_id']}")
            inserted += 1
    return inserted, skipped_existing


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--require-baseline", action="store_true")
    args = parser.parse_args()

    engine = create_engine(os.environ["DATABASE_URL_UNPOOLED"], pool_pre_ping=True)
    if not args.apply:
        with engine.connect() as conn:
            conn.execute(text("SET TRANSACTION READ ONLY"))
            if conn.execute(text("SHOW transaction_read_only")).scalar_one() != "on":
                raise RuntimeError("Dry-run connection is not read-only")
            conn.rollback()

    rows = _load_v2_rows(engine)
    verified, metrics = _build_candidates(rows)

    if args.require_baseline:
        required = {
            "residual_v2_missing_images": EXPECTED_RESIDUAL,
            "residual_with_tcgplayer": EXPECTED_WITH_TCGPLAYER,
            "unique_exact_identity_candidates": EXPECTED_UNIQUE_EXACT,
        }
        for key, expected in required.items():
            actual = int(metrics[key])
            if actual != expected:
                raise RuntimeError(f"Baseline drift for {key}: expected={expected} actual={actual}")

    inserted = 0
    skipped_existing = 0
    if args.apply:
        inserted, skipped_existing = _apply(engine, verified)

    manifest = [
        {
            "print_id": int(item["print_id"]),
            "tcgdex_id": item.get("tcgdex_id"),
            "card_name": item.get("card_name"),
            "set_code": item.get("set_code"),
            "collector_number": item.get("collector_number"),
            "variant": item.get("variant"),
            "exact_variant_id": item["exact_identity"],
            "tcgplayer_product_id": str(item["tcgplayer_product_id"]),
            "image_url": item["image_url"],
            "source": SOURCE_LABEL,
            "source_version": SOURCE_VERSION,
            "probe": item.get("probe"),
        }
        for item in sorted(verified, key=lambda row: int(row["print_id"]))
    ]
    report = {
        "status": "pass",
        "mode": "apply" if args.apply else "dry-run",
        "source_version": SOURCE_VERSION,
        "source_label": SOURCE_LABEL,
        **metrics,
        "verified_manifest_sha256": _manifest_hash(verified),
        "production_writes": inserted,
        "skipped_existing_during_apply": skipped_existing,
        "manifest": manifest,
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False, default=str) + "\n")
    print(json.dumps({k: v for k, v in report.items() if k != "manifest"}, indent=2, sort_keys=True, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
