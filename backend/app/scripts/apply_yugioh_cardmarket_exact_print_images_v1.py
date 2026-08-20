from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import urllib.error
import urllib.request
from pathlib import Path

import psycopg2
from PIL import Image
from psycopg2.extras import RealDictCursor

GAME = "yugioh"
SOURCE = "cardmarket_exact_product_image_v1"
ACCEPTED = ("accepted", "mapped", "exact")
EXPECTED_ROWS = 16
CONFIRM = "APPLY_YUGIOH_CARDMARKET_EXACT_PRINT_IMAGES_V1"
MANIFEST = Path(__file__).resolve().parents[1] / "data" / "yugioh_cardmarket_exact_print_images_v1.csv"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


def _connect(*, readonly: bool):
    url = os.getenv("DATABASE_URL_UNPOOLED") or os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL_UNPOOLED or DATABASE_URL is required")
    conn = psycopg2.connect(
        url,
        connect_timeout=30,
        application_name="dontripit_ygo_exact_cardmarket_images_v1",
    )
    conn.set_session(readonly=readonly, autocommit=False)
    return conn


def _load_manifest() -> list[dict]:
    with MANIFEST.open("r", encoding="utf-8", newline="") as handle:
        rows = [dict(row) for row in csv.DictReader(handle)]
    if len(rows) != EXPECTED_ROWS:
        raise RuntimeError({"manifest_count_drift": {"expected": EXPECTED_ROWS, "actual": len(rows)}})

    for row in rows:
        row["print_id"] = int(row["print_id"])
        row["width"] = int(row["width"])
        row["height"] = int(row["height"])
        for key in ("card_name", "collector_number", "language", "rarity", "variant", "id_product",
                    "category_id", "expansion_external_id", "directory_token", "url", "sha256", "format"):
            row[key] = str(row.get(key) or "").strip()
        expected_url = (
            "https://product-images.s3.cardmarket.com/"
            f"{row['category_id']}/{row['directory_token']}/{row['id_product']}/{row['id_product']}.jpg"
        )
        if row["url"] != expected_url:
            raise RuntimeError({"manifest_url_shape_drift": {"print_id": row["print_id"], "url": row["url"]}})
        if len(row["sha256"]) != 64 or any(ch not in "0123456789abcdef" for ch in row["sha256"]):
            raise RuntimeError({"manifest_sha256_invalid": row["print_id"]})
        if row["language"].lower() != "ja":
            raise RuntimeError({"manifest_non_ja_row": row["print_id"]})
        if row["format"].upper() != "JPEG":
            raise RuntimeError({"manifest_non_jpeg_row": row["print_id"]})

    unique_fields = ("print_id", "id_product", "url", "sha256")
    for field in unique_fields:
        values = [str(row[field]) for row in rows]
        if len(values) != len(set(values)):
            raise RuntimeError({"manifest_duplicate": field})
    return rows


