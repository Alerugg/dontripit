from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import text

from app import db
from app.scripts.audit_pokemon_rich_snapshot_v2 import load_snapshot
from app.scripts.preflight_pokemon_variants_v2 import (
    _canonical_ids,
    _detailed_candidates,
    _legacy_candidates,
    run as run_preflight,
)


SOURCE = "tcgdex/cards-database"
VARIANT_SOURCE = "tcgdex-variant-v2"
EXPECTED_CANONICAL_ENGLISH = 21065
EXPECTED_SAFE_VARIANTS = 27241
EXPECTED_CARDS_WITH_VARIANTS = 14549
EXPECTED_ADDITIONAL_PRINTS = 12692


def _write_json(path: Path | None, payload: dict) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def _variant_name(candidate: dict) -> str:
    return f"v2-{candidate['variant_hash']}"


def _variant_print_key(source_id: str, candidate: dict) -> str:
    return f"pokemon:tcgdex:{source_id}:en:{_variant_name(candidate)}"


def _candidate_sort_key(candidate: dict) -> tuple:
    """Deterministically choose which exact variant reuses the TCGdex baseline Print.

    This is a storage decision, not a claim that the first variant is the most
    common or valuable. Prefer main-set context and unadorned standard-size
    variants, then a stable lexical dimension ordering.
    """
    dims = candidate["dimensions"]
    context = dims.get("release_context")
    context_rank = 0 if context == "main_set" else (1 if context is None else 2)
    stamp_rank = 0 if not dims.get("stamps") else 1
    size_rank = 0 if dims.get("size") in (None, "standard") else 1
    type_rank = {"normal": 0, "holo": 1, "reverse": 2, "lenticular": 3, "metal": 4}.get(dims.get("type"), 9)
    return (
        context_rank,
        stamp_rank,
        size_rank,
        type_rank,
        str(dims.get("subtype") or ""),
        str(dims.get("foil") or ""),
        candidate["variant_hash"],
    )


def _merge_print_attributes(existing: object, candidate: dict, source_id: str, source_version: str, *, baseline: bool) -> dict:
    payload = dict(existing) if isinstance(existing, dict) else {}
    payload["physical_variant"] = {
        **candidate["dimensions"],
        "variant_hash": candidate["variant_hash"],
        "third_party": candidate.get("third_party") or {},
        "source_rows": candidate.get("source_rows") or 1,
        "baseline_reused": baseline,
    }
    payload["variant_identity_source"] = SOURCE
    payload["variant_identity_source_version"] = source_version
    payload["source_id"] = source_id
    return payload


def _upsert_identifier(session, print_id: int, source: str, external_id: object) -> None:
    clean = str(external_id or "").strip()
    if not clean:
        return
    session.execute(text(
        """
        INSERT INTO print_identifiers (print_id, source, external_id)
        VALUES (:print_id, :source, :external_id)
        ON CONFLICT (print_id, source) DO UPDATE SET external_id=EXCLUDED.external_id
        """
    ), {"print_id": print_id, "source": source, "external_id": clean})


