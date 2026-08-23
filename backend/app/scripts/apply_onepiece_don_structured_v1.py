from __future__ import annotations

import hashlib
import json
import os
import re
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from urllib.request import Request, urlopen

import psycopg2
from psycopg2.extras import Json, RealDictCursor
from pypdf import PdfReader

SOURCE_URL = os.getenv("ONEPIECE_DON_OFFICIAL_URL", "https://onepiece-cardgame.com/pdf/don-cardlist.pdf?v=260227")
EXPECTED_PDF_SHA256 = os.getenv("ONEPIECE_DON_EXPECTED_PDF_SHA256", "cd518a04ea3ff1acdc1f3bc824ad53d0ca17d8ee2fd0a6427717e0bdaacbdfe0").strip().lower()
EXPECTED_PAGES = 30
EXPECTED_ITEMS = 262
EXPECTED_MARKET_METACARDS_MIN = 150
OUTPUT = Path(os.getenv("ONEPIECE_DON_APPLY_OUTPUT", "artifacts/onepiece-don-structured-v1-apply.json"))
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
                "ST-01 is a claimed/test label only, never a collector_number",
                "do not conflate with collaborator Bushiroad/Premier version",
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
                "distinct physical variant from Osaka minisite reservation test",
                "do not inherit ST-01 or any collector number from Osaka evidence",
                "canonical Print mapping requires independent deterministic evidence",
            ],
        },
    },
)


def _norm(value: str | None) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch)).casefold()
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", text)).strip()


def _subject_from_market_name(name: str) -> str | None:
    """Extract only character-like labels explicitly present in Cardmarket names.

    This is source metadata, not canonical identity. Ambiguous event/set labels
    deliberately return NULL instead of guessing a character from artwork.
    """
    match = re.search(r"\((.*)\)\s*$", str(name or "").strip())
    if not match:
        return None
    label = match.group(1).strip()

    patterns = (
        r"^PRB02\s*-\s*(.+)$",
        r"^PRB\s+(.+)$",
        r"^(?:DP\d{2}\s+)(.+)$",
        r"^(.+?)\s+DP\d{2}$",
        r"^(.+?)\s+(?:EB\d{2}|TS\d{2}|SSG|DFC)$",
        r"^Kumamoto\s+2026\s+(.+)$",
        r"^(.+?)\s*-\s*Special\s+Don!!\s+Set$",
        r"^OP\d{2}\s*-\s*(.+)$",
        r"^OP13\s+(.+)$",
        r"^Live\s+Action\s+(.+)$",
        r"^Elbaph\s+(.+)$",
        r"^General\s+Shogun\s+(.+?)\s*-\s*Saikyo\s+Jump",
        r"^(.+?)\s*-\s*Saiko\s+Jump",
    )
    for pattern in patterns:
        m = re.match(pattern, label, flags=re.I)
        if m:
            candidate = m.group(1).strip(" -")
            return candidate or None

    # Explicit multi-character artwork labels remain searchable as source labels.
    if re.search(r"\b(?:Luffy|Zoro|Sanji|Nami|Chopper|Robin|Franky|Brook|Jinbe|Ace|Sabo|Law|Corazon|Garp|Kuma|Bonney|Magellan|Galdino)\b", label, flags=re.I):
        return label
    return None


def _download_pdf() -> bytes:
    if "onepiece-cardgame.com" not in SOURCE_URL:
        raise RuntimeError(f"refusing non-official DON source URL: {SOURCE_URL}")
    req = Request(SOURCE_URL, headers={"User-Agent": "DonTripIt-Catalog/1.0 (+https://dontripit.com)", "Accept": "application/pdf,*/*;q=0.8"})
    with urlopen(req, timeout=90) as response:
        body = response.read(80_000_001)
        final_url = response.geturl()
    if "onepiece-cardgame.com" not in final_url or len(body) > 80_000_000 or not body.startswith(b"%PDF-"):
        raise RuntimeError("official DON source failed safety checks")
    return body


