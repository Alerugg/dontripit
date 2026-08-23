from __future__ import annotations

import hashlib
import json
import os
import re
from collections import Counter
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from urllib.request import Request, urlopen

import psycopg2
from psycopg2.extras import Json, RealDictCursor
from pypdf import PdfReader


SOURCE_URL = os.getenv(
    "ONEPIECE_DON_OFFICIAL_URL",
    "https://onepiece-cardgame.com/pdf/don-cardlist.pdf?v=260227",
)
EXPECTED_PDF_SHA256 = os.getenv(
    "ONEPIECE_DON_EXPECTED_PDF_SHA256",
    "cd518a04ea3ff1acdc1f3bc824ad53d0ca17d8ee2fd0a6427717e0bdaacbdfe0",
).strip().lower()
EXPECTED_PAGES = 30
EXPECTED_ITEMS = 262
OUTPUT = Path(
    os.getenv(
        "ONEPIECE_DON_APPLY_OUTPUT",
        "artifacts/onepiece-don-structured-v1-apply.json",
    )
)
APPLY = os.getenv("ONEPIECE_DON_APPLY", "0").strip().lower() in {"1", "true", "yes", "on"}


EVIDENCE_ROWS = (
    {
        "evidence_key": "osaka-championship-2023-minisite-test",
        "evidence_kind": "official_event_test",
        "source_label": "Championship 2023 Osaka reservation test version",
        "source_url": "https://prod.ww.guan.jp/ps/",
        "organization": "ONE PIECE CARD GAME",
        "physical_received": False,
        "claimed_label": "ST-01",
        "identity_status": "unresolved",
        "evidence_json": {
            "provenance": "project_handoff",
            "rules": [
                "ST-01 is preserved only as a claimed/test label, not promoted to collector_number",
                "must not be conflated with the collaborator-received Bushiroad/Premier version",
                "canonical Print mapping requires independent deterministic evidence",
            ],
        },
    },
    {
        "evidence_key": "collaborator-bushiroad-premier-received",
        "evidence_kind": "collaborator_physical",
        "source_label": "Collaborator-received provided/test version",
        "source_url": None,
        "organization": "Bushiroad / Premier Event Inc.",
        "physical_received": True,
        "claimed_label": None,
        "identity_status": "unresolved",
        "evidence_json": {
            "provenance": "project_handoff",
            "rules": [
                "this is a distinct physical variant from the Osaka minisite reservation test",
                "do not inherit ST-01 or any collector number from the Osaka evidence",
                "canonical Print mapping requires independent deterministic evidence",
            ],
        },
    },
)


def _download_pdf() -> bytes:
    if "onepiece-cardgame.com" not in SOURCE_URL:
        raise RuntimeError(f"refusing non-official DON source URL: {SOURCE_URL}")
    req = Request(
        SOURCE_URL,
        headers={
            "User-Agent": "DonTripIt-Catalog/1.0 (+https://dontripit.com)",
            "Accept": "application/pdf,*/*;q=0.8",
        },
    )
    with urlopen(req, timeout=90) as response:
        body = response.read(80_000_001)
        final_url = response.geturl()
    if "onepiece-cardgame.com" not in final_url:
        raise RuntimeError(f"official DON source redirected outside expected host: {final_url}")
    if len(body) > 80_000_000 or not body.startswith(b"%PDF-"):
        raise RuntimeError("official DON source failed PDF safety checks")
    return body


def _image_object_number(name: str) -> int:
    match = re.search(r"(\d+)", str(name or ""))
    return int(match.group(1)) if match else 10**9


