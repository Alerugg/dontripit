from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import text

from app import db
from app.scripts.audit_pokemon_rich_snapshot_v2 import load_snapshot


SOURCE = "tcgdex/cards-database"
EXPECTED_CANONICAL_ENGLISH = 21065

CARD_ATTRIBUTE_KEYS = (
    "category", "dex_id", "hp", "types", "evolve_from", "weight",
    "description", "level", "stage", "suffix", "held_item", "abilities",
    "attacks", "weaknesses", "resistances", "retreat", "effect",
    "trainer_type", "energy_type",
)
PRINT_ATTRIBUTE_KEYS = (
    "rarity", "illustrator", "regulation_mark", "boosters", "variants",
    "variant_shape", "third_party",
)


def _write_json(path: Path | None, payload: dict) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def _json(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str)


def _chunks(rows: list[dict], size: int = 600):
    for index in range(0, len(rows), size):
        yield rows[index:index + size]


def run(snapshot_path: Path, manifest_path: Path, *, backup_path: Path | None = None, report_path: Path | None = None) -> dict:
    snapshot = load_snapshot(snapshot_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("status") != "pass":
        raise AssertionError("Pinned rich snapshot exporter did not pass")
    source_version = str(manifest.get("source_version") or "").strip()
    if not source_version:
        raise AssertionError("Pinned rich source version missing")

    db.init_engine()
    with db.SessionLocal() as session:
        rows = [dict(row) for row in session.execute(text(
            """
            SELECT c.id AS card_id, c.tcgdex_id, c.name,
                   p.id AS print_id, p.rarity
            FROM cards c
            JOIN games g ON g.id=c.game_id
            JOIN prints p ON p.card_id=c.id AND p.tcgdex_id=c.tcgdex_id
            WHERE g.slug='pokemon' AND c.tcgdex_id IS NOT NULL
            """
        )).mappings().all()]
        neon_by_source = {str(row["tcgdex_id"]): row for row in rows}

        # Canonical English identity has already been reconciled. The pinned rich
        # snapshot contains 94 regional/no-English rows that are intentionally not
        # in Neon; stale legacy Neon IDs are intentionally not in the snapshot.
        canonical_ids = set(snapshot) & set(neon_by_source)
        if len(canonical_ids) != EXPECTED_CANONICAL_ENGLISH:
            raise AssertionError(
                f"Pinned snapshot ↔ Neon canonical intersection moved: {len(canonical_ids)} != {EXPECTED_CANONICAL_ENGLISH}"
            )
        non_neon_snapshot = set(snapshot) - set(neon_by_source)
        if len(non_neon_snapshot) != 94:
            raise AssertionError(f"Expected 94 regional/non-English snapshot rows outside Neon, got {len(non_neon_snapshot)}")
        if any(str(snapshot[source_id].get("name") or "").strip() for source_id in non_neon_snapshot):
            raise AssertionError("A non-Neon snapshot row now has an English name; identity reconciliation must run first")

        card_ids = [int(neon_by_source[source_id]["card_id"]) for source_id in sorted(canonical_ids)]
        print_ids = [int(neon_by_source[source_id]["print_id"]) for source_id in sorted(canonical_ids)]

        before = {
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "source_version": source_version,
            "canonical_count": len(canonical_ids),
            "card_attributes_existing": int(session.execute(text(
                "SELECT COUNT(*) FROM card_attributes WHERE card_id = ANY(:ids)"
            ), {"ids": card_ids}).scalar_one()),
            "print_attributes_existing": int(session.execute(text(
                "SELECT COUNT(*) FROM print_attributes WHERE print_id = ANY(:ids)"
            ), {"ids": print_ids}).scalar_one()),
            "unknown_rarity_existing": int(session.execute(text(
                "SELECT COUNT(*) FROM prints WHERE id = ANY(:ids) AND lower(COALESCE(rarity,''))='unknown'"
            ), {"ids": print_ids}).scalar_one()),
        }
        _write_json(backup_path, before)
        session.rollback()

    card_rows: list[dict] = []
    print_rows: list[dict] = []
    rarity_rows: list[dict] = []
    for source_id in sorted(canonical_ids):
        source = snapshot[source_id]
        attrs = source.get("attributes") or {}
        neon = neon_by_source[source_id]
        set_row = source.get("set") or {}

        card_payload = {key: attrs.get(key) for key in CARD_ATTRIBUTE_KEYS}
        card_payload.update({
            "source_id": source_id,
            "source_id_original": source.get("source_id_original"),
        })
        print_payload = {key: attrs.get(key) for key in PRINT_ATTRIBUTE_KEYS}
        print_payload.update({
            "source_id": source_id,
            "source_id_original": source.get("source_id_original"),
            "source_file": source.get("source_file"),
            "language": "en",
            "set_id": set_row.get("id"),
            "set_id_original": set_row.get("id_original"),
            "set_name": set_row.get("name"),
            "series_id": set_row.get("series_id"),
            "series_name": set_row.get("series_name"),
            "release_date": set_row.get("release_date"),
        })
        rarity = str(attrs.get("rarity") or "unknown")
        card_rows.append({
            "card_id": int(neon["card_id"]),
            "attributes_json": _json(card_payload),
            "source": SOURCE,
            "source_version": source_version,
        })
        print_rows.append({
            "print_id": int(neon["print_id"]),
            "attributes_json": _json(print_payload),
            "source": SOURCE,
            "source_version": source_version,
        })
        rarity_rows.append({"print_id": int(neon["print_id"]), "rarity": rarity})

    card_upsert = text(
        """
        INSERT INTO card_attributes (card_id, attributes_json, source, source_version, updated_at)
        VALUES (:card_id, CAST(:attributes_json AS jsonb), :source, :source_version, now())
        ON CONFLICT (card_id) DO UPDATE SET
          attributes_json=EXCLUDED.attributes_json,
          source=EXCLUDED.source,
          source_version=EXCLUDED.source_version,
          updated_at=now()
        """
    )
    print_upsert = text(
        """
        INSERT INTO print_attributes (print_id, attributes_json, source, source_version, updated_at)
        VALUES (:print_id, CAST(:attributes_json AS jsonb), :source, :source_version, now())
        ON CONFLICT (print_id) DO UPDATE SET
          attributes_json=EXCLUDED.attributes_json,
          source=EXCLUDED.source,
          source_version=EXCLUDED.source_version,
          updated_at=now()
        """
    )

    with db.SessionLocal() as session:
        with session.begin():
            for batch in _chunks(card_rows):
                session.execute(card_upsert, batch)
            for batch in _chunks(print_rows):
                session.execute(print_upsert, batch)
            for batch in _chunks(rarity_rows):
                session.execute(text("UPDATE prints SET rarity=:rarity WHERE id=:print_id"), batch)

            card_attr_count = int(session.execute(text(
                "SELECT COUNT(*) FROM card_attributes WHERE card_id = ANY(:ids) AND source=:source AND source_version=:version"
            ), {"ids": card_ids, "source": SOURCE, "version": source_version}).scalar_one())
            print_attr_count = int(session.execute(text(
                "SELECT COUNT(*) FROM print_attributes WHERE print_id = ANY(:ids) AND source=:source AND source_version=:version"
            ), {"ids": print_ids, "source": SOURCE, "version": source_version}).scalar_one())
            unknown_rarity = int(session.execute(text(
                "SELECT COUNT(*) FROM prints WHERE id = ANY(:ids) AND lower(COALESCE(rarity,''))='unknown'"
            ), {"ids": print_ids}).scalar_one())
            rarity_mismatch = int(session.execute(text(
                """
                SELECT COUNT(*) FROM prints p
                JOIN print_attributes pa ON pa.print_id=p.id
                WHERE p.id = ANY(:ids)
                  AND p.rarity IS DISTINCT FROM COALESCE(pa.attributes_json->>'rarity','unknown')
                """
            ), {"ids": print_ids}).scalar_one())

            if card_attr_count != EXPECTED_CANONICAL_ENGLISH or print_attr_count != EXPECTED_CANONICAL_ENGLISH:
                raise AssertionError(f"Attribute postcondition failed: cards={card_attr_count}, prints={print_attr_count}")
            if unknown_rarity:
                raise AssertionError(f"{unknown_rarity} canonical Prints still have unknown rarity")
            if rarity_mismatch:
                raise AssertionError(f"{rarity_mismatch} Print rarity values disagree with pinned source")

        card_coverage = dict(session.execute(text(
            """
            SELECT
              COUNT(*) FILTER (WHERE attributes_json->>'category' IS NOT NULL) AS category,
              COUNT(*) FILTER (WHERE attributes_json->>'hp' IS NOT NULL) AS hp,
              COUNT(*) FILTER (WHERE jsonb_array_length(COALESCE(attributes_json->'types','[]'::jsonb)) > 0) AS types,
              COUNT(*) FILTER (WHERE attributes_json->>'stage' IS NOT NULL) AS stage
            FROM card_attributes WHERE card_id = ANY(:ids)
            """
        ), {"ids": card_ids}).mappings().one())
        print_coverage = dict(session.execute(text(
            """
            SELECT
              COUNT(*) FILTER (WHERE attributes_json->>'illustrator' IS NOT NULL) AS illustrator,
              COUNT(*) FILTER (WHERE attributes_json->>'regulation_mark' IS NOT NULL) AS regulation_mark,
              COUNT(*) FILTER (WHERE attributes_json->'variants' IS NOT NULL AND attributes_json->'variants' <> 'null'::jsonb) AS variants_defined,
              COUNT(*) FILTER (WHERE attributes_json->>'variant_shape'='detailed_array') AS detailed_variant_cards
            FROM print_attributes WHERE print_id = ANY(:ids)
            """
        ), {"ids": print_ids}).mappings().one())

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": "fast_transactional_pinned_snapshot_enrichment",
        "source": SOURCE,
        "source_version": source_version,
        "canonical_english_cards": EXPECTED_CANONICAL_ENGLISH,
        "card_attributes_upserted": len(card_rows),
        "print_attributes_upserted": len(print_rows),
        "rarities_updated": len(rarity_rows),
        "coverage": {**{k: int(v) for k, v in card_coverage.items()}, **{k: int(v) for k, v in print_coverage.items()}},
        "variant_expansion": "not_performed",
        "status": "pass",
    }
    _write_json(report_path, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--backup-path", type=Path)
    parser.add_argument("--report-path", type=Path)
    args = parser.parse_args()
    run(args.snapshot, args.manifest, backup_path=args.backup_path, report_path=args.report_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