def run(
    snapshot_path: Path,
    manifest_path: Path,
    *,
    backup_path: Path | None = None,
    report_path: Path | None = None,
) -> dict:
    preflight = run_preflight(snapshot_path, manifest_path)
    if preflight.get("status") != "pass":
        raise AssertionError("Pokémon variant expansion refused: preflight is not green")
    safe_plan = preflight.get("safe_plan") or {}
    if int(safe_plan.get("safe_unique_variant_definitions") or 0) != EXPECTED_SAFE_VARIANTS:
        raise AssertionError("Variant definition count moved; re-audit before expansion")
    if int(safe_plan.get("additional_prints_if_expanded") or 0) != EXPECTED_ADDITIONAL_PRINTS:
        raise AssertionError("Additional Print plan moved; re-audit before expansion")

    snapshot = load_snapshot(snapshot_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    source_version = str(manifest.get("source_version") or "").strip()
    if not source_version:
        raise AssertionError("Pinned rich source version missing")

    db.init_engine()
    with db.SessionLocal() as session:
        neon_rows = session.execute(text(
            """
            SELECT c.tcgdex_id, c.id AS card_id,
                   p.id AS baseline_print_id, p.set_id, p.collector_number,
                   p.language, p.rarity, p.is_foil, p.variant, p.print_key,
                   pa.attributes_json AS print_attributes
            FROM cards c
            JOIN games g ON g.id=c.game_id
            JOIN prints p ON p.card_id=c.id AND p.tcgdex_id=c.tcgdex_id
            LEFT JOIN print_attributes pa ON pa.print_id=p.id
            WHERE g.slug='pokemon' AND c.tcgdex_id IS NOT NULL
            """
        )).mappings().all()
        neon_by_source = {str(row["tcgdex_id"]): dict(row) for row in neon_rows}
        canonical_ids = _canonical_ids(snapshot, set(neon_by_source))
        if len(canonical_ids) != EXPECTED_CANONICAL_ENGLISH:
            raise AssertionError(f"Canonical identity moved: {len(canonical_ids)} != {EXPECTED_CANONICAL_ENGLISH}")

        candidates_by_source: dict[str, list[dict]] = {}
        for source_id in sorted(canonical_ids):
            variants = (snapshot[source_id].get("attributes") or {}).get("variants")
            safe: list[dict] = []
            ambiguous: list[dict] = []
            if isinstance(variants, list):
                safe, ambiguous, _foreign = _detailed_candidates(source_id, variants)
            elif isinstance(variants, dict):
                safe, ambiguous = _legacy_candidates(source_id, variants)
            if ambiguous:
                raise AssertionError(f"Preflight/write disagreement: {source_id} still has ambiguous variants")
            if safe:
                candidates_by_source[source_id] = sorted(safe, key=_candidate_sort_key)

        total_variants = sum(len(rows) for rows in candidates_by_source.values())
        additional_expected = sum(max(0, len(rows) - 1) for rows in candidates_by_source.values())
        if len(candidates_by_source) != EXPECTED_CARDS_WITH_VARIANTS:
            raise AssertionError(f"Cards with variants moved: {len(candidates_by_source)}")
        if total_variants != EXPECTED_SAFE_VARIANTS or additional_expected != EXPECTED_ADDITIONAL_PRINTS:
            raise AssertionError(
                f"Variant plan drift: definitions={total_variants}, additional={additional_expected}"
            )

        baseline_ids = [int(neon_by_source[source_id]["baseline_print_id"]) for source_id in candidates_by_source]
        before = [dict(row) for row in session.execute(text(
            """
            SELECT p.id, p.tcgdex_id, p.card_id, p.set_id, p.collector_number,
                   p.language, p.rarity, p.is_foil, p.variant, p.print_key,
                   pa.attributes_json, pa.source, pa.source_version
            FROM prints p
            LEFT JOIN print_attributes pa ON pa.print_id=p.id
            WHERE p.id = ANY(:ids)
            ORDER BY p.id
            """
        ), {"ids": baseline_ids}).mappings().all()]
        _write_json(backup_path, {
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "source_version": source_version,
            "baseline_prints_before": before,
        })
        session.rollback()

    counters = {
        "baseline_prints_reclassified": 0,
        "additional_prints_inserted": 0,
        "variant_attribute_rows_upserted": 0,
        "variant_identifiers_upserted": 0,
        "market_identifiers_upserted": 0,
        "shared_images_copied": 0,
    }

    with db.SessionLocal() as session:
        with session.begin():
            # Refresh rows under the write transaction.
            baseline_rows = session.execute(text(
                """
                SELECT c.tcgdex_id, c.id AS card_id,
                       p.id AS baseline_print_id, p.set_id, p.collector_number,
                       p.language, p.rarity, p.is_foil, p.variant, p.print_key,
                       pa.attributes_json AS print_attributes
                FROM cards c
                JOIN games g ON g.id=c.game_id
                JOIN prints p ON p.card_id=c.id AND p.tcgdex_id=c.tcgdex_id
                LEFT JOIN print_attributes pa ON pa.print_id=p.id
                WHERE g.slug='pokemon' AND c.tcgdex_id = ANY(:ids)
                """
            ), {"ids": list(candidates_by_source)}).mappings().all()
            current = {str(row["tcgdex_id"]): dict(row) for row in baseline_rows}
            if set(current) != set(candidates_by_source):
                raise AssertionError("Baseline Print set changed between preflight and write")

            for source_id in sorted(candidates_by_source):
                source_row = current[source_id]
                candidates = candidates_by_source[source_id]
                baseline_candidate = candidates[0]
                baseline_print_id = int(source_row["baseline_print_id"])
                baseline_variant = _variant_name(baseline_candidate)
                baseline_key = _variant_print_key(source_id, baseline_candidate)

                session.execute(text(
                    """
                    UPDATE prints
                    SET is_foil=:is_foil, variant=:variant, print_key=:print_key
                    WHERE id=:print_id
                    """
                ), {
                    "is_foil": bool(baseline_candidate["is_foil"]),
                    "variant": baseline_variant,
                    "print_key": baseline_key,
                    "print_id": baseline_print_id,
                })
                counters["baseline_prints_reclassified"] += 1

                baseline_attrs = _merge_print_attributes(
                    source_row.get("print_attributes"),
                    baseline_candidate,
                    source_id,
                    source_version,
                    baseline=True,
                )
                session.execute(text(
                    """
                    INSERT INTO print_attributes (print_id, attributes_json, source, source_version, updated_at)
                    VALUES (:print_id, CAST(:attributes AS jsonb), :source, :version, now())
                    ON CONFLICT (print_id) DO UPDATE SET
                      attributes_json=EXCLUDED.attributes_json,
                      source=EXCLUDED.source,
                      source_version=EXCLUDED.source_version,
                      updated_at=now()
                    """
                ), {
                    "print_id": baseline_print_id,
                    "attributes": json.dumps(baseline_attrs, ensure_ascii=False, separators=(",", ":")),
                    "source": SOURCE,
                    "version": source_version,
                })
                counters["variant_attribute_rows_upserted"] += 1
                _upsert_identifier(session, baseline_print_id, VARIANT_SOURCE, f"{source_id}#{baseline_candidate['variant_hash']}")
                counters["variant_identifiers_upserted"] += 1
                for provider, external_id in (baseline_candidate.get("third_party") or {}).items():
                    _upsert_identifier(session, baseline_print_id, str(provider), external_id)
                    counters["market_identifiers_upserted"] += 1

                primary_image = session.execute(text(
                    "SELECT url FROM print_images WHERE print_id=:print_id ORDER BY is_primary DESC, id ASC LIMIT 1"
                ), {"print_id": baseline_print_id}).scalar_one_or_none()

                for candidate in candidates[1:]:
                    variant = _variant_name(candidate)
                    key = _variant_print_key(source_id, candidate)
                    existing_id = session.execute(text(
                        "SELECT id FROM prints WHERE print_key=:print_key LIMIT 1"
                    ), {"print_key": key}).scalar_one_or_none()
                    if existing_id is None:
                        new_print_id = session.execute(text(
                            """
                            INSERT INTO prints (
                              set_id, card_id, collector_number, language, rarity,
                              is_foil, variant, print_key, tcgdex_id
                            ) VALUES (
                              :set_id, :card_id, :collector, :language, :rarity,
                              :is_foil, :variant, :print_key, NULL
                            ) RETURNING id
                            """
                        ), {
                            "set_id": int(source_row["set_id"]),
                            "card_id": int(source_row["card_id"]),
                            "collector": str(source_row["collector_number"]),
                            "language": "en",
                            "rarity": str(source_row["rarity"]),
                            "is_foil": bool(candidate["is_foil"]),
                            "variant": variant,
                            "print_key": key,
                        }).scalar_one()
                        counters["additional_prints_inserted"] += 1
                    else:
                        new_print_id = int(existing_id)

                    attrs = _merge_print_attributes({}, candidate, source_id, source_version, baseline=False)
                    session.execute(text(
                        """
                        INSERT INTO print_attributes (print_id, attributes_json, source, source_version, updated_at)
                        VALUES (:print_id, CAST(:attributes AS jsonb), :source, :version, now())
                        ON CONFLICT (print_id) DO UPDATE SET
                          attributes_json=EXCLUDED.attributes_json,
                          source=EXCLUDED.source,
                          source_version=EXCLUDED.source_version,
                          updated_at=now()
                        """
                    ), {
                        "print_id": new_print_id,
                        "attributes": json.dumps(attrs, ensure_ascii=False, separators=(",", ":")),
                        "source": SOURCE,
                        "version": source_version,
                    })
                    counters["variant_attribute_rows_upserted"] += 1
                    _upsert_identifier(session, new_print_id, VARIANT_SOURCE, f"{source_id}#{candidate['variant_hash']}")
                    counters["variant_identifiers_upserted"] += 1
                    for provider, external_id in (candidate.get("third_party") or {}).items():
                        _upsert_identifier(session, new_print_id, str(provider), external_id)
                        counters["market_identifiers_upserted"] += 1

                    if primary_image:
                        image_exists = session.execute(text(
                            "SELECT 1 FROM print_images WHERE print_id=:print_id LIMIT 1"
                        ), {"print_id": new_print_id}).scalar_one_or_none()
                        if image_exists is None:
                            session.execute(text(
                                """
                                INSERT INTO print_images (print_id, url, is_primary, source)
                                VALUES (:print_id, :url, TRUE, 'tcgdex-shared-card-art')
                                """
                            ), {"print_id": new_print_id, "url": primary_image})
                            counters["shared_images_copied"] += 1

            # Hard postconditions inside the same transaction.
            exact_variant_rows = int(session.execute(text(
                """
                SELECT COUNT(*)
                FROM print_identifiers pi
                WHERE pi.source=:source
                  AND split_part(pi.external_id, '#', 1) = ANY(:ids)
                """
            ), {"source": VARIANT_SOURCE, "ids": list(candidates_by_source)}).scalar_one())
            additional_rows = int(session.execute(text(
                """
                SELECT COUNT(*)
                FROM prints p
                JOIN cards c ON c.id=p.card_id
                WHERE c.tcgdex_id = ANY(:ids)
                  AND p.tcgdex_id IS NULL
                  AND p.variant LIKE 'v2-%'
                """
            ), {"ids": list(candidates_by_source)}).scalar_one())
            unresolved_default = int(session.execute(text(
                """
                SELECT COUNT(*)
                FROM prints p
                JOIN cards c ON c.id=p.card_id
                WHERE c.tcgdex_id = ANY(:ids)
                  AND p.tcgdex_id=c.tcgdex_id
                  AND p.variant='default'
                """
            ), {"ids": list(candidates_by_source)}).scalar_one())
            duplicate_exact_keys = int(session.execute(text(
                """
                SELECT COUNT(*) FROM (
                  SELECT print_key FROM prints
                  WHERE print_key LIKE 'pokemon:tcgdex:%:en:v2-%'
                  GROUP BY print_key HAVING COUNT(*) > 1
                ) duplicates
                """
            )).scalar_one())

            if exact_variant_rows != EXPECTED_SAFE_VARIANTS:
                raise AssertionError(f"Variant identity postcondition failed: {exact_variant_rows} != {EXPECTED_SAFE_VARIANTS}")
            if additional_rows != EXPECTED_ADDITIONAL_PRINTS:
                raise AssertionError(f"Additional Print postcondition failed: {additional_rows} != {EXPECTED_ADDITIONAL_PRINTS}")
            if unresolved_default:
                raise AssertionError(f"{unresolved_default} cards with source variants still have default baseline Prints")
            if duplicate_exact_keys:
                raise AssertionError(f"{duplicate_exact_keys} duplicate exact variant Print keys")

        final = {
            "pokemon_prints_total": int(session.execute(text(
                "SELECT COUNT(*) FROM prints p JOIN cards c ON c.id=p.card_id JOIN games g ON g.id=c.game_id WHERE g.slug='pokemon'"
            )).scalar_one()),
            "exact_variant_identifiers": int(session.execute(text(
                "SELECT COUNT(*) FROM print_identifiers WHERE source=:source"
            ), {"source": VARIANT_SOURCE}).scalar_one()),
            "additional_v2_prints": int(session.execute(text(
                """
                SELECT COUNT(*) FROM prints p
                JOIN cards c ON c.id=p.card_id JOIN games g ON g.id=c.game_id
                WHERE g.slug='pokemon' AND p.tcgdex_id IS NULL AND p.variant LIKE 'v2-%'
                """
            )).scalar_one()),
        }

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": "transactional_physical_variant_expansion",
        "source": SOURCE,
        "source_version": source_version,
        "canonical_english_cards": EXPECTED_CANONICAL_ENGLISH,
        "safe_variant_definitions": EXPECTED_SAFE_VARIANTS,
        "cards_with_variant_definitions": EXPECTED_CARDS_WITH_VARIANTS,
        "additional_prints_expected": EXPECTED_ADDITIONAL_PRINTS,
        "mutations": counters,
        "after": final,
        "image_policy": "additional physical variants reuse source card art and are marked tcgdex-shared-card-art; image is not claimed variant-exact",
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
