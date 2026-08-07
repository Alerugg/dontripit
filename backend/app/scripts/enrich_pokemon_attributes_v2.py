from __future__ import annotations

import argparse
import json
from datetime import date, datetime, timezone
from pathlib import Path

from sqlalchemy import text

from app import db
from app.pokemon_source_inventory import load_inventory
from app.scripts.audit_pokemon_rich_snapshot_v2 import load_snapshot, run as run_rich_audit


SOURCE = "tcgdex/cards-database"
EXPECTED_CANONICAL_ENGLISH = 21065

CARD_ATTRIBUTE_KEYS = (
    "category",
    "dex_id",
    "hp",
    "types",
    "evolve_from",
    "weight",
    "description",
    "level",
    "stage",
    "suffix",
    "held_item",
    "abilities",
    "attacks",
    "weaknesses",
    "resistances",
    "retreat",
    "effect",
    "trainer_type",
    "energy_type",
)

PRINT_ATTRIBUTE_KEYS = (
    "rarity",
    "illustrator",
    "regulation_mark",
    "boosters",
    "variants",
    "variant_shape",
    "third_party",
)


def _release_date(row: dict) -> date | None:
    raw = str((row.get("set") or {}).get("release_date") or "").strip()
    if not raw:
        return None
    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        return None


def _canonical_ids(snapshot: dict[str, dict], neon_ids: set[str]) -> set[str]:
    rest_ids = set(load_inventory().physical_cards)
    today = datetime.now(timezone.utc).date()
    accepted = set(rest_ids)
    for source_id, row in snapshot.items():
        if source_id in rest_ids or source_id not in neon_ids:
            continue
        name = str(row.get("name") or "").strip()
        released = _release_date(row)
        if name and released is not None and released <= today:
            accepted.add(source_id)
    return accepted


def _json_text(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str)


def _write_json(path: Path | None, payload: dict) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def _chunks(rows: list[dict], size: int = 750):
    for index in range(0, len(rows), size):
        yield rows[index:index + size]


