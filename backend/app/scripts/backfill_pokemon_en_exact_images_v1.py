from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, text

from app.scripts.audit_pokemon_tcgdex_source_marketplace_v1 import (
    EXPECTED_TCGDEX_SHA,
    TCGDEX_REPO,
    _build_set_index,
    _git_head,
    _probe_product,
    _resolve_source_file,
    _source_identity,
    _strict_candidate,
)

OUTPUT = Path(
    os.environ.get(
        "POKEMON_EN_IMAGE_BACKFILL_OUTPUT",
        "artifacts/pokemon-en-exact-images-v1.json",
    )
)
SOURCE_LABEL = "tcgplayer:tcgdex-source"
EXPECTED_BASELINE_MISSING = 1558
EXPECTED_BASELINE_BASE_CANDIDATES = 388
EXPECTED_BASELINE_MISSING_1TO1 = 384


def _load_missing_rows(engine) -> list[dict[str, Any]]:
    with engine.connect() as conn:
        rows = [
            dict(row)
            for row in conn.execute(
                text(
                    """
                    SELECT p.id AS print_id,p.tcgdex_id,p.collector_number,p.variant,
                           p.is_foil,p.rarity,p.print_key,c.name AS card_name,s.code AS set_code
                    FROM prints p
                    JOIN cards c ON c.id=p.card_id
                    JOIN games g ON g.id=c.game_id
                    LEFT JOIN sets s ON s.id=p.set_id
                    WHERE g.slug='pokemon' AND p.language='en'
                      AND p.tcgdex_id IS NOT NULL
                      AND NOT EXISTS (SELECT 1 FROM print_images pi WHERE pi.print_id=p.id)
                    ORDER BY p.id
                    """
                )
            ).mappings().all()
        ]
        conn.rollback()
    return rows


def _global_tcgplayer_occurrences(data_root: Path) -> Counter[int]:
    counts: Counter[int] = Counter()
    pattern = re.compile(r"\btcgplayer\s*:\s*(\d+)\b")
    for path in data_root.rglob("*.ts"):
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for match in pattern.finditer(content):
            counts[int(match.group(1))] += 1
    return counts


def _probe_product_reliable(product_id: int) -> dict[str, Any]:
    last: dict[str, Any] = {}
    for attempt in range(4):
        result = _probe_product(product_id)
        result["attempt"] = attempt + 1
        last = result
        status = result.get("status")
        content_type = str(result.get("content_type") or "")
        if status == 200 and content_type.startswith("image/"):
            return result
        if status not in {403, 429, 500, 502, 503, 504}:
            return result
        time.sleep(0.35 * (attempt + 1))
    return last


def _manifest_hash(items: list[dict[str, Any]]) -> str:
    lines = [
        "|".join(
            [
                str(item["print_id"]),
                str(item["tcgdex_id"]),
                str(item["tcgplayer_product_id"]),
                str(item["image_url"]),
            ]
        )
        for item in sorted(items, key=lambda row: int(row["print_id"]))
    ]
    return hashlib.sha256(("\n".join(lines) + "\n").encode("utf-8")).hexdigest()


