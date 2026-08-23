from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

from pypdf import PdfReader


SOURCE_URL = os.getenv(
    "ONEPIECE_DON_OFFICIAL_URL",
    "https://onepiece-cardgame.com/pdf/don-cardlist.pdf?v=260227",
)
OUTPUT = Path(
    os.getenv(
        "ONEPIECE_DON_SOURCE_PROBE_OUTPUT",
        "artifacts/onepiece-don-official-source-v1.json",
    )
)
PDF_OUTPUT = Path(
    os.getenv(
        "ONEPIECE_DON_SOURCE_PDF_OUTPUT",
        "artifacts/onepiece-don-official-source-v1.pdf",
    )
)


EXPECTED_HOST = "onepiece-cardgame.com"
MIN_PDF_BYTES = 100_000
MAX_PDF_BYTES = 80_000_000


def _compact(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def main() -> int:
    if EXPECTED_HOST not in SOURCE_URL:
        raise RuntimeError(f"refusing non-official DON source URL: {SOURCE_URL}")

    req = Request(
        SOURCE_URL,
        headers={
            "User-Agent": "DonTripIt-Catalog-Audit/1.0 (+https://dontripit.com)",
            "Accept": "application/pdf,*/*;q=0.8",
        },
    )
    with urlopen(req, timeout=90) as response:
        content_type = str(response.headers.get("Content-Type") or "").lower()
        body = response.read(MAX_PDF_BYTES + 1)
        final_url = response.geturl()
        last_modified = response.headers.get("Last-Modified")
        etag = response.headers.get("ETag")

    if EXPECTED_HOST not in final_url:
        raise RuntimeError(f"official DON source redirected outside expected host: {final_url}")
    if len(body) > MAX_PDF_BYTES:
        raise RuntimeError(f"official DON PDF exceeds safety limit: {len(body)} bytes")
    if len(body) < MIN_PDF_BYTES:
        raise RuntimeError(f"official DON PDF unexpectedly small: {len(body)} bytes")
    if not body.startswith(b"%PDF-"):
        raise RuntimeError(f"official DON source is not a PDF (content-type={content_type!r})")

    PDF_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    PDF_OUTPUT.write_bytes(body)

    reader = PdfReader(str(PDF_OUTPUT), strict=False)
    page_rows: list[dict] = []
    all_text_parts: list[str] = []
    for index, page in enumerate(reader.pages):
        extracted = page.extract_text() or ""
        compact = _compact(extracted)
        all_text_parts.append(extracted)
        page_rows.append(
            {
                "page": index + 1,
                "text_chars": len(extracted),
                "text_preview": compact[:1600],
                "image_count": len(getattr(page, "images", []) or []),
            }
        )

    combined_text = "\n".join(all_text_parts)
    normalized = combined_text.lower()
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_url": SOURCE_URL,
        "final_url": final_url,
        "content_type": content_type,
        "last_modified": last_modified,
        "etag": etag,
        "bytes": len(body),
        "sha256": hashlib.sha256(body).hexdigest(),
        "pages": len(reader.pages),
        "text_chars": len(combined_text),
        "signals": {
            "has_don_card_list_ja": "ドン!!カードリスト" in combined_text or "ドン！！カードリスト" in combined_text,
            "has_event_distribution": "イベント配布" in combined_text,
            "has_standard_battle": "スタンダードバトル" in combined_text,
            "has_storage_box": "ストレージボックス" in combined_text,
            "mentions_luffy": "ルフィ" in combined_text or "luffy" in normalized,
            "mentions_zoro": "ゾロ" in combined_text or "zoro" in normalized,
            "mentions_sabo": "サボ" in combined_text or "sabo" in normalized,
        },
        "page_rows": page_rows,
        "status": "pass",
    }

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
