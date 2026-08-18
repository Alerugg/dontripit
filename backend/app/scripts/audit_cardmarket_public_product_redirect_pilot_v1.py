from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup


PRODUCT_IDS = ["657441", "657442", "657443", "657484", "657485", "657489"]
BASE = "https://www.cardmarket.com/en/YuGiOh/Products?idProduct={}"


def _rarity_from_text(value: str | None) -> str | None:
    text = str(value or "")
    m = re.search(r"V\.?\s*\d+\s*-\s*([^\)]+)", text, flags=re.IGNORECASE)
    return m.group(1).strip() if m else None


def main() -> int:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": "Mozilla/5.0 (compatible; DontRipIt/1.0; +https://dontripit.com)",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept": "text/html,application/xhtml+xml",
        }
    )
    rows = []
    failures = []
    for index, product_id in enumerate(PRODUCT_IDS):
        url = BASE.format(product_id)
        try:
            response = session.get(url, timeout=25, allow_redirects=True)
            soup = BeautifulSoup(response.text or "", "html.parser")
            canonical = None
            canonical_tag = soup.find("link", attrs={"rel": lambda v: v and "canonical" in v})
            if canonical_tag:
                canonical = canonical_tag.get("href")
            og = soup.find("meta", attrs={"property": "og:url"})
            og_url = og.get("content") if og else None
            title = soup.title.get_text(" ", strip=True) if soup.title else None
            h1 = soup.find("h1")
            h1_text = h1.get_text(" ", strip=True) if h1 else None
            row = {
                "idProduct": product_id,
                "status_code": response.status_code,
                "final_url": response.url,
                "history": [{"status": r.status_code, "url": r.url} for r in response.history],
                "canonical_url": canonical,
                "og_url": og_url,
                "title": title,
                "h1": h1_text,
                "rarity_from_title": _rarity_from_text(title),
                "rarity_from_h1": _rarity_from_text(h1_text),
                "body_bytes": len(response.content or b""),
            }
            rows.append(row)
            if response.status_code >= 400:
                failures.append(f"{product_id}_http_{response.status_code}")
        except Exception as exc:
            rows.append({"idProduct": product_id, "error": f"{type(exc).__name__}: {exc}"})
            failures.append(f"{product_id}_request_error")
        if index + 1 < len(PRODUCT_IDS):
            time.sleep(0.75)

    report = {
        "status": "pass" if not failures else "diagnostic",
        "production_writes": 0,
        "products": rows,
        "failures": failures,
    }
    output = os.getenv("CARDMARKET_PUBLIC_REDIRECT_PILOT_OUTPUT", "/tmp/cardmarket-public-product-redirect-pilot-v1.json")
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    Path(output).write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
