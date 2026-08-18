from __future__ import annotations

import argparse
import json
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from app.jobs.cardmarket_master_inventory import ProductListRow, load_catalog_feed_bytes
from app.jobs.cardmarket_public_catalog_sync_v1 import CARDMARKET_GAME_IDS, catalog_url


IMAGE_BASE = "https://product-images.s3.cardmarket.com"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


@dataclass(frozen=True)
class RegionalAnchorSet:
    key: str
    game_slug: str
    expansion_code: str
    region: str
    official_expansion_url: str
    anchors: tuple[str, ...]
    min_confirmations: int = 2


REGIONAL_ANCHORS: tuple[RegionalAnchorSet, ...] = (
    RegionalAnchorSet(
        key="yugioh_agov_jp",
        game_slug="yugioh",
        expansion_code="AGOV-JP",
        region="ocg_japan",
        official_expansion_url="https://www.cardmarket.com/en/YuGiOh/Products/Singles/Age-of-Overlord-OCG",
        anchors=(
            "Arias the Labrynth Butler (V.1 - Super Rare)",
            "Xyz Armor Fortress",
            "Card Scanner",
        ),
    ),
    RegionalAnchorSet(
        key="onepiece_op16_jp",
        game_slug="onepiece",
        expansion_code="OP16-JP",
        region="asia_region_legal",
        official_expansion_url="https://www.cardmarket.com/en/OnePiece/Products/Singles/The-Time-of-Battle-Asia-Region-Legal",
        anchors=(
            "Roronoa Zoro (OP16-035)",
            "Doc Q (OP16-109)",
            "Portgas.D.Ace (OP16-094)",
        ),
    ),
)


def _normalise_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").casefold())


def find_anchor_candidates(rows: tuple[ProductListRow, ...], anchor: str) -> list[ProductListRow]:
    target = _normalise_name(anchor)
    exact = [row for row in rows if _normalise_name(row.name) == target]
    if exact:
        return exact
    # Cardmarket sometimes duplicates the visible version suffix in rendered UI
    # while Product Catalog keeps a shorter name. Only allow prefix fallback when
    # it still has meaningful specificity.
    if len(target) < 12:
        return []
    return [
        row
        for row in rows
        if _normalise_name(row.name).startswith(target) or target.startswith(_normalise_name(row.name))
    ]


def image_url(row: ProductListRow, expansion_code: str) -> str:
    category_id = str(row.category_id or "").strip()
    if not category_id:
        raise ValueError(f"Cardmarket product {row.product_id} has no idCategory")
    product_id = str(row.product_id)
    return f"{IMAGE_BASE}/{category_id}/{expansion_code}/{product_id}/{product_id}.jpg"


def probe_jpeg(url: str, *, timeout: int = 25, attempts: int = 2) -> dict:
    last_status = None
    for attempt in range(1, attempts + 1):
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": USER_AGENT,
                "Referer": "https://www.cardmarket.com/",
                "Accept": "image/avif,image/webp,image/*,*/*;q=0.8",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                status = int(getattr(response, "status", 200) or 200)
                body = response.read(64)
                return {
                    "status": status,
                    "jpeg": body[:2] == b"\xff\xd8",
                    "content_type": response.headers.get("Content-Type"),
                }
        except urllib.error.HTTPError as exc:
            last_status = int(exc.code)
            if exc.code == 403 and attempt < attempts:
                time.sleep(2 * attempt)
                continue
            return {"status": int(exc.code), "jpeg": False, "content_type": exc.headers.get("Content-Type") if exc.headers else None}
        except Exception as exc:  # network failures are evidence of nothing
            if attempt < attempts:
                time.sleep(2 * attempt)
                continue
            return {"status": last_status, "jpeg": False, "error": f"{type(exc).__name__}: {exc}"}
    return {"status": last_status, "jpeg": False}


