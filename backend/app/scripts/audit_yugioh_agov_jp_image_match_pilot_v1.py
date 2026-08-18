from __future__ import annotations

import argparse
import hashlib
import io
import itertools
import json
import os
import urllib.error
import urllib.request
from pathlib import Path

import psycopg2
from PIL import Image, ImageOps
from psycopg2.extras import RealDictCursor


EXPANSION_ID = "5421"
EXPANSION_CODE = "AGOV-JP"
SET_CODE = "AGOV"
IMAGE_BASE = "https://product-images.s3.cardmarket.com"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


def _connect():
    url = os.getenv("DATABASE_URL_UNPOOLED") or os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL_UNPOOLED or DATABASE_URL is required")
    conn = psycopg2.connect(url, connect_timeout=30, application_name="dontripit_ygo_agov_jp_image_match_pilot_v1")
    conn.set_session(readonly=True, autocommit=False)
    return conn


def _download(url: str, *, referer: str | None = None, timeout: int = 30) -> tuple[bytes | None, dict]:
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "image/avif,image/webp,image/png,image/jpeg,image/*;q=0.8,*/*;q=0.1",
    }
    if referer:
        headers["Referer"] = referer
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            body = response.read()
            return body, {
                "status": int(getattr(response, "status", 200) or 200),
                "content_type": response.headers.get("Content-Type"),
                "bytes": len(body),
                "sha256": hashlib.sha256(body).hexdigest(),
            }
    except urllib.error.HTTPError as exc:
        return None, {"status": int(exc.code), "error": f"HTTPError: {exc.code}"}
    except Exception as exc:
        return None, {"status": None, "error": f"{type(exc).__name__}: {exc}"}


def _prepare(body: bytes) -> Image.Image:
    image = Image.open(io.BytesIO(body))
    image = ImageOps.exif_transpose(image).convert("RGB")
    width, height = image.size
    bx = max(1, int(width * 0.015))
    by = max(1, int(height * 0.015))
    if width > bx * 2 and height > by * 2:
        image = image.crop((bx, by, width - bx, height - by))
    return image.resize((128, 176), Image.Resampling.LANCZOS)


def _ahash(image: Image.Image, size: int = 16) -> int:
    gray = image.convert("L").resize((size, size), Image.Resampling.LANCZOS)
    pixels = list(gray.getdata())
    avg = sum(pixels) / len(pixels)
    value = 0
    for pixel in pixels:
        value = (value << 1) | int(pixel >= avg)
    return value


def _dhash(image: Image.Image, size: int = 16) -> int:
    gray = image.convert("L").resize((size + 1, size), Image.Resampling.LANCZOS)
    pixels = list(gray.getdata())
    value = 0
    for y in range(size):
        row = y * (size + 1)
        for x in range(size):
            value = (value << 1) | int(pixels[row + x] >= pixels[row + x + 1])
    return value


def _hamming(a: int, b: int) -> int:
    return (a ^ b).bit_count()


def _pixel_mae(a: Image.Image, b: Image.Image) -> float:
    pa = list(a.getdata())
    pb = list(b.getdata())
    total = 0
    for left, right in zip(pa, pb):
        total += abs(left[0] - right[0]) + abs(left[1] - right[1]) + abs(left[2] - right[2])
    return total / (len(pa) * 3.0)


def _feature(body: bytes) -> dict:
    image = _prepare(body)
    return {"image": image, "ahash": _ahash(image), "dhash": _dhash(image)}


def _assignment_rankings(matrix: list[dict], products: list[str], prints: list[int], metric: str) -> list[dict]:
    lookup = {(str(r["idProduct"]), int(r["print_id"])): float(r[metric]) for r in matrix}
    rankings = []
    for permutation in itertools.permutations(prints):
        pairs = list(zip(products, permutation))
        if any((pid, print_id) not in lookup for pid, print_id in pairs):
            continue
        score = sum(lookup[(pid, print_id)] for pid, print_id in pairs)
        rankings.append(
            {
                "score": round(score, 4),
                "pairs": [{"idProduct": pid, "print_id": int(print_id)} for pid, print_id in pairs],
            }
        )
    rankings.sort(key=lambda r: (r["score"], [(p["idProduct"], p["print_id"]) for p in r["pairs"]]))
    return rankings


def _pair_signature(pairs: list[dict]) -> tuple[tuple[str, int], ...]:
    return tuple(sorted((str(p["idProduct"]), int(p["print_id"])) for p in pairs))


