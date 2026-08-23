from __future__ import annotations

"""Build visual controls for the read-only DON image crosswalk calibration.

Consumes a V4 calibration artifact and renders Cardmarket / best Bandai /
second-best Bandai triplets. No mapping decision is made here. The purpose is
to visually validate whether score + margin separate true same-art pairs from
near-neighbour official images before a fixed V5 production gate is proposed.
"""

import hashlib
import io
import json
import os
import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps
from pypdf import PdfReader

from app.scripts import audit_onepiece_don_image_crosswalk_v2 as v2

INPUT = Path(os.getenv("ONEPIECE_DON_V4_INPUT", "artifacts/onepiece-don-image-crosswalk-v4-fast.json"))
OUT = Path(os.getenv("ONEPIECE_DON_V5_OUTPUT_DIR", "artifacts/onepiece-don-v5-visual"))

THUMB_W = 210
THUMB_H = 294
PANEL_W = 700
PANEL_H = 430
COLS = 2


def _official_bodies(pdf_bytes: bytes) -> dict[int, bytes]:
    if hashlib.sha256(pdf_bytes).hexdigest() != v2.EXPECTED_PDF_SHA256:
        raise AssertionError("official PDF hash drifted")
    reader = PdfReader(io.BytesIO(pdf_bytes), strict=False)
    if len(reader.pages) != v2.EXPECTED_PAGES:
        raise AssertionError("official PDF page count drifted")

    raw = []
    for page_number, page in enumerate(reader.pages, start=1):
        for image in list(page.images):
            body = image.data
            raw.append((page_number, hashlib.sha256(body).hexdigest(), body))
    counts = {}
    for _, digest, _ in raw:
        counts[digest] = counts.get(digest, 0) + 1
    furniture = {digest for digest, count in counts.items() if count == v2.EXPECTED_PAGES}
    if len(furniture) != 1:
        raise AssertionError("unexpected PDF furniture")
    items = [body for _, digest, body in raw if digest not in furniture]
    if len(items) != v2.EXPECTED_ITEMS:
        raise AssertionError("official item count drifted")
    return {index: body for index, body in enumerate(items, start=1)}


def _open(body: bytes) -> Image.Image:
    with Image.open(io.BytesIO(body)) as source:
        return ImageOps.exif_transpose(source).convert("RGB")


def _crop(image: Image.Image, pct_text: str) -> Image.Image:
    pct = float(pct_text)
    if pct <= 0:
        return image.copy()
    width, height = image.size
    dx = max(1, int(round(width * pct)))
    dy = max(1, int(round(height * pct)))
    return image.crop((dx, dy, width - dx, height - dy))


def _thumb(image: Image.Image) -> Image.Image:
    canvas = Image.new("RGB", (THUMB_W, THUMB_H), "white")
    fitted = ImageOps.contain(image, (THUMB_W, THUMB_H))
    x = (THUMB_W - fitted.width) // 2
    y = (THUMB_H - fitted.height) // 2
    canvas.paste(fitted, (x, y))
    return canvas


def _panel(row: dict, official: dict[int, bytes]) -> Image.Image:
    market_body = v2._download(row["cardmarket_image_url"])
    best = row["best"]
    second = row["second_best"]
    market = _thumb(_crop(_open(market_body), best["market_crop"]))
    best_img = _thumb(_crop(_open(official[int(best["sequence_number"])]), best["official_crop"]))
    second_img = _thumb(_crop(_open(official[int(second["sequence_number"])]), second["official_crop"]))

    panel = Image.new("RGB", (PANEL_W, PANEL_H), "white")
    draw = ImageDraw.Draw(panel)
    font = ImageFont.load_default()
    xs = (10, 245, 480)
    for x, image, label in zip(xs, (market, best_img, second_img), ("Cardmarket", "Bandai BEST", "Bandai SECOND")):
        panel.paste(image, (x, 58))
        draw.text((x, 42), label, fill="black", font=font)

    title = f'{row["product_id"]} | {row["name"]}'
    draw.text((10, 8), title[:108], fill="black", font=font)
    best_d = best["distance"]
    second_d = second["distance"]
    detail = (
        f'BEST seq={best["sequence_number"]} sum={best_d["sum"]} max={best_d["max"]} '
        f'margin={row["distance_margin"]} crops={best["market_crop"]}/{best["official_crop"]} | '
        f'SECOND seq={second["sequence_number"]} sum={second_d["sum"]} max={second_d["max"]}'
    )
    wrapped = textwrap.wrap(detail, width=108)
    y = 365
    for line in wrapped[:4]:
        draw.text((10, y), line, fill="black", font=font)
        y += 14
    return panel