def _image_object_number(name: str) -> int:
    match = re.search(r"(\d+)", str(name or ""))
    return int(match.group(1)) if match else 10**9


def _extract_inventory(pdf_bytes: bytes) -> tuple[str, list[dict], dict]:
    pdf_sha = hashlib.sha256(pdf_bytes).hexdigest()
    if EXPECTED_PDF_SHA256 and pdf_sha != EXPECTED_PDF_SHA256:
        raise AssertionError({"unexpected_official_pdf_sha256": pdf_sha, "expected": EXPECTED_PDF_SHA256, "action": "review source change before materialization"})
    reader = PdfReader(BytesIO(pdf_bytes), strict=False)
    if len(reader.pages) != EXPECTED_PAGES:
        raise AssertionError({"unexpected_page_count": len(reader.pages), "expected": EXPECTED_PAGES})
    raw_images: list[dict] = []
    for page_number, page in enumerate(reader.pages, start=1):
        for image in sorted(list(page.images), key=lambda image: _image_object_number(getattr(image, "name", ""))):
            data = image.data
            raw_images.append({"page_number": page_number, "image_object": str(getattr(image, "name", "")), "image_sha256": hashlib.sha256(data).hexdigest(), "image_bytes": len(data)})
    counts = Counter(row["image_sha256"] for row in raw_images)
    furniture = {sha for sha, count in counts.items() if count == EXPECTED_PAGES}
    if len(furniture) != 1:
        raise AssertionError({"expected_one_repeated_page_furniture_image": sorted(furniture), "raw_images": len(raw_images)})
    items = [row for row in raw_images if row["image_sha256"] not in furniture]
    if len(items) != EXPECTED_ITEMS or len({row["image_sha256"] for row in items}) != EXPECTED_ITEMS:
        raise AssertionError({"official_items": len(items), "unique": len({row['image_sha256'] for row in items}), "expected": EXPECTED_ITEMS})
    page_slots: Counter[int] = Counter()
    for seq, row in enumerate(items, start=1):
        page_slots[row["page_number"]] += 1
        row["sequence_number"] = seq
        row["slot_number"] = page_slots[row["page_number"]]
    return pdf_sha, items, {"pages": len(reader.pages), "raw_images": len(raw_images), "page_furniture_images": sum(counts[h] for h in furniture), "official_items": len(items), "unique_item_images": len({r['image_sha256'] for r in items})}


def _market_groups(cur, game_id: int) -> tuple[object, list[dict]]:
    cur.execute("SELECT max(last_seen_at) AS ts FROM external_catalog_products WHERE source='cardmarket' AND game_id=%s", (game_id,))
    latest = cur.fetchone()["ts"]
    if latest is None:
        raise AssertionError("missing Cardmarket snapshot for One Piece")
    cur.execute(
        """
        SELECT external_id,name,metacard_external_id,category,expansion_external_id,last_seen_at
        FROM external_catalog_products
        WHERE source='cardmarket' AND game_id=%s AND product_group='single' AND last_seen_at=%s
          AND (
            lower(name) LIKE '%%don!!%%' OR lower(name) LIKE '%%don card%%'
            OR lower(coalesce(category,'')) LIKE '%%don%%'
            OR lower(coalesce(website_path,'')) LIKE '%%don%%'
          )
        ORDER BY metacard_external_id, external_id
        """,
        (game_id, latest),
    )
    raw = [dict(r) for r in cur.fetchall()]
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in raw:
        meta = str(row.get("metacard_external_id") or "").strip()
        if meta:
            grouped[meta].append(row)
    rows = []
    for meta, members in sorted(grouped.items()):
        names = Counter(str(r["name"]).strip() for r in members if str(r.get("name") or "").strip())
        representative_name = sorted(names.items(), key=lambda item: (-item[1], len(item[0]), item[0].lower()))[0][0]
        subject = _subject_from_market_name(representative_name)
        rows.append({
            "source": "cardmarket",
            "metacard_external_id": meta,
            "representative_external_product_id": str(members[0]["external_id"]),
            "name": representative_name,
            "subject": subject,
            "subject_normalized": _norm(subject) or None,
            "product_ids": sorted({str(r["external_id"]) for r in members}),
            "product_count": len({str(r["external_id"]) for r in members}),
            "source_as_of": latest,
        })
    if len(rows) < EXPECTED_MARKET_METACARDS_MIN:
        raise AssertionError({"cardmarket_don_metacards_regression": len(rows), "minimum": EXPECTED_MARKET_METACARDS_MIN})
    return latest, rows