def _extract_inventory(pdf_bytes: bytes) -> tuple[str, list[dict], dict]:
    pdf_sha = hashlib.sha256(pdf_bytes).hexdigest()
    if EXPECTED_PDF_SHA256 and pdf_sha != EXPECTED_PDF_SHA256:
        raise AssertionError(
            {
                "unexpected_official_pdf_sha256": pdf_sha,
                "expected": EXPECTED_PDF_SHA256,
                "action": "review source change before catalog materialization",
            }
        )

    reader = PdfReader(BytesIO(pdf_bytes), strict=False)
    if len(reader.pages) != EXPECTED_PAGES:
        raise AssertionError({"unexpected_page_count": len(reader.pages), "expected": EXPECTED_PAGES})

    raw_images: list[dict] = []
    for page_number, page in enumerate(reader.pages, start=1):
        images = sorted(list(page.images), key=lambda image: _image_object_number(getattr(image, "name", "")))
        for image in images:
            data = image.data
            raw_images.append(
                {
                    "page_number": page_number,
                    "image_object": str(getattr(image, "name", "")),
                    "image_sha256": hashlib.sha256(data).hexdigest(),
                    "image_bytes": len(data),
                }
            )

    hash_counts = Counter(row["image_sha256"] for row in raw_images)
    repeated_page_furniture = {
        image_sha
        for image_sha, count in hash_counts.items()
        if count == EXPECTED_PAGES
    }
    if len(repeated_page_furniture) != 1:
        raise AssertionError(
            {
                "expected_one_repeated_page_furniture_image": sorted(repeated_page_furniture),
                "raw_image_count": len(raw_images),
            }
        )

    item_rows = [row for row in raw_images if row["image_sha256"] not in repeated_page_furniture]
    if len(item_rows) != EXPECTED_ITEMS:
        raise AssertionError({"unexpected_don_item_count": len(item_rows), "expected": EXPECTED_ITEMS})
    if len({row["image_sha256"] for row in item_rows}) != EXPECTED_ITEMS:
        raise AssertionError("official DON item images are not one-to-one by sha256")

    page_slots: Counter[int] = Counter()
    for sequence_number, row in enumerate(item_rows, start=1):
        page_slots[row["page_number"]] += 1
        row["sequence_number"] = sequence_number
        row["slot_number"] = page_slots[row["page_number"]]

    extraction = {
        "pages": len(reader.pages),
        "raw_images": len(raw_images),
        "page_furniture_images": sum(hash_counts[h] for h in repeated_page_furniture),
        "page_furniture_sha256": sorted(repeated_page_furniture),
        "official_items": len(item_rows),
        "unique_item_images": len({row["image_sha256"] for row in item_rows}),
        "first_item": item_rows[0],
        "last_item": item_rows[-1],
    }
    return pdf_sha, item_rows, extraction


def _write_report(report: dict) -> None:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str, sort_keys=True) + "\n", encoding="utf-8")
    print(OUTPUT.read_text(encoding="utf-8"))


