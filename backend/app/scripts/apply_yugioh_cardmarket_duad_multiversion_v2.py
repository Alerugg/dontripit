from __future__ import annotations

import csv
from pathlib import Path

from app.scripts import apply_yugioh_cardmarket_duad_multiversion_v1 as impl

MANIFEST = Path(__file__).resolve().parents[1] / "data" / "yugioh_duad_jp_image_bijection_certified_v2.csv"
EXPECTED_ROWS = 76
REQUIRED_COLUMNS = {
    "idProduct",
    "idMetacard",
    "print_id",
    "card_id",
    "card_name",
    "collector_number",
    "canonical_variant",
    "canonical_rarity",
    "product_image_sha256",
    "canonical_image_sha256",
    "minimum_relative_assignment_gap",
}


def _validate_manifest() -> None:
    with MANIFEST.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        columns = set(reader.fieldnames or [])
        if columns != REQUIRED_COLUMNS:
            raise RuntimeError(
                {
                    "certified_v2_manifest_schema_drift": {
                        "expected": sorted(REQUIRED_COLUMNS),
                        "actual": sorted(columns),
                    }
                }
            )
        rows = [dict(row) for row in reader]
    if len(rows) != EXPECTED_ROWS:
        raise RuntimeError({"certified_v2_manifest_count_drift": len(rows)})
    for row in rows:
        for key in ("product_image_sha256", "canonical_image_sha256"):
            value = str(row.get(key) or "").casefold()
            if len(value) != 64 or any(ch not in "0123456789abcdef" for ch in value):
                raise RuntimeError(
                    {
                        "invalid_certified_v2_image_hash": {
                            "idProduct": row.get("idProduct"),
                            "field": key,
                            "value": value,
                        }
                    }
                )


def main() -> int:
    _validate_manifest()
    # Reuse the already-tested transactional implementation, replacing only its
    # frozen evidence source with the exact V2 artifact-derived manifest.
    impl.MANIFEST = MANIFEST
    return impl.main()


if __name__ == "__main__":
    raise SystemExit(main())
