from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import re
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path

from PIL import Image

IMAGE_BASE = "https://product-images.s3.cardmarket.com"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


def _directory_token(collector: str) -> str | None:
    value = str(collector or "").strip()
    if not value:
        return None
    # Japanese physical collectors in the certified corpus are either
    # SET-JP### (directory SET-JP) or SET-### (directory SET).
    match = re.fullmatch(r"(.+?JP)(\d{3})", value, flags=re.IGNORECASE)
    if match:
        return match.group(1).upper()
    match = re.fullmatch(r"(.+?)-(\d{3})", value)
    if match:
        return match.group(1).upper()
    return None


def _download(url: str, timeout: int = 30) -> tuple[bytes | None, dict]:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "image/avif,image/webp,image/png,image/jpeg,image/*;q=0.8,*/*;q=0.1",
            "Referer": "https://www.cardmarket.com/",
        },
    )
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


def main() -> int:
    parser = argparse.ArgumentParser(description="READ ONLY probe exact Cardmarket product images for certified YGO print identities")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--csv", type=Path, required=True)
    args = parser.parse_args()

    with args.input.open("r", encoding="utf-8", newline="") as handle:
        rows = [dict(row) for row in csv.DictReader(handle)]

    results = []
    for row in rows:
        token = _directory_token(row.get("collector_number") or "")
        pid = str(row.get("id_product") or "").strip()
        category = str(row.get("category_id") or "").strip()
        url = f"{IMAGE_BASE}/{category}/{token}/{pid}/{pid}.jpg" if token and pid and category else None
        result = {
            "print_id": int(row["print_id"]),
            "card_name": row.get("card_name"),
            "collector_number": row.get("collector_number"),
            "language": row.get("language"),
            "rarity": row.get("rarity"),
            "variant": row.get("variant"),
            "id_product": pid,
            "category_id": category,
            "expansion_external_id": row.get("expansion_external_id"),
            "directory_token": token,
            "url": url,
            "identity_confidence": row.get("confidence"),
            "identity_reviewed": row.get("reviewed"),
        }
        if not url:
            result.update({"probe": "unresolved_directory", "valid_image": False})
            results.append(result)
            continue

        body, meta = _download(url)
        result.update(meta)
        valid = False
        if body:
            try:
                with Image.open(io.BytesIO(body)) as image:
                    image.verify()
                with Image.open(io.BytesIO(body)) as image:
                    result["width"], result["height"] = image.size
                    result["format"] = image.format
                valid = bool(result.get("width") and result.get("height"))
            except Exception as exc:
                result["decode_error"] = f"{type(exc).__name__}: {exc}"
        result["valid_image"] = valid
        result["probe"] = "resolved" if valid else "not_resolved"
        results.append(result)

    counts = Counter(str(r["probe"]) for r in results)
    valid = [r for r in results if r["valid_image"]]
    unique_urls = len({str(r["url"]) for r in valid})
    unique_hashes = len({str(r.get("sha256") or "") for r in valid})

    report = {
        "status": "pass",
        "production_writes": 0,
        "input_exact_reviewed_one_to_one": len(rows),
        "probe_counts": dict(sorted(counts.items())),
        "valid_exact_product_images": len(valid),
        "unique_valid_urls": unique_urls,
        "unique_valid_image_hashes": unique_hashes,
        "resolved_by_directory_token": dict(sorted(Counter(str(r["directory_token"]) for r in valid).items())),
        "unresolved": [r for r in results if not r["valid_image"]],
        "resolved": valid,
    }
    if len(valid) != unique_urls:
        raise RuntimeError({"duplicate_exact_product_image_url": report})

    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")

    fields = [
        "print_id","card_name","collector_number","language","rarity","variant","id_product","category_id",
        "expansion_external_id","directory_token","url","sha256","width","height","format","status","content_type",
        "bytes","valid_image","probe","identity_confidence","identity_reviewed","error","decode_error",
    ]
    args.csv.parent.mkdir(parents=True, exist_ok=True)
    with args.csv.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(results)

    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