def main() -> int:
    pdf_bytes = _download_pdf()
    pdf_sha, inventory, extraction = _extract_inventory(pdf_bytes)
    report: dict = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_url": SOURCE_URL,
        "pdf_sha256": pdf_sha,
        "apply": APPLY,
        "extraction": extraction,
        "evidence_rows": len(EVIDENCE_ROWS),
        "production_writes": 0,
    }

    if not APPLY:
        report["status"] = "dry_run_pass"
        _write_report(report)
        return 0

    url = (os.getenv("DATABASE_URL_UNPOOLED") or os.getenv("DATABASE_URL") or "").strip()
    if not url:
        raise RuntimeError("DATABASE_URL_UNPOOLED or DATABASE_URL is required for apply")

    conn = psycopg2.connect(url)
    conn.autocommit = False
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SHOW transaction_read_only")
            if cur.fetchone()["transaction_read_only"] == "on":
                raise RuntimeError("apply requested but database transaction is read-only")

            cur.execute("SELECT to_regclass('public.onepiece_don_official_items') AS t")
            if not cur.fetchone()["t"]:
                raise RuntimeError("onepiece_don_official_items is missing; run Alembic revision 20260823_40 first")
            cur.execute("SELECT to_regclass('public.onepiece_don_evidence_items') AS t")
            if not cur.fetchone()["t"]:
                raise RuntimeError("onepiece_don_evidence_items is missing; run Alembic revision 20260823_40 first")

            inserted_or_updated = 0
            for row in inventory:
                cur.execute(
                    """
                    INSERT INTO onepiece_don_official_items(
                      pdf_sha256, source_url, sequence_number, page_number, slot_number,
                      image_object, image_sha256, image_phash, distribution_label,
                      print_id, mapping_source, mapping_confidence
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s,NULL,NULL,NULL,NULL,NULL)
                    ON CONFLICT (pdf_sha256, image_object) DO UPDATE SET
                      source_url=EXCLUDED.source_url,
                      sequence_number=EXCLUDED.sequence_number,
                      page_number=EXCLUDED.page_number,
                      slot_number=EXCLUDED.slot_number,
                      image_sha256=EXCLUDED.image_sha256,
                      updated_at=now()
                    """,
                    (
                        pdf_sha,
                        SOURCE_URL,
                        row["sequence_number"],
                        row["page_number"],
                        row["slot_number"],
                        row["image_object"],
                        row["image_sha256"],
                    ),
                )
                inserted_or_updated += 1

            evidence_written = 0
            for evidence in EVIDENCE_ROWS:
                cur.execute(
                    """
                    INSERT INTO onepiece_don_evidence_items(
                      evidence_key,evidence_kind,source_label,source_url,organization,
                      physical_received,claimed_label,identity_status,evidence_json
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (evidence_key) DO UPDATE SET
                      evidence_kind=EXCLUDED.evidence_kind,
                      source_label=EXCLUDED.source_label,
                      source_url=EXCLUDED.source_url,
                      organization=EXCLUDED.organization,
                      physical_received=EXCLUDED.physical_received,
                      claimed_label=EXCLUDED.claimed_label,
                      identity_status=EXCLUDED.identity_status,
                      evidence_json=EXCLUDED.evidence_json,
                      updated_at=now()
                    """,
                    (
                        evidence["evidence_key"],
                        evidence["evidence_kind"],
                        evidence["source_label"],
                        evidence["source_url"],
                        evidence["organization"],
                        evidence["physical_received"],
                        evidence["claimed_label"],
                        evidence["identity_status"],
                        Json(evidence["evidence_json"]),
                    ),
                )
                evidence_written += 1

            cur.execute(
                """
                SELECT count(*) AS n,
                       count(*) FILTER (WHERE print_id IS NOT NULL) AS mapped,
                       count(DISTINCT image_sha256) AS unique_images,
                       min(sequence_number) AS min_sequence,
                       max(sequence_number) AS max_sequence
                FROM onepiece_don_official_items
                WHERE pdf_sha256=%s
                """,
                (pdf_sha,),
            )
            after = dict(cur.fetchone())
            cur.execute(
                "SELECT evidence_key,evidence_kind,physical_received,claimed_label,identity_status FROM onepiece_don_evidence_items ORDER BY evidence_key"
            )
            evidence_after = [dict(row) for row in cur.fetchall()]

            if int(after["n"]) != EXPECTED_ITEMS or int(after["unique_images"]) != EXPECTED_ITEMS:
                raise AssertionError({"official_inventory_after_apply": after, "expected": EXPECTED_ITEMS})
            if int(after["mapped"]) != 0:
                raise AssertionError(
                    {
                        "unexpected_preexisting_don_mappings": int(after["mapped"]),
                        "rule": "P0 inventory materialization must not invent or inherit Print mappings",
                    }
                )
            if int(after["min_sequence"]) != 1 or int(after["max_sequence"]) != EXPECTED_ITEMS:
                raise AssertionError({"sequence_integrity": after})
            if len(evidence_after) < len(EVIDENCE_ROWS):
                raise AssertionError({"evidence_after": evidence_after})

            report.update(
                {
                    "official_items_upserted": inserted_or_updated,
                    "evidence_items_upserted": evidence_written,
                    "official_inventory_after": after,
                    "evidence_after": evidence_after,
                    "production_writes": inserted_or_updated + evidence_written,
                    "status": "apply_pass",
                }
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    _write_report(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