def run(
    snapshot_path: Path,
    manifest_path: Path,
    *,
    backup_path: Path | None = None,
    report_path: Path | None = None,
) -> dict:
    # Identity reconciliation must be green immediately before enrichment.
    audit = run_rich_audit(snapshot_path, manifest_path)
    if audit.get("status") != "pass":
        raise AssertionError("Pokémon rich-source identity reconciliation is not green")

    snapshot = load_snapshot(snapshot_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    source_version = str(manifest.get("source_version") or "").strip()
    if not source_version:
        raise AssertionError("Rich source has no pinned version")

    db.init_engine()
    with db.SessionLocal() as session:
        neon_rows = session.execute(text(
            """
            SELECT c.id AS card_id, c.tcgdex_id, c.name,
                   p.id AS print_id, p.rarity
            FROM cards c
            JOIN games g ON g.id=c.game_id
            JOIN prints p ON p.card_id=c.id AND p.tcgdex_id=c.tcgdex_id
            WHERE g.slug='pokemon' AND c.tcgdex_id IS NOT NULL
            """
        )).mappings().all()
        neon_by_source = {str(row["tcgdex_id"]): dict(row) for row in neon_rows}
        canonical_ids = _canonical_ids(snapshot, set(neon_by_source))

        if len(canonical_ids) != EXPECTED_CANONICAL_ENGLISH:
            raise AssertionError(
                f"Canonical English identity count moved: {len(canonical_ids)} != {EXPECTED_CANONICAL_ENGLISH}; re-audit first"
            )
        missing_neon = sorted(canonical_ids - set(neon_by_source))
        missing_snapshot = sorted(canonical_ids - set(snapshot))
        if missing_neon or missing_snapshot:
            raise AssertionError(
                f"Enrichment mapping incomplete: missing_neon={len(missing_neon)} missing_snapshot={len(missing_snapshot)}"
            )

        card_ids = [int(neon_by_source[source_id]["card_id"]) for source_id in sorted(canonical_ids)]
        print_ids = [int(neon_by_source[source_id]["print_id"]) for source_id in sorted(canonical_ids)]

        before_card_attrs = [dict(row) for row in session.execute(text(
            "SELECT card_id, attributes_json, source, source_version, updated_at FROM card_attributes WHERE card_id = ANY(:ids)"
        ), {"ids": card_ids}).mappings().all()]
        before_print_attrs = [dict(row) for row in session.execute(text(
            "SELECT print_id, attributes_json, source, source_version, updated_at FROM print_attributes WHERE print_id = ANY(:ids)"
        ), {"ids": print_ids}).mappings().all()]
        before_rarities = [dict(row) for row in session.execute(text(
            "SELECT id AS print_id, tcgdex_id, rarity FROM prints WHERE id = ANY(:ids) ORDER BY id"
        ), {"ids": print_ids}).mappings().all()]
        _write_json(backup_path, {
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "source_version": source_version,
            "canonical_count": len(canonical_ids),
            "card_attributes_before": before_card_attrs,
            "print_attributes_before": before_print_attrs,
            "print_rarities_before": before_rarities,
        })
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
        card_payload["source_id"] = source_id
        card_payload["source_id_original"] = source.get("source_id_original")

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
            "attributes_json": _json_text(card_payload),
            "source": SOURCE,
            "source_version": source_version,
        })
        print_rows.append({
            "print_id": int(neon["print_id"]),
            "attributes_json": _json_text(print_payload),
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
    rarity_update = text("UPDATE prints SET rarity=:rarity WHERE id=:print_id")

    with db.SessionLocal() as session:
        with session.begin():
            for batch in _chunks(card_rows):
                session.execute(card_upsert, batch)
            for batch in _chunks(print_rows):
                session.execute(print_upsert, batch)
            for batch in _chunks(rarity_rows):
                session.execute(rarity_update, batch)

            card_attr_count = int(session.execute(text(
                "SELECT COUNT(*) FROM card_attributes WHERE card_id = ANY(:ids) AND source=:source AND source_version=:version"
            ), {"ids": card_ids, "source": SOURCE, "version": source_version}).scalar_one())
            print_attr_count = int(session.execute(text(
                "SELECT COUNT(*) FROM print_attributes WHERE print_id = ANY(:ids) AND source=:source AND source_version=:version"
            ), {"ids": print_ids, "source": SOURCE, "version": source_version}).scalar_one())
            unknown_rarity = int(session.execute(text(
                "SELECT COUNT(*) FROM prints WHERE id = ANY(:ids) AND lower(COALESCE(rarity,''))='unknown'"
            ), {"ids": print_ids}).scalar_one())
            rarity_mismatches = int(session.execute(text(
                """
                SELECT COUNT(*)
                FROM prints p
                JOIN print_attributes pa ON pa.print_id=p.id
                WHERE p.id = ANY(:ids)
                  AND p.rarity IS DISTINCT FROM COALESCE(pa.attributes_json->>'rarity', 'unknown')
                """
            ), {"ids": print_ids}).scalar_one())

            if card_attr_count != EXPECTED_CANONICAL_ENGLISH:
                raise AssertionError(f"Card attributes postcondition failed: {card_attr_count}")
            if print_attr_count != EXPECTED_CANONICAL_ENGLISH:
                raise AssertionError(f"Print attributes postcondition failed: {print_attr_count}")
            if unknown_rarity:
                raise AssertionError(f"{unknown_rarity} canonical baseline Prints still have unknown rarity")
            if rarity_mismatches:
                raise AssertionError(f"{rarity_mismatches} Print rarity values disagree with rich source")

        evidence = dict(session.execute(text(
            """
            SELECT
              COUNT(*) FILTER (WHERE ca.attributes_json->>'category' IS NOT NULL) AS category,
              COUNT(*) FILTER (WHERE ca.attributes_json->>'hp' IS NOT NULL) AS hp,
              COUNT(*) FILTER (WHERE jsonb_array_length(COALESCE(ca.attributes_json->'types','[]'::jsonb)) > 0) AS types,
              COUNT(*) FILTER (WHERE ca.attributes_json->>'stage' IS NOT NULL) AS stage,
              COUNT(*) FILTER (WHERE pa.attributes_json->>'illustrator' IS NOT NULL) AS illustrator,
              COUNT(*) FILTER (WHERE pa.attributes_json->>'regulation_mark' IS NOT NULL) AS regulation_mark,
              COUNT(*) FILTER (WHERE pa.attributes_json->'variants' IS NOT NULL AND pa.attributes_json->'variants' <> 'null'::jsonb) AS variants_defined,
              COUNT(*) FILTER (WHERE pa.attributes_json->>'variant_shape'='detailed_array') AS detailed_variant_cards
            FROM card_attributes ca
            JOIN print_attributes pa ON pa.print_id = (
              SELECT p.id FROM prints p WHERE p.card_id=ca.card_id AND p.tcgdex_id=(SELECT c.tcgdex_id FROM cards c WHERE c.id=ca.card_id) LIMIT 1
            )
            WHERE ca.card_id = ANY(:ids)
            """
        ), {"ids": card_ids}).mappings().one())

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": "transactional_canonical_attribute_enrichment",
        "source": SOURCE,
        "source_version": source_version,
        "canonical_english_cards": len(canonical_ids),
        "card_attributes_upserted": len(card_rows),
        "print_attributes_upserted": len(print_rows),
        "rarities_updated": len(rarity_rows),
        "coverage": evidence,
        "variant_expansion": "not_performed; source definitions stored for separate preflight",
        "status": "pass",
    }
    _write_json(report_path, report)
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--backup-path", type=Path)
    parser.add_argument("--report-path", type=Path)
    args = parser.parse_args()
    run(
        args.snapshot,
        args.manifest,
        backup_path=args.backup_path,
        report_path=args.report_path,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