def main() -> int:
    parser = argparse.ArgumentParser(description="READ ONLY compare Cardmarket AGOV-JP product images to exact canonical print images")
    parser.add_argument("--card-name", required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()

    conn = _connect()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT id FROM games WHERE slug='yugioh' LIMIT 1")
            game_id = int(cur.fetchone()["id"])
            cur.execute("SELECT max(last_seen_at) AS ts FROM external_catalog_products WHERE source='cardmarket'")
            capture = cur.fetchone()["ts"]
            cur.execute(
                """
                SELECT e.id AS external_product_id,e.external_id AS id_product,e.name,e.category_id,e.metacard_external_id
                FROM external_catalog_products e
                WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single'
                  AND e.expansion_external_id=%s AND e.last_seen_at=%s AND e.name=%s
                ORDER BY e.external_id::bigint
                """,
                (game_id, EXPANSION_ID, capture, args.card_name),
            )
            products = [dict(r) for r in cur.fetchall()]
            cur.execute(
                """
                SELECT p.id AS print_id,p.collector_number,p.rarity,p.variant,p.print_key,c.name AS card_name,
                       pi.url AS image_url,pi.source AS image_source,pi.is_primary
                FROM prints p JOIN cards c ON c.id=p.card_id JOIN sets s ON s.id=p.set_id
                LEFT JOIN LATERAL (
                    SELECT x.url,x.source,x.is_primary FROM print_images x
                    WHERE x.print_id=p.id ORDER BY x.is_primary DESC,x.id ASC LIMIT 1
                ) pi ON true
                WHERE c.game_id=%s AND c.name=%s AND lower(coalesce(p.language,''))='ja'
                  AND upper(coalesce(s.code,''))=%s
                ORDER BY p.id
                """,
                (game_id, args.card_name, SET_CODE),
            )
            prints = [dict(r) for r in cur.fetchall()]
            conn.rollback()
    finally:
        conn.close()

    product_features = {}
    product_downloads = {}
    for row in products:
        pid = str(row["id_product"])
        category_id = str(row.get("category_id") or "5")
        url = f"{IMAGE_BASE}/{category_id}/{EXPANSION_CODE}/{pid}/{pid}.jpg"
        body, meta = _download(url, referer="https://www.cardmarket.com/")
        product_downloads[pid] = {"url": url, **meta}
        if body:
            try:
                product_features[pid] = _feature(body)
            except Exception as exc:
                product_downloads[pid]["decode_error"] = f"{type(exc).__name__}: {exc}"

    print_features = {}
    print_downloads = {}
    for row in prints:
        print_id = int(row["print_id"])
        url = row.get("image_url")
        if not url:
            print_downloads[str(print_id)] = {"url": None, "error": "missing canonical print image"}
            continue
        body, meta = _download(str(url))
        print_downloads[str(print_id)] = {"url": str(url), "source": row.get("image_source"), **meta}
        if body:
            try:
                print_features[print_id] = _feature(body)
            except Exception as exc:
                print_downloads[str(print_id)]["decode_error"] = f"{type(exc).__name__}: {exc}"

    matrix = []
    for product in products:
        pid = str(product["id_product"])
        pf = product_features.get(pid)
        if not pf:
            continue
        for print_row in prints:
            print_id = int(print_row["print_id"])
            cf = print_features.get(print_id)
            if not cf:
                continue
            matrix.append(
                {
                    "idProduct": pid,
                    "print_id": print_id,
                    "canonical_variant": print_row.get("variant"),
                    "canonical_rarity": print_row.get("rarity"),
                    "ahash_distance": _hamming(pf["ahash"], cf["ahash"]),
                    "dhash_distance": _hamming(pf["dhash"], cf["dhash"]),
                    "pixel_mae": round(_pixel_mae(pf["image"], cf["image"]), 4),
                }
            )

    product_ids = sorted(product_features, key=int)
    print_ids = sorted(print_features)
    metrics = ("pixel_mae", "dhash_distance", "ahash_distance")
    assignments = {}
    signatures = []
    for metric in metrics:
        rankings = _assignment_rankings(matrix, product_ids, print_ids, metric)
        best = rankings[0] if rankings else None
        second = rankings[1] if len(rankings) > 1 else None
        if best:
            signatures.append(_pair_signature(best["pairs"]))
        assignments[metric] = {
            "best": best,
            "second": second,
            "absolute_gap": round(second["score"] - best["score"], 4) if best and second else None,
            "relative_gap": round((second["score"] - best["score"]) / best["score"], 6) if best and second and best["score"] else None,
        }

    complete_surface = (
        bool(products)
        and len(products) == len(prints)
        and len(product_features) == len(products)
        and len(print_features) == len(prints)
    )
    assignment_consensus = bool(signatures) and len(signatures) == len(metrics) and len(set(signatures)) == 1
    min_relative_gap = min(
        (info["relative_gap"] for info in assignments.values() if info.get("relative_gap") is not None),
        default=None,
    )
    # This is still an audit-only candidate flag. Production mapping requires a separate apply gate.
    image_bijection_candidate = bool(complete_surface and assignment_consensus and min_relative_gap is not None and min_relative_gap >= 0.03)

    consensus_pairs = []
    if assignment_consensus and assignments["pixel_mae"]["best"]:
        lookup = {(str(r["idProduct"]), int(r["print_id"])): r for r in matrix}
        for pair in assignments["pixel_mae"]["best"]["pairs"]:
            row = lookup[(str(pair["idProduct"]), int(pair["print_id"]))]
            consensus_pairs.append(
                {
                    "idProduct": str(pair["idProduct"]),
                    "print_id": int(pair["print_id"]),
                    "canonical_variant": row["canonical_variant"],
                    "canonical_rarity": row["canonical_rarity"],
                }
            )

    report = {
        "status": "pass",
        "production_writes": 0,
        "card_name": args.card_name,
        "cardmarket_capture": str(capture),
        "products": [
            {"idProduct": str(r["id_product"]), "idMetacard": str(r.get("metacard_external_id") or ""), "name": r["name"]}
            for r in products
        ],
        "prints": [
            {"print_id": int(r["print_id"]), "collector_number": r["collector_number"], "rarity": r["rarity"], "variant": r["variant"], "image_source": r.get("image_source")}
            for r in prints
        ],
        "product_downloads": product_downloads,
        "canonical_downloads": print_downloads,
        "matrix": matrix,
        "complete_product_images": len(product_features),
        "complete_canonical_images": len(print_features),
        "complete_surface": complete_surface,
        "assignment_by_metric": assignments,
        "assignment_consensus": assignment_consensus,
        "minimum_relative_assignment_gap": min_relative_gap,
        "image_bijection_candidate": image_bijection_candidate,
        "consensus_pairs": consensus_pairs,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