def _download_and_verify(row: dict, timeout: int = 30) -> dict:
    req = urllib.request.Request(
        row["url"],
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "image/avif,image/webp,image/png,image/jpeg,image/*;q=0.8,*/*;q=0.1",
            "Referer": "https://www.cardmarket.com/",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            body = response.read()
            status = int(getattr(response, "status", 200) or 200)
            content_type = str(response.headers.get("Content-Type") or "")
    except urllib.error.HTTPError as exc:
        raise RuntimeError({"image_http_error": {"print_id": row["print_id"], "status": exc.code}}) from exc
    except Exception as exc:
        raise RuntimeError({"image_request_error": {"print_id": row["print_id"], "error": str(exc)}}) from exc

    actual_sha = hashlib.sha256(body).hexdigest()
    if status != 200 or actual_sha != row["sha256"]:
        raise RuntimeError({
            "image_bytes_drift": {
                "print_id": row["print_id"],
                "status": status,
                "expected_sha256": row["sha256"],
                "actual_sha256": actual_sha,
            }
        })
    try:
        with Image.open(io.BytesIO(body)) as image:
            image.verify()
        with Image.open(io.BytesIO(body)) as image:
            width, height = image.size
            image_format = str(image.format or "").upper()
    except Exception as exc:
        raise RuntimeError({"image_decode_error": {"print_id": row["print_id"], "error": str(exc)}}) from exc
    if (width, height, image_format) != (row["width"], row["height"], row["format"].upper()):
        raise RuntimeError({
            "image_geometry_drift": {
                "print_id": row["print_id"],
                "expected": [row["width"], row["height"], row["format"].upper()],
                "actual": [width, height, image_format],
            }
        })
    return {
        "status": status,
        "content_type": content_type,
        "bytes": len(body),
        "sha256": actual_sha,
        "width": width,
        "height": height,
        "format": image_format,
    }


def _current_capture(cur):
    cur.execute("SELECT max(last_seen_at) AS capture FROM external_catalog_products WHERE source='cardmarket'")
    capture = cur.fetchone()["capture"]
    if capture is None:
        raise RuntimeError("Current Cardmarket capture missing")
    return capture


def _game_id(cur) -> int:
    cur.execute("SELECT id FROM games WHERE slug=%s LIMIT 1", (GAME,))
    row = cur.fetchone()
    if not row:
        raise RuntimeError("Yu-Gi-Oh game row missing")
    return int(row["id"])


def _validate_identity(cur, row: dict, *, game_id: int, capture) -> dict:
    cur.execute(
        """
        SELECT p.id AS print_id,p.card_id,p.collector_number,p.language,p.rarity,p.variant,
               c.name AS card_name,s.code AS set_code,g.slug AS game_slug
        FROM prints p
        JOIN cards c ON c.id=p.card_id
        JOIN games g ON g.id=c.game_id
        JOIN sets s ON s.id=p.set_id
        WHERE p.id=%s
        """,
        (row["print_id"],),
    )
    pr = cur.fetchone()
    if not pr:
        raise RuntimeError({"canonical_print_missing": row["print_id"]})
    checks = {
        "game": str(pr["game_slug"]) == GAME,
        "card_name": str(pr["card_name"]) == row["card_name"],
        "collector_number": str(pr["collector_number"]) == row["collector_number"],
        "language": str(pr["language"] or "").lower() == row["language"].lower(),
        "rarity": str(pr["rarity"] or "") == row["rarity"],
        "variant": str(pr["variant"] or "") == row["variant"],
    }
    failed = [key for key, ok in checks.items() if not ok]
    if failed:
        raise RuntimeError({"canonical_identity_drift": {"print_id": row["print_id"], "failed": failed, "actual": dict(pr)}})

    cur.execute(
        """
        SELECT id,external_id,name,category_id,expansion_external_id,metacard_external_id,last_seen_at
        FROM external_catalog_products
        WHERE source='cardmarket' AND game_id=%s AND product_group='single'
          AND external_id=%s AND last_seen_at=%s
        ORDER BY id
        """,
        (game_id, row["id_product"], capture),
    )
    products = [dict(r) for r in cur.fetchall()]
    if len(products) != 1:
        raise RuntimeError({"current_cardmarket_product_surface_drift": {"id_product": row["id_product"], "count": len(products)}})
    product = products[0]
    if str(product.get("category_id") or "") != row["category_id"]:
        raise RuntimeError({"cardmarket_category_drift": row["id_product"]})
    if str(product.get("expansion_external_id") or "") != row["expansion_external_id"]:
        raise RuntimeError({"cardmarket_expansion_drift": row["id_product"]})

    cur.execute(
        """
        SELECT l.print_id,l.link_status,l.mapping_method,l.confidence,l.reviewed
        FROM external_catalog_print_links l
        WHERE l.external_product_id=%s AND l.link_status=ANY(%s)
        ORDER BY l.id
        """,
        (int(product["id"]), list(ACCEPTED)),
    )
    product_claims = [dict(r) for r in cur.fetchall()]
    if len(product_claims) != 1:
        raise RuntimeError({"product_claim_count_drift": {"id_product": row["id_product"], "claims": product_claims}})
    claim = product_claims[0]
    if int(claim["print_id"]) != row["print_id"] or str(claim.get("confidence") or "") != "exact" or not bool(claim.get("reviewed")):
        raise RuntimeError({"product_claim_identity_drift": {"id_product": row["id_product"], "claim": claim}})

    cur.execute(
        """
        SELECT e.external_id AS id_product,l.link_status,l.mapping_method,l.confidence,l.reviewed
        FROM external_catalog_print_links l
        JOIN external_catalog_products e ON e.id=l.external_product_id
        WHERE l.print_id=%s AND l.link_status=ANY(%s)
          AND e.source='cardmarket' AND e.game_id=%s AND e.product_group='single' AND e.last_seen_at=%s
        ORDER BY e.external_id
        """,
        (row["print_id"], list(ACCEPTED), game_id, capture),
    )
    print_claims = [dict(r) for r in cur.fetchall()]
    if len(print_claims) != 1 or str(print_claims[0]["id_product"]) != row["id_product"]:
        raise RuntimeError({"print_claim_count_drift": {"print_id": row["print_id"], "claims": print_claims}})

    cur.execute(
        "SELECT id,url,is_primary,source FROM print_images WHERE print_id=%s ORDER BY id",
        (row["print_id"],),
    )
    images = [dict(r) for r in cur.fetchall()]
    if not images:
        image_state = "missing"
    elif len(images) == 1 and images[0]["url"] == row["url"] and str(images[0].get("source") or "") == SOURCE:
        image_state = "already_present"
    else:
        raise RuntimeError({"print_image_conflict": {"print_id": row["print_id"], "images": images}})

    return {
        "print_id": row["print_id"],
        "id_product": row["id_product"],
        "external_product_row_id": int(product["id"]),
        "mapping_method": claim.get("mapping_method"),
        "image_state": image_state,
    }


def run(*, apply: bool, report_path: Path) -> dict:
    manifest = _load_manifest()

    # Network evidence is revalidated before any database write. If a single
    # byte changes or a first-party image disappears, the whole run fails closed.
    network = {}
    for row in manifest:
        network[row["print_id"]] = _download_and_verify(row)

    conn = _connect(readonly=not apply)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            game_id = _game_id(cur)
            capture = _current_capture(cur)
            identities = [
                _validate_identity(cur, row, game_id=game_id, capture=capture)
                for row in manifest
            ]
            planned = [item for item in identities if item["image_state"] == "missing"]
            already = [item for item in identities if item["image_state"] == "already_present"]

            inserted = 0
            if apply:
                if os.getenv("CONFIRM_APPLY") != CONFIRM:
                    raise RuntimeError(f"CONFIRM_APPLY must equal {CONFIRM}")
                for item in planned:
                    row = next(r for r in manifest if r["print_id"] == item["print_id"])
                    cur.execute(
                        """
                        INSERT INTO print_images (print_id,url,is_primary,source)
                        VALUES (%s,%s,TRUE,%s)
                        """,
                        (row["print_id"], row["url"], SOURCE),
                    )
                    inserted += 1
                conn.commit()
            else:
                conn.rollback()

        # Post-state is checked in a new readonly transaction so report counts
        # reflect committed production state rather than the writer cursor.
        post_conn = _connect(readonly=True)
        try:
            with post_conn.cursor(cursor_factory=RealDictCursor) as cur:
                ids = [row["print_id"] for row in manifest]
                cur.execute(
                    """
                    SELECT print_id,url,source,is_primary
                    FROM print_images
                    WHERE print_id=ANY(%s)
                    ORDER BY print_id,id
                    """,
                    (ids,),
                )
                post_images = [dict(r) for r in cur.fetchall()]
                post_conn.rollback()
        finally:
            post_conn.close()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        conn.close()

    exact_post = [
        img for img in post_images
        if str(img.get("source") or "") == SOURCE
        and any(r["print_id"] == int(img["print_id"]) and r["url"] == img["url"] for r in manifest)
    ]
    report = {
        "status": "pass",
        "mode": "apply" if apply else "dry_run",
        "cardmarket_capture": str(capture),
        "manifest_rows": len(manifest),
        "network_verified": len(network),
        "identity_verified": len(identities),
        "planned_inserts": len(planned),
        "already_present": len(already),
        "inserted": inserted,
        "post_exact_images": len(exact_post),
        "production_writes": inserted,
        "source": SOURCE,
        "rows": [
            {
                **item,
                "url": next(r["url"] for r in manifest if r["print_id"] == item["print_id"]),
                "sha256": network[item["print_id"]]["sha256"],
            }
            for item in identities
        ],
    }
    expected_post = len(already) + (len(planned) if apply else 0)
    if report["post_exact_images"] != expected_post:
        raise RuntimeError({"post_state_accounting_drift": {"expected": expected_post, "report": report}})
    if len(identities) != EXPECTED_ROWS or len(network) != EXPECTED_ROWS:
        raise RuntimeError({"verification_accounting_drift": report})

    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True, default=str))
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Fail-closed exact Cardmarket YGO PrintImage writer V1")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    run(apply=args.apply, report_path=args.report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
