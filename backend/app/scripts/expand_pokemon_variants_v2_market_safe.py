from __future__ import annotations

import argparse
import csv
import io
import json
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import text

from app import db
from app.scripts import expand_pokemon_variants_v2 as base


SOURCE = base.SOURCE
VARIANT_SOURCE = base.VARIANT_SOURCE
EXPECTED_CANONICAL_ENGLISH = base.EXPECTED_CANONICAL_ENGLISH
EXPECTED_SAFE_VARIANTS = base.EXPECTED_SAFE_VARIANTS
EXPECTED_CARDS_WITH_VARIANTS = base.EXPECTED_CARDS_WITH_VARIANTS
EXPECTED_ADDITIONAL_PRINTS = base.EXPECTED_ADDITIONAL_PRINTS


def _write_json(path: Path | None, payload: dict) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def _copy_buffer(rows: list[tuple]) -> io.StringIO:
    buffer = io.StringIO()
    writer = csv.writer(
        buffer,
        delimiter="\t",
        quotechar='"',
        quoting=csv.QUOTE_ALL,
        lineterminator="\n",
    )
    writer.writerows(rows)
    buffer.seek(0)
    return buffer


def run(
    snapshot_path: Path,
    manifest_path: Path,
    *,
    backup_path: Path | None = None,
    report_path: Path | None = None,
) -> dict:
    snapshot = base.load_snapshot(snapshot_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("status") != "pass":
        raise AssertionError("Pinned rich snapshot exporter did not pass")
    source_version = str(manifest.get("source_version") or "").strip()
    if not source_version:
        raise AssertionError("Pinned rich source version missing")

    db.init_engine()
    with db.SessionLocal() as session:
        baseline_rows = [dict(row) for row in session.execute(text(
            """
            SELECT
              c.tcgdex_id, c.id AS card_id,
              p.id AS baseline_print_id, p.set_id, p.collector_number,
              p.language, p.rarity, p.is_foil, p.variant, p.print_key,
              pa.attributes_json AS print_attributes,
              (
                SELECT pi.url FROM print_images pi
                WHERE pi.print_id=p.id
                ORDER BY pi.is_primary DESC, pi.id ASC
                LIMIT 1
              ) AS primary_image_url
            FROM cards c
            JOIN games g ON g.id=c.game_id
            JOIN card_attributes ca ON ca.card_id=c.id
              AND ca.source=:source AND ca.source_version=:version
            JOIN prints p ON p.card_id=c.id AND p.tcgdex_id=c.tcgdex_id
            JOIN print_attributes pa ON pa.print_id=p.id
              AND pa.source=:source AND pa.source_version=:version
            WHERE g.slug='pokemon' AND c.tcgdex_id IS NOT NULL
            ORDER BY c.tcgdex_id
            """
        ), {"source": SOURCE, "version": source_version}).mappings().all()]
        neon_by_source = {str(row["tcgdex_id"]): row for row in baseline_rows}
        if len(neon_by_source) != EXPECTED_CANONICAL_ENGLISH:
            raise AssertionError(
                f"Certified enriched Pokémon baseline moved: {len(neon_by_source)} != {EXPECTED_CANONICAL_ENGLISH}"
            )
        if set(neon_by_source) - set(snapshot):
            raise AssertionError("Certified Neon baseline contains IDs absent from pinned snapshot")

        candidates_by_source: dict[str, list[dict]] = {}
        for source_id in sorted(neon_by_source):
            variants = (snapshot[source_id].get("attributes") or {}).get("variants")
            safe: list[dict] = []
            ambiguous: list[dict] = []
            if isinstance(variants, list):
                safe, ambiguous, _foreign = base._detailed_candidates(source_id, variants)
            elif isinstance(variants, dict):
                safe, ambiguous = base._legacy_candidates(source_id, variants)
            if ambiguous:
                raise AssertionError(f"Variant expansion refused: {source_id} still has ambiguous variants")
            if safe:
                candidates_by_source[source_id] = sorted(safe, key=base._candidate_sort_key)

        total_variants = sum(len(items) for items in candidates_by_source.values())
        additional_expected = sum(max(0, len(items) - 1) for items in candidates_by_source.values())
        if len(candidates_by_source) != EXPECTED_CARDS_WITH_VARIANTS:
            raise AssertionError(f"Cards with variants moved: {len(candidates_by_source)} != {EXPECTED_CARDS_WITH_VARIANTS}")
        if total_variants != EXPECTED_SAFE_VARIANTS:
            raise AssertionError(f"Safe variant definitions moved: {total_variants} != {EXPECTED_SAFE_VARIANTS}")
        if additional_expected != EXPECTED_ADDITIONAL_PRINTS:
            raise AssertionError(f"Additional Print plan moved: {additional_expected} != {EXPECTED_ADDITIONAL_PRINTS}")

        baseline_ids = [int(neon_by_source[source_id]["baseline_print_id"]) for source_id in candidates_by_source]
        backup_rows = [dict(row) for row in session.execute(text(
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
        existing_additional = int(session.execute(text(
            """
            SELECT COUNT(*) FROM prints
            WHERE print_key LIKE 'pokemon:tcgdex:%:en:v2-%' AND tcgdex_id IS NULL
            """
        )).scalar_one())
        _write_json(backup_path, {
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "source_version": source_version,
            "baseline_prints_before": backup_rows,
            "existing_additional_v2_prints_before": existing_additional,
        })
        session.rollback()

    staged_rows: list[tuple] = []
    tuple_keys: set[tuple] = set()
    print_keys: set[str] = set()
    for source_id in sorted(candidates_by_source):
        source_row = neon_by_source[source_id]
        candidates = candidates_by_source[source_id]
        for idx, candidate in enumerate(candidates):
            is_baseline = idx == 0
            variant_name = base._variant_name(candidate)
            print_key = base._variant_print_key(source_id, candidate)
            if print_key in print_keys:
                raise AssertionError(f"Duplicate staged Print key: {print_key}")
            print_keys.add(print_key)

            tuple_key = (
                int(source_row["set_id"]),
                str(source_row["collector_number"]),
                "en",
                bool(candidate["is_foil"]),
                variant_name,
            )
            if tuple_key in tuple_keys:
                raise AssertionError(f"Duplicate staged physical tuple: {tuple_key}")
            tuple_keys.add(tuple_key)

            attrs = base._merge_print_attributes(
                source_row.get("print_attributes"),
                candidate,
                source_id,
                source_version,
                baseline=is_baseline,
            )
            staged_rows.append((
                source_id,
                candidate["variant_hash"],
                bool(is_baseline),
                int(source_row["baseline_print_id"]),
                int(source_row["set_id"]),
                int(source_row["card_id"]),
                str(source_row["collector_number"]),
                "en",
                str(source_row["rarity"]),
                bool(candidate["is_foil"]),
                variant_name,
                print_key,
                json.dumps(attrs, ensure_ascii=False, separators=(",", ":"), default=str),
                json.dumps(candidate.get("third_party") or {}, ensure_ascii=False, separators=(",", ":"), default=str),
                f"{source_id}#{candidate['variant_hash']}",
                str(source_row.get("primary_image_url") or ""),
            ))

    if len(staged_rows) != EXPECTED_SAFE_VARIANTS:
        raise AssertionError(f"Variant stage row count mismatch: {len(staged_rows)}")

    raw = db.engine.raw_connection()
    counters: dict[str, int] = {}
    market_evidence: dict[str, int] = {}
    try:
        raw.autocommit = False
        cur = raw.cursor()
        cur.execute(
            """
            CREATE TEMP TABLE pokemon_variant_stage (
              source_id TEXT NOT NULL,
              variant_hash TEXT NOT NULL,
              is_baseline BOOLEAN NOT NULL,
              baseline_print_id BIGINT NOT NULL,
              set_id BIGINT NOT NULL,
              card_id BIGINT NOT NULL,
              collector_number TEXT NOT NULL,
              language TEXT NOT NULL,
              rarity TEXT NOT NULL,
              is_foil BOOLEAN NOT NULL,
              variant_name TEXT NOT NULL,
              print_key TEXT NOT NULL,
              attributes_json JSONB NOT NULL,
              third_party JSONB NOT NULL,
              variant_external_id TEXT NOT NULL,
              primary_image_url TEXT NOT NULL
            ) ON COMMIT DROP
            """
        )
        cur.copy_expert(
            """
            COPY pokemon_variant_stage (
              source_id, variant_hash, is_baseline, baseline_print_id,
              set_id, card_id, collector_number, language, rarity, is_foil,
              variant_name, print_key, attributes_json, third_party,
              variant_external_id, primary_image_url
            ) FROM STDIN WITH (FORMAT CSV, DELIMITER E'\\t', QUOTE '"', ESCAPE '"')
            """,
            _copy_buffer(staged_rows),
        )
        cur.execute("SELECT COUNT(*), COUNT(*) FILTER (WHERE NOT is_baseline) FROM pokemon_variant_stage")
        stage_count, stage_additional = map(int, cur.fetchone())
        if stage_count != EXPECTED_SAFE_VARIANTS or stage_additional != EXPECTED_ADDITIONAL_PRINTS:
            raise AssertionError(f"Variant COPY stage mismatch: rows={stage_count}, additional={stage_additional}")

        cur.execute(
            """
            SELECT COUNT(*)
            FROM pokemon_variant_stage s
            JOIN prints p ON p.print_key=s.print_key
            WHERE p.card_id <> s.card_id OR p.set_id <> s.set_id
            """
        )
        if int(cur.fetchone()[0]):
            raise AssertionError("A staged Print key already belongs to another canonical identity")

        cur.execute(
            """
            SELECT COUNT(*)
            FROM pokemon_variant_stage s
            JOIN prints p
              ON p.set_id=s.set_id
             AND p.collector_number=s.collector_number
             AND COALESCE(p.language,'')=s.language
             AND p.is_foil=s.is_foil
             AND p.variant=s.variant_name
            WHERE p.id <> s.baseline_print_id
              AND p.print_key IS DISTINCT FROM s.print_key
            """
        )
        if int(cur.fetchone()[0]):
            raise AssertionError("Physical tuple conflicts detected before expansion")

        cur.execute(
            """
            SELECT COUNT(*)
            FROM pokemon_variant_stage s
            JOIN prints p ON p.print_key=s.print_key
            WHERE NOT s.is_baseline AND p.tcgdex_id IS NULL
            """
        )
        existing_additional_in_plan = int(cur.fetchone()[0])

        cur.execute(
            """
            UPDATE prints p
            SET is_foil=s.is_foil,
                variant=s.variant_name,
                print_key=s.print_key
            FROM pokemon_variant_stage s
            WHERE s.is_baseline AND p.id=s.baseline_print_id
            """
        )
        baseline_reclassified = int(cur.rowcount)

        cur.execute(
            """
            INSERT INTO prints (
              set_id, card_id, collector_number, language, rarity,
              is_foil, variant, print_key, tcgdex_id
            )
            SELECT
              s.set_id, s.card_id, s.collector_number, s.language, s.rarity,
              s.is_foil, s.variant_name, s.print_key, NULL
            FROM pokemon_variant_stage s
            WHERE NOT s.is_baseline
            ON CONFLICT (print_key) DO UPDATE SET
              set_id=EXCLUDED.set_id,
              card_id=EXCLUDED.card_id,
              collector_number=EXCLUDED.collector_number,
              language=EXCLUDED.language,
              rarity=EXCLUDED.rarity,
              is_foil=EXCLUDED.is_foil,
              variant=EXCLUDED.variant
            """
        )
        additional_touched = int(cur.rowcount)

        cur.execute(
            """
            INSERT INTO print_attributes (print_id, attributes_json, source, source_version, updated_at)
            SELECT p.id, s.attributes_json, %s, %s, now()
            FROM pokemon_variant_stage s
            JOIN prints p ON p.print_key=s.print_key
            ON CONFLICT (print_id) DO UPDATE SET
              attributes_json=EXCLUDED.attributes_json,
              source=EXCLUDED.source,
              source_version=EXCLUDED.source_version,
              updated_at=now()
            """,
            (SOURCE, source_version),
        )
        attribute_rows = int(cur.rowcount)

        # This identifier is our exact physical identity and must map 1:1.
        cur.execute(
            """
            INSERT INTO print_identifiers (print_id, source, external_id)
            SELECT p.id, %s, s.variant_external_id
            FROM pokemon_variant_stage s
            JOIN prints p ON p.print_key=s.print_key
            ON CONFLICT (print_id, source) DO UPDATE SET external_id=EXCLUDED.external_id
            """,
            (VARIANT_SOURCE,),
        )
        variant_identifiers = int(cur.rowcount)

        # Marketplace product IDs are evidence, not automatically exact Print
        # identity. Build a reviewable relation table from the raw source rows.
        cur.execute(
            """
            CREATE TEMP TABLE pokemon_market_reference_stage ON COMMIT DROP AS
            SELECT p.id AS print_id, provider.key::text AS source,
                   provider.value::text AS external_id
            FROM pokemon_variant_stage s
            JOIN prints p ON p.print_key=s.print_key
            CROSS JOIN LATERAL jsonb_each_text(s.third_party) provider
            WHERE provider.value <> ''
            """
        )
        cur.execute("SELECT COUNT(*) FROM pokemon_market_reference_stage")
        market_reference_rows = int(cur.fetchone()[0])
        cur.execute(
            """
            SELECT COUNT(*) FROM (
              SELECT source, external_id
              FROM pokemon_market_reference_stage
              GROUP BY source, external_id
              HAVING COUNT(*) > 1
            ) shared
            """
        )
        shared_market_ids = int(cur.fetchone()[0])
        cur.execute(
            """
            SELECT COUNT(*)
            FROM pokemon_market_reference_stage r
            JOIN (
              SELECT source, external_id
              FROM pokemon_market_reference_stage
              GROUP BY source, external_id
              HAVING COUNT(*) = 1
            ) u USING (source, external_id)
            LEFT JOIN print_identifiers exact
              ON exact.source=r.source AND exact.external_id=r.external_id
            WHERE exact.id IS NOT NULL AND exact.print_id <> r.print_id
            """
        )
        unique_external_owner_conflicts = int(cur.fetchone()[0])
        cur.execute(
            """
            SELECT COUNT(*)
            FROM pokemon_market_reference_stage r
            JOIN (
              SELECT source, external_id
              FROM pokemon_market_reference_stage
              GROUP BY source, external_id
              HAVING COUNT(*) = 1
            ) u USING (source, external_id)
            JOIN print_identifiers same_source
              ON same_source.print_id=r.print_id AND same_source.source=r.source
            WHERE same_source.external_id <> r.external_id
            """
        )
        unique_print_source_conflicts = int(cur.fetchone()[0])

        # Promote only genuinely 1:1 references with no conflicting owner and no
        # competing identifier from the same marketplace on that Print.
        cur.execute(
            """
            INSERT INTO print_identifiers (print_id, source, external_id)
            SELECT r.print_id, r.source, r.external_id
            FROM pokemon_market_reference_stage r
            JOIN (
              SELECT source, external_id
              FROM pokemon_market_reference_stage
              GROUP BY source, external_id
              HAVING COUNT(*) = 1
            ) u USING (source, external_id)
            LEFT JOIN print_identifiers external_owner
              ON external_owner.source=r.source AND external_owner.external_id=r.external_id
            LEFT JOIN print_identifiers print_source
              ON print_source.print_id=r.print_id AND print_source.source=r.source
            WHERE (external_owner.id IS NULL OR external_owner.print_id=r.print_id)
              AND (print_source.id IS NULL OR print_source.external_id=r.external_id)
            ON CONFLICT DO NOTHING
            """
        )
        promoted_market_identifiers = int(cur.rowcount)

        cur.execute(
            """
            INSERT INTO print_images (print_id, url, is_primary, source)
            SELECT p.id, s.primary_image_url, TRUE, 'tcgdex-shared-card-art'
            FROM pokemon_variant_stage s
            JOIN prints p ON p.print_key=s.print_key
            WHERE NOT s.is_baseline
              AND s.primary_image_url <> ''
              AND NOT EXISTS (SELECT 1 FROM print_images pi WHERE pi.print_id=p.id)
            """
        )
        shared_images = int(cur.rowcount)

        cur.execute(
            """
            SELECT
              COUNT(*) AS matched_prints,
              COUNT(*) FILTER (WHERE NOT s.is_baseline AND p.tcgdex_id IS NULL) AS additional_prints,
              COUNT(*) FILTER (WHERE pi.external_id=s.variant_external_id) AS exact_variant_identifiers,
              COUNT(*) FILTER (
                WHERE pa.attributes_json->'physical_variant'->>'variant_hash'=s.variant_hash
              ) AS exact_variant_attributes,
              COUNT(*) FILTER (WHERE s.is_baseline AND p.tcgdex_id=s.source_id) AS baseline_tcgdex_preserved,
              COUNT(*) FILTER (WHERE NOT s.is_baseline AND p.tcgdex_id IS NOT NULL) AS additional_with_tcgdex_id
            FROM pokemon_variant_stage s
            JOIN prints p ON p.print_key=s.print_key
            LEFT JOIN print_identifiers pi ON pi.print_id=p.id AND pi.source=%s
            LEFT JOIN print_attributes pa ON pa.print_id=p.id
            """,
            (VARIANT_SOURCE,),
        )
        (
            matched_prints,
            additional_prints,
            exact_variant_identifiers,
            exact_variant_attributes,
            baseline_tcgdex_preserved,
            additional_with_tcgdex_id,
        ) = map(int, cur.fetchone())

        cur.execute(
            """
            SELECT COUNT(*) FROM (
              SELECT print_key FROM prints
              WHERE print_key LIKE 'pokemon:tcgdex:%:en:v2-%'
              GROUP BY print_key HAVING COUNT(*) > 1
            ) duplicate_keys
            """
        )
        duplicate_exact_keys = int(cur.fetchone()[0])

        if matched_prints != EXPECTED_SAFE_VARIANTS:
            raise AssertionError(f"Variant Print postcondition failed: {matched_prints}")
        if additional_prints != EXPECTED_ADDITIONAL_PRINTS:
            raise AssertionError(f"Additional Print postcondition failed: {additional_prints}")
        if exact_variant_identifiers != EXPECTED_SAFE_VARIANTS:
            raise AssertionError(f"Variant identifier postcondition failed: {exact_variant_identifiers}")
        if exact_variant_attributes != EXPECTED_SAFE_VARIANTS:
            raise AssertionError(f"Variant attributes postcondition failed: {exact_variant_attributes}")
        if baseline_tcgdex_preserved != EXPECTED_CARDS_WITH_VARIANTS:
            raise AssertionError(f"Baseline TCGdex IDs not preserved: {baseline_tcgdex_preserved}")
        if additional_with_tcgdex_id:
            raise AssertionError(f"{additional_with_tcgdex_id} additional Prints incorrectly received TCGdex IDs")
        if duplicate_exact_keys:
            raise AssertionError(f"{duplicate_exact_keys} duplicate exact variant Print keys")

        counters = {
            "baseline_prints_reclassified": baseline_reclassified,
            "additional_prints_inserted": EXPECTED_ADDITIONAL_PRINTS - existing_additional_in_plan,
            "additional_prints_touched": additional_touched,
            "variant_attribute_rows_upserted": attribute_rows,
            "exact_variant_identifiers_upserted": variant_identifiers,
            "shared_images_copied": shared_images,
        }
        market_evidence = {
            "raw_market_reference_rows": market_reference_rows,
            "shared_market_product_ids": shared_market_ids,
            "unique_external_owner_conflicts": unique_external_owner_conflicts,
            "unique_print_source_conflicts": unique_print_source_conflicts,
            "safe_market_identifiers_promoted": promoted_market_identifiers,
        }
        raw.commit()
    except Exception:
        raw.rollback()
        raise
    finally:
        raw.close()

    with db.SessionLocal() as session:
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
        "mode": "transactional_physical_variant_expansion_with_market_reference_separation",
        "source": SOURCE,
        "source_version": source_version,
        "canonical_english_cards": EXPECTED_CANONICAL_ENGLISH,
        "safe_variant_definitions": EXPECTED_SAFE_VARIANTS,
        "cards_with_variant_definitions": EXPECTED_CARDS_WITH_VARIANTS,
        "additional_prints_expected": EXPECTED_ADDITIONAL_PRINTS,
        "mutations": counters,
        "market_reference_resolution": market_evidence,
        "after": final,
        "market_identity_policy": "tcgdex-variant-v2 is exact physical identity; marketplace product IDs are promoted only when 1:1 and non-conflicting; shared IDs remain in print_attributes for later Entity Resolution",
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
        snapshot_path=args.snapshot,
        manifest_path=args.manifest,
        backup_path=args.backup_path,
        report_path=args.report_path,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