def certify_anchor_set(anchor_set: RegionalAnchorSet, rows: tuple[ProductListRow, ...]) -> dict:
    anchor_reports = []
    confirmed_rows: list[ProductListRow] = []
    any_403 = False

    for anchor in anchor_set.anchors:
        candidates = find_anchor_candidates(rows, anchor)
        probes = []
        for row in candidates:
            url = image_url(row, anchor_set.expansion_code)
            result = probe_jpeg(url)
            any_403 = any_403 or result.get("status") == 403
            probe = {
                "product_id": str(row.product_id),
                "product_name": row.name,
                "expansion_id": str(row.expansion_id or ""),
                "category_id": str(row.category_id or ""),
                "image_url": url,
                **result,
            }
            probes.append(probe)
            if result.get("jpeg"):
                confirmed_rows.append(row)
            time.sleep(0.35)
        anchor_reports.append({"anchor": anchor, "candidate_count": len(candidates), "probes": probes})

    confirmed_product_ids = sorted({str(row.product_id) for row in confirmed_rows})
    confirmed_expansion_ids = sorted({str(row.expansion_id or "") for row in confirmed_rows if str(row.expansion_id or "")})
    confirmed_anchor_count = sum(
        1 for report in anchor_reports if any(bool(probe.get("jpeg")) for probe in report["probes"])
    )

    status = "certified"
    reason = None
    if len(confirmed_expansion_ids) > 1:
        status = "conflict"
        reason = "candidate expansion code resolved JPEGs under multiple Cardmarket idExpansion values"
    elif confirmed_anchor_count < anchor_set.min_confirmations:
        status = "inconclusive"
        reason = (
            "Cardmarket image evidence did not reach the required independent-anchor threshold; "
            "403/network failures must never be treated as a negative identity result"
            if any_403
            else "not enough independent anchors resolved as JPEGs"
        )
    elif len(confirmed_expansion_ids) != 1:
        status = "inconclusive"
        reason = "JPEG evidence did not resolve exactly one non-empty Cardmarket idExpansion"

    return {
        "key": anchor_set.key,
        "game": anchor_set.game_slug,
        "cardmarket_game_id": CARDMARKET_GAME_IDS[anchor_set.game_slug],
        "candidate_expansion_code": anchor_set.expansion_code,
        "region": anchor_set.region,
        "official_expansion_url": anchor_set.official_expansion_url,
        "required_anchor_confirmations": anchor_set.min_confirmations,
        "confirmed_anchor_count": confirmed_anchor_count,
        "confirmed_product_ids": confirmed_product_ids,
        "confirmed_expansion_ids": confirmed_expansion_ids,
        "status": status,
        "reason": reason,
        "anchors": anchor_reports,
    }


def _download_singles(game_slug: str) -> tuple[ProductListRow, ...]:
    url = catalog_url(game_slug, "single")
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=180) as response:
        content = response.read()
    feed = load_catalog_feed_bytes(content, game_slug=game_slug, product_group="single")
    if feed.rejected_records or not feed.rows:
        raise RuntimeError(
            f"Cardmarket source failed accounting for {game_slug}: raw={feed.raw_records} "
            f"accepted={len(feed.rows)} rejected={feed.rejected_records}"
        )
    return feed.rows


def run_audit(keys: set[str] | None = None) -> dict:
    selected = [anchor_set for anchor_set in REGIONAL_ANCHORS if not keys or anchor_set.key in keys]
    if keys:
        missing = sorted(keys - {anchor_set.key for anchor_set in selected})
        if missing:
            raise ValueError(f"Unknown regional anchor keys: {missing}")
    rows_by_game = {game: _download_singles(game) for game in sorted({item.game_slug for item in selected})}
    reports = [certify_anchor_set(item, rows_by_game[item.game_slug]) for item in selected]
    return {
        "source": "cardmarket",
        "mode": "read_only",
        "method": "product_catalog_candidate_plus_cardmarket_image_s3_jpeg",
        "certified": sum(report["status"] == "certified" for report in reports),
        "conflicts": sum(report["status"] == "conflict" for report in reports),
        "inconclusive": sum(report["status"] == "inconclusive" for report in reports),
        "results": reports,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="READ ONLY certify Cardmarket regional idExpansion identities")
    parser.add_argument("--key", action="append", default=[], help="Audit only a named anchor set; repeatable")
    parser.add_argument("--report", type=Path)
    parser.add_argument("--require-certified", action="store_true")
    args = parser.parse_args()

    payload = run_audit(set(args.key) or None)
    rendered = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(rendered, encoding="utf-8")
    print(rendered, end="")

    if payload["conflicts"]:
        return 2
    if args.require_certified and payload["inconclusive"]:
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