def _write_report(report: dict) -> None:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str, sort_keys=True) + "\n", encoding="utf-8")
    print(OUTPUT.read_text(encoding="utf-8"))


def main() -> int:
    pdf_sha, inventory, extraction = _extract_inventory(_download_pdf())
    report: dict = {"generated_at": datetime.now(timezone.utc).isoformat(), "source_url": SOURCE_URL, "pdf_sha256": pdf_sha, "apply": APPLY, "extraction": extraction, "evidence_rows": len(EVIDENCE_ROWS), "production_writes": 0}
    if not APPLY:
        report["status"] = "dry_run_pass"
        _write_report(report)
        return 0

    url = (os.getenv("DATABASE_URL_UNPOOLED") or os.getenv("DATABASE_URL") or "").strip()
    if not url:
        raise RuntimeError("DATABASE_URL_UNPOOLED or DATABASE_URL is required for apply")
    conn = psycopg2.connect(url); conn.autocommit = False
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SHOW transaction_read_only")
            if cur.fetchone()["transaction_read_only"] == "on":
                raise RuntimeError("apply requested but database is read-only")
            for table in ("onepiece_don_official_items", "onepiece_don_evidence_items", "onepiece_don_market_items"):
                cur.execute("SELECT to_regclass(%s) AS t", (f"public.{table}",))
                if not cur.fetchone()["t"]:
                    raise RuntimeError(f"{table} missing; run alembic upgrade head")
            cur.execute("SELECT id FROM games WHERE slug='onepiece'")
            game_id = int(cur.fetchone()["id"])
            latest, market_rows = _market_groups(cur, game_id)

            for row in inventory:
                cur.execute(
                    """INSERT INTO onepiece_don_official_items(pdf_sha256,source_url,sequence_number,page_number,slot_number,image_object,image_sha256)
                       VALUES(%s,%s,%s,%s,%s,%s,%s)
                       ON CONFLICT(pdf_sha256,image_object) DO UPDATE SET source_url=EXCLUDED.source_url,sequence_number=EXCLUDED.sequence_number,page_number=EXCLUDED.page_number,slot_number=EXCLUDED.slot_number,image_sha256=EXCLUDED.image_sha256,updated_at=now()""",
                    (pdf_sha,SOURCE_URL,row["sequence_number"],row["page_number"],row["slot_number"],row["image_object"],row["image_sha256"]),
                )
            for evidence in EVIDENCE_ROWS:
                cur.execute(
                    """INSERT INTO onepiece_don_evidence_items(evidence_key,evidence_kind,source_label,source_url,organization,physical_received,claimed_label,identity_status,evidence_json)
                       VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)
                       ON CONFLICT(evidence_key) DO UPDATE SET evidence_kind=EXCLUDED.evidence_kind,source_label=EXCLUDED.source_label,source_url=EXCLUDED.source_url,organization=EXCLUDED.organization,physical_received=EXCLUDED.physical_received,claimed_label=EXCLUDED.claimed_label,identity_status=EXCLUDED.identity_status,evidence_json=EXCLUDED.evidence_json,updated_at=now()""",
                    (evidence["evidence_key"],evidence["evidence_kind"],evidence["source_label"],evidence["source_url"],evidence["organization"],evidence["physical_received"],evidence["claimed_label"],evidence["identity_status"],Json(evidence["evidence_json"])),
                )
            for row in market_rows:
                cur.execute(
                    """INSERT INTO onepiece_don_market_items(source,metacard_external_id,representative_external_product_id,name,subject,subject_normalized,product_ids_json,product_count,source_as_of)
                       VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)
                       ON CONFLICT(source,metacard_external_id) DO UPDATE SET representative_external_product_id=EXCLUDED.representative_external_product_id,name=EXCLUDED.name,subject=EXCLUDED.subject,subject_normalized=EXCLUDED.subject_normalized,product_ids_json=EXCLUDED.product_ids_json,product_count=EXCLUDED.product_count,source_as_of=EXCLUDED.source_as_of,updated_at=now()""",
                    (row["source"],row["metacard_external_id"],row["representative_external_product_id"],row["name"],row["subject"],row["subject_normalized"],Json(row["product_ids"]),row["product_count"],row["source_as_of"]),
                )

            cur.execute("SELECT count(*) n,count(*) FILTER(WHERE print_id IS NOT NULL) mapped,count(DISTINCT image_sha256) unique_images,min(sequence_number) min_sequence,max(sequence_number) max_sequence FROM onepiece_don_official_items WHERE pdf_sha256=%s", (pdf_sha,))
            official = dict(cur.fetchone())
            cur.execute("SELECT evidence_key,evidence_kind,physical_received,claimed_label,identity_status FROM onepiece_don_evidence_items WHERE evidence_key=ANY(%s) ORDER BY evidence_key", ([e["evidence_key"] for e in EVIDENCE_ROWS],))
            evidence_after = [dict(r) for r in cur.fetchall()]
            cur.execute("SELECT count(*) n,count(*) FILTER(WHERE subject_normalized IS NOT NULL) subject_rows,sum(product_count) represented_products,count(*) FILTER(WHERE official_item_id IS NOT NULL) mapped FROM onepiece_don_market_items WHERE source='cardmarket' AND source_as_of=%s", (latest,))
            market = dict(cur.fetchone())
            cur.execute("SELECT count(*) n FROM onepiece_don_prints")
            classified_prints = int(cur.fetchone()["n"])

            if int(official["n"]) != EXPECTED_ITEMS or int(official["unique_images"]) != EXPECTED_ITEMS or int(official["mapped"]) != 0:
                raise AssertionError({"official_inventory_after": official})
            if int(official["min_sequence"]) != 1 or int(official["max_sequence"]) != EXPECTED_ITEMS:
                raise AssertionError({"official_sequence_after": official})
            if len(evidence_after) != 2 or evidence_after[0]["evidence_key"] == evidence_after[1]["evidence_key"]:
                raise AssertionError({"evidence_after": evidence_after})
            osaka = next(r for r in evidence_after if r["evidence_key"].startswith("osaka-"))
            collaborator = next(r for r in evidence_after if r["evidence_key"].startswith("collaborator-"))
            if osaka["claimed_label"] != "ST-01" or collaborator["claimed_label"] is not None:
                raise AssertionError({"evidence_identity_separation": evidence_after})
            if int(market["n"]) != len(market_rows) or int(market["represented_products"] or 0) != sum(r["product_count"] for r in market_rows) or int(market["mapped"]) != 0:
                raise AssertionError({"market_after": market, "expected_metacards": len(market_rows)})
            if classified_prints != 0:
                raise AssertionError({"unexpected_canonical_don_prints": classified_prints})

            report.update({"cardmarket_as_of": latest, "market_source_rows": len(market_rows), "market_source_products": sum(r["product_count"] for r in market_rows), "market_subject_rows": sum(1 for r in market_rows if r["subject_normalized"]), "official_inventory_after": official, "evidence_after": evidence_after, "market_after": market, "canonical_don_prints": classified_prints, "status": "apply_pass", "production_writes": len(inventory)+len(EVIDENCE_ROWS)+len(market_rows)})
        conn.commit()
    except Exception:
        conn.rollback(); raise
    finally:
        conn.close()
    _write_report(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