def _build_candidates(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if _git_head(TCGDEX_REPO) != EXPECTED_TCGDEX_SHA:
        raise RuntimeError(
            f"TCGdex source SHA mismatch: expected={EXPECTED_TCGDEX_SHA} actual={_git_head(TCGDEX_REPO)}"
        )

    set_index, duplicate_set_ids = _build_set_index(TCGDEX_REPO / "data")
    if duplicate_set_ids:
        raise RuntimeError(f"Duplicate TCGdex source set IDs: {duplicate_set_ids}")

    upstream_product_occurrences = _global_tcgplayer_occurrences(TCGDEX_REPO / "data")
    audited: list[dict[str, Any]] = []
    for row in rows:
        tcgdex_id = str(row["tcgdex_id"])
        set_id, local_id, card_file = _resolve_source_file(tcgdex_id, set_index)
        item = dict(row)
        item.update(
            {
                "source_set_id": set_id,
                "source_local_id": local_id,
                "source_file": str(card_file.relative_to(TCGDEX_REPO)) if card_file else None,
            }
        )
        source = (
            _source_identity(card_file)
            if card_file is not None
            else {"parse_status": "source_file_not_found"}
        )
        item["source"] = source
        strict, reason = _strict_candidate(item, source)
        item["strict_candidate"] = strict
        item["strict_candidate_reason"] = reason
        # V1 writes ONLY source base products onto canonical default/nonfoil Prints.
        # Detailed source variants remain blocked even if another gate could match them.
        writer_candidate = bool(strict and reason == "base_product_matches_default_nonfoil")
        item["writer_candidate"] = writer_candidate
        product_ids = source.get("tcgplayer_ids") or []
        item["tcgplayer_product_id"] = int(product_ids[0]) if writer_candidate else None
        audited.append(item)

    writer_candidates = [item for item in audited if item["writer_candidate"]]
    missing_product_to_prints: Counter[int] = Counter(
        int(item["tcgplayer_product_id"]) for item in writer_candidates
    )
    for item in writer_candidates:
        product_id = int(item["tcgplayer_product_id"])
        item["missing_product_1to1"] = missing_product_to_prints[product_id] == 1
        item["upstream_product_occurrences"] = upstream_product_occurrences[product_id]
        item["upstream_product_unique"] = upstream_product_occurrences[product_id] == 1

    missing_1to1 = [item for item in writer_candidates if item["missing_product_1to1"]]
    source_unique = [
        item
        for item in missing_1to1
        if item["upstream_product_unique"]
    ]

    product_ids = sorted({int(item["tcgplayer_product_id"]) for item in source_unique})
    probes: dict[int, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(_probe_product_reliable, product_id): product_id for product_id in product_ids}
        for future in as_completed(futures):
            probes[futures[future]] = future.result()

    verified: list[dict[str, Any]] = []
    for item in source_unique:
        product_id = int(item["tcgplayer_product_id"])
        probe = probes.get(product_id) or {}
        item["probe"] = probe
        item["image_url"] = f"https://tcgplayer-cdn.tcgplayer.com/product/{product_id}_in_1000x1000.jpg"
        if probe.get("status") == 200 and str(probe.get("content_type") or "").startswith("image/"):
            verified.append(item)

    metrics = {
        "tcgdex_set_index_count": len(set_index),
        "source_file_found": sum(1 for item in audited if item.get("source_file")),
        "source_file_missing": sum(1 for item in audited if not item.get("source_file")),
        "strict_reason_counts": dict(Counter(item["strict_candidate_reason"] for item in audited).most_common()),
        "writer_base_candidates": len(writer_candidates),
        "missing_product_1to1": len(missing_1to1),
        "upstream_globally_unique": len(source_unique),
        "upstream_nonunique_rejected": len(missing_1to1) - len(source_unique),
        "probe_status_counts": dict(Counter(str((item.get("probe") or {}).get("status")) for item in source_unique)),
        "verified_images": len(verified),
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
                    SELECT p.id,p.tcgdex_id,p.language,g.slug AS game_slug
                    FROM prints p
                    JOIN cards c ON c.id=p.card_id
                    JOIN games g ON g.id=c.game_id
                    WHERE p.id=:print_id
                    FOR UPDATE OF p
                    """
                ),
                {"print_id": int(item["print_id"])},
            ).mappings().one_or_none()
            if locked is None:
                raise RuntimeError(f"Print disappeared during image apply: {item['print_id']}")
            if locked["game_slug"] != "pokemon" or locked["language"] != "en":
                raise RuntimeError(f"Print scope changed during image apply: {dict(locked)}")
            if str(locked["tcgdex_id"] or "") != str(item["tcgdex_id"]):
                raise RuntimeError(
                    f"TCGdex identity changed for print {item['print_id']}: "
                    f"expected={item['tcgdex_id']} actual={locked['tcgdex_id']}"
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

    rows = _load_missing_rows(engine)
    verified, metrics = _build_candidates(rows)

    if args.require_baseline:
        if len(rows) != EXPECTED_BASELINE_MISSING:
            raise RuntimeError(
                f"Baseline drift: missing exact EN expected={EXPECTED_BASELINE_MISSING} actual={len(rows)}"
            )
        if metrics["writer_base_candidates"] != EXPECTED_BASELINE_BASE_CANDIDATES:
            raise RuntimeError(
                "Baseline drift: base candidates "
                f"expected={EXPECTED_BASELINE_BASE_CANDIDATES} actual={metrics['writer_base_candidates']}"
            )
        if metrics["missing_product_1to1"] != EXPECTED_BASELINE_MISSING_1TO1:
            raise RuntimeError(
                "Baseline drift: missing-product 1:1 "
                f"expected={EXPECTED_BASELINE_MISSING_1TO1} actual={metrics['missing_product_1to1']}"
            )

    inserted = 0
    skipped_existing = 0
    if args.apply:
        inserted, skipped_existing = _apply(engine, verified)

    manifest = [
        {
            "print_id": int(item["print_id"]),
            "tcgdex_id": str(item["tcgdex_id"]),
            "card_name": item.get("card_name"),
            "set_code": item.get("set_code"),
            "collector_number": item.get("collector_number"),
            "tcgplayer_product_id": int(item["tcgplayer_product_id"]),
            "image_url": item["image_url"],
            "source": SOURCE_LABEL,
            "probe": item.get("probe"),
        }
        for item in sorted(verified, key=lambda row: int(row["print_id"]))
    ]
    summary = {
        "status": "pass",
        "mode": "apply" if args.apply else "dry-run",
        "tcgdex_source_sha": _git_head(TCGDEX_REPO),
        "missing_en_exact_before": len(rows),
        **metrics,
        "verified_manifest_sha256": _manifest_hash(manifest),
        "production_writes": inserted,
        "skipped_existing_during_apply": skipped_existing,
        "source_label": SOURCE_LABEL,
    }
    report = {**summary, "manifest": manifest}
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False, default=str) + "\n")
    print(json.dumps(summary, indent=2, sort_keys=True, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