def _sheet(rows: list[dict], official: dict[int, bytes], path: Path) -> None:
    panels = [_panel(row, official) for row in rows]
    sheet_rows = (len(panels) + COLS - 1) // COLS
    sheet = Image.new("RGB", (PANEL_W * COLS, PANEL_H * sheet_rows), "white")
    for index, panel in enumerate(panels):
        x = (index % COLS) * PANEL_W
        y = (index // COLS) * PANEL_H
        sheet.paste(panel, (x, y))
    sheet.save(path, format="JPEG", quality=88, optimize=True)


def main() -> int:
    report = json.loads(INPUT.read_text(encoding="utf-8"))
    if report.get("status") != "pass" or int(report.get("fetched_images") or 0) != 161:
        raise AssertionError("V4 input is not the certified 161-image calibration")

    matches = list(report["matches"])
    strong = [
        row for row in matches
        if int(row["best"]["distance"]["sum"]) <= 75
        and int(row["distance_margin"]) >= 75
    ]
    ambiguous = [
        row for row in matches
        if int(row["best"]["distance"]["sum"]) <= 35
        and int(row["distance_margin"]) < 16
    ]
    strong.sort(key=lambda r: (int(r["best"]["distance"]["sum"]), -int(r["distance_margin"]), r["product_id"]))
    ambiguous.sort(key=lambda r: (int(r["best"]["distance"]["sum"]), int(r["distance_margin"]), r["product_id"]))
    if len(strong) != 26 or len(ambiguous) != 15:
        raise AssertionError({"strong": len(strong), "ambiguous": len(ambiguous)})

    official = _official_bodies(v2._download(v2.OFFICIAL_URL, official=True))
    OUT.mkdir(parents=True, exist_ok=True)
    _sheet(strong, official, OUT / "strong-candidates.jpg")
    _sheet(ambiguous, official, OUT / "ambiguous-controls.jpg")

    summary = {
        "status": "pass",
        "production_writes": 0,
        "decision": "visual_calibration_only_no_mapping_gate",
        "strong_rule": "best_sum<=75 and margin>=75",
        "strong_count": len(strong),
        "ambiguous_control_rule": "best_sum<=35 and margin<16",
        "ambiguous_count": len(ambiguous),
        "strong": [
            {
                "idProduct": row["product_id"],
                "name": row["name"],
                "best_sequence": row["best"]["sequence_number"],
                "best_sum": row["best"]["distance"]["sum"],
                "best_max": row["best"]["distance"]["max"],
                "margin": row["distance_margin"],
            }
            for row in strong
        ],
        "ambiguous": [
            {
                "idProduct": row["product_id"],
                "name": row["name"],
                "best_sequence": row["best"]["sequence_number"],
                "best_sum": row["best"]["distance"]["sum"],
                "best_max": row["best"]["distance"]["max"],
                "margin": row["distance_margin"],
                "second_sequence": row["second_best"]["sequence_number"],
                "second_sum": row["second_best"]["distance"]["sum"],
            }
            for row in ambiguous
        ],
    }
    (OUT / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({k: summary[k] for k in ("status", "production_writes", "strong_count", "ambiguous_count", "decision")}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
