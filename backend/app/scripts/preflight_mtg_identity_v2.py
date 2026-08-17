from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import requests
from sqlalchemy import text

from app import db
from app.ingest.connectors.scryfall_mtg_v2 import ScryfallMtgV2Connector


NEON_LIMIT_MIB = 512.0
PROJECT_SAFETY_CEILING_MIB = 480.0
MIN_OPERATIONAL_RESERVE_MIB = 32.0
KNOWN_FINISHES = {"nonfoil", "foil", "etched"}


def _write(path: Path | None, payload: dict) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def _table_exists(session, table: str) -> bool:
    return bool(session.execute(text("SELECT to_regclass(:name) IS NOT NULL"), {"name": f"public.{table}"}).scalar_one())


def _scalar(session, sql: str, params: dict | None = None) -> int:
    return int(session.execute(text(sql), params or {}).scalar_one() or 0)


def _table_metric(session, table: str) -> dict:
    if not _table_exists(session, table):
        return {"exists": False, "rows": 0, "total_bytes": 0, "heap_bytes": 0, "index_bytes": 0, "bytes_per_row": 0.0}
    row = dict(session.execute(text(
        """
        SELECT
          pg_total_relation_size(:table)::bigint AS total_bytes,
          pg_relation_size(:table)::bigint AS heap_bytes,
          pg_indexes_size(:table)::bigint AS index_bytes
        """
    ), {"table": table}).mappings().one())
    rows = _scalar(session, f'SELECT COUNT(*) FROM "{table}"')
    total = int(row["total_bytes"] or 0)
    return {
        "exists": True,
        "rows": rows,
        "total_bytes": total,
        "heap_bytes": int(row["heap_bytes"] or 0),
        "index_bytes": int(row["index_bytes"] or 0),
        "total_mib": round(total / 1024 / 1024, 2),
        "bytes_per_row": round(total / rows, 2) if rows else 0.0,
    }


def _finish_values(card: dict) -> tuple[str, ...]:
    raw = card.get("finishes")
    values: set[str] = set()
    if isinstance(raw, list):
        values.update(str(value or "").strip().lower() for value in raw if str(value or "").strip())
    if not values:
        if bool(card.get("nonfoil")):
            values.add("nonfoil")
        if bool(card.get("foil")):
            values.add("foil")
    return tuple(sorted(values))


def _is_paper(card: dict) -> bool:
    games = card.get("games")
    if not isinstance(games, list):
        return True
    return "paper" in {str(value or "").strip().lower() for value in games}


def _card_image_present(card: dict) -> bool:
    if card.get("image_uris"):
        return True
    return any(bool(face.get("image_uris")) for face in (card.get("card_faces") or []) if isinstance(face, dict))


def _iter_bulk_rows(connector: ScryfallMtgV2Connector, download_url: str):
    headers = {
        "User-Agent": connector._SCRYFALL_HEADERS["User-Agent"],
        "Accept": "application/gzip,application/jsonl,application/x-ndjson,*/*;q=0.8",
    }
    with requests.get(download_url, headers=headers, stream=True, timeout=240) as response:
        response.raise_for_status()
        response.raw.decode_content = False
        content_type = str(response.headers.get("Content-Type") or "").lower()
        is_gzip = download_url.lower().endswith(".gz") or "gzip" in content_type
        if is_gzip:
            with gzip.GzipFile(fileobj=response.raw, mode="rb") as compressed:
                with io.TextIOWrapper(compressed, encoding="utf-8") as stream:
                    for line in stream:
                        line = line.strip()
                        if line:
                            yield line
        else:
            for raw in response.iter_lines(decode_unicode=True):
                line = str(raw or "").strip()
                if line:
                    yield line


def _project_relation(metric: dict, rows: int, *, multiplier: float = 1.0, fallback_bytes: float = 256.0) -> int:
    per_row = float(metric.get("bytes_per_row") or 0.0) or fallback_bytes
    return int(rows * per_row * multiplier)


def _scenario(name: str, current_db_bytes: int, additions: dict[str, int]) -> dict:
    added = sum(max(int(value), 0) for value in additions.values())
    projected = current_db_bytes + added
    limit_bytes = int(NEON_LIMIT_MIB * 1024 * 1024)
    safety_bytes = int(PROJECT_SAFETY_CEILING_MIB * 1024 * 1024)
    reserve_bytes = int(MIN_OPERATIONAL_RESERVE_MIB * 1024 * 1024)
    return {
        "name": name,
        "additions_bytes": additions,
        "estimated_added_mib": round(added / 1024 / 1024, 2),
        "projected_database_mib": round(projected / 1024 / 1024, 2),
        "remaining_to_neon_limit_mib": round((limit_bytes - projected) / 1024 / 1024, 2),
        "within_480_mib_safety_ceiling": projected <= safety_bytes,
        "keeps_32_mib_operational_reserve": projected <= limit_bytes - reserve_bytes,
        "safe_to_materialize": projected <= min(safety_bytes, limit_bytes - reserve_bytes),
        "estimate_is_conservative_not_a_commitment": True,
    }


def run(*, report_path: Path | None = None) -> dict:
    connector = ScryfallMtgV2Connector()
    metadata = connector._bulk_metadata()
    download_url = connector._bulk_download_url(metadata)
    if not download_url:
        raise AssertionError("Scryfall default_cards metadata exposes no downloadable bulk URL")

    db.init_engine()
    with db.SessionLocal() as session:
        database_bytes = _scalar(session, "SELECT pg_database_size(current_database())")
        game_id = session.execute(text("SELECT id FROM games WHERE slug='mtg' LIMIT 1")).scalar_one_or_none()
        game_id = int(game_id) if game_id is not None else None

        relation_names = [
            "sets", "cards", "prints", "print_images", "print_identifiers", "source_records",
            "card_attributes", "print_attributes", "card_search_profiles", "print_search_profiles", "facet_definitions",
        ]
        relation_metrics = {name: _table_metric(session, name) for name in relation_names}

        current = {
            "sets": 0,
            "cards": 0,
            "prints": 0,
            "print_images": 0,
            "print_identifiers": 0,
            "card_attributes": 0,
            "print_attributes": 0,
            "card_search_profiles": 0,
            "print_search_profiles": 0,
            "facet_definitions": 0,
            "search_documents": 0,
            "prices": 0,
            "products": 0,
        }
        current_scryfall_ids: set[str] = set()
        current_oracle_ids: set[str] = set()
        current_set_codes: set[str] = set()
        duplicate_identity_groups = 0
        current_source_checksums: set[str] = set()
        source_records = {"count": 0, "raw_json_bytes": 0, "avg_raw_json_bytes": 0.0}

        if game_id is not None:
            current.update({
                "sets": _scalar(session, "SELECT COUNT(*) FROM sets WHERE game_id=:game", {"game": game_id}),
                "cards": _scalar(session, "SELECT COUNT(*) FROM cards WHERE game_id=:game", {"game": game_id}),
                "prints": _scalar(session, "SELECT COUNT(*) FROM prints p JOIN cards c ON c.id=p.card_id WHERE c.game_id=:game", {"game": game_id}),
                "print_images": _scalar(session, "SELECT COUNT(*) FROM print_images pi JOIN prints p ON p.id=pi.print_id JOIN cards c ON c.id=p.card_id WHERE c.game_id=:game", {"game": game_id}),
                "print_identifiers": _scalar(session, "SELECT COUNT(*) FROM print_identifiers pi JOIN prints p ON p.id=pi.print_id JOIN cards c ON c.id=p.card_id WHERE c.game_id=:game", {"game": game_id}),
                "card_search_profiles": _scalar(session, "SELECT COUNT(*) FROM card_search_profiles WHERE game_id=:game", {"game": game_id}) if _table_exists(session, "card_search_profiles") else 0,
                "print_search_profiles": _scalar(session, "SELECT COUNT(*) FROM print_search_profiles WHERE game_id=:game", {"game": game_id}) if _table_exists(session, "print_search_profiles") else 0,
                "facet_definitions": _scalar(session, "SELECT COUNT(*) FROM facet_definitions WHERE game_id=:game", {"game": game_id}) if _table_exists(session, "facet_definitions") else 0,
                "search_documents": _scalar(session, "SELECT COUNT(*) FROM search_documents WHERE game_id=:game", {"game": game_id}) if _table_exists(session, "search_documents") else 0,
                "prices": _scalar(session, "SELECT COUNT(*) FROM prices WHERE game_id=:game", {"game": game_id}) if _table_exists(session, "prices") else 0,
                "products": _scalar(session, "SELECT COUNT(*) FROM products WHERE game_id=:game", {"game": game_id}) if _table_exists(session, "products") else 0,
            })
            if _table_exists(session, "card_attributes"):
                current["card_attributes"] = _scalar(session, "SELECT COUNT(*) FROM card_attributes ca JOIN cards c ON c.id=ca.card_id WHERE c.game_id=:game", {"game": game_id})
            if _table_exists(session, "print_attributes"):
                current["print_attributes"] = _scalar(session, "SELECT COUNT(*) FROM print_attributes pa JOIN prints p ON p.id=pa.print_id JOIN cards c ON c.id=p.card_id WHERE c.game_id=:game", {"game": game_id})

            current_scryfall_ids = {str(v).strip() for v in session.execute(text("SELECT p.scryfall_id FROM prints p JOIN cards c ON c.id=p.card_id WHERE c.game_id=:game AND p.scryfall_id IS NOT NULL"), {"game": game_id}).scalars().all() if str(v or "").strip()}
            current_oracle_ids = {str(v).strip() for v in session.execute(text("SELECT oracle_id FROM cards WHERE game_id=:game AND oracle_id IS NOT NULL"), {"game": game_id}).scalars().all() if str(v or "").strip()}
            current_set_codes = {str(v).strip().lower() for v in session.execute(text("SELECT code FROM sets WHERE game_id=:game"), {"game": game_id}).scalars().all() if str(v or "").strip()}
            duplicate_identity_groups = _scalar(session, """
                SELECT COUNT(*) FROM (
                  SELECT p.set_id,p.collector_number,p.language,p.is_foil,p.variant
                  FROM prints p JOIN cards c ON c.id=p.card_id
                  WHERE c.game_id=:game
                  GROUP BY p.set_id,p.collector_number,p.language,p.is_foil,p.variant
                  HAVING COUNT(*)>1
                ) q
            """, {"game": game_id})

        source_id = session.execute(text("SELECT id FROM sources WHERE name='scryfall_mtg' LIMIT 1")).scalar_one_or_none()
        if source_id is not None:
            source_id = int(source_id)
            current_source_checksums = {str(v) for v in session.execute(text("SELECT checksum FROM source_records WHERE source_id=:source"), {"source": source_id}).scalars().all()}
            row = dict(session.execute(text("""
                SELECT COUNT(*) AS count,
                       COALESCE(SUM(pg_column_size(raw_json)),0)::bigint AS raw_json_bytes,
                       COALESCE(AVG(pg_column_size(raw_json)),0)::float AS avg_raw_json_bytes
                FROM source_records WHERE source_id=:source
            """), {"source": source_id}).mappings().one())
            source_records = {
                "count": int(row["count"] or 0),
                "raw_json_bytes": int(row["raw_json_bytes"] or 0),
                "avg_raw_json_bytes": round(float(row["avg_raw_json_bytes"] or 0.0), 2),
            }
        session.rollback()

    counters = Counter()
    languages = Counter()
    rarities = Counter()
    layouts = Counter()
    set_types = Counter()
    finish_combinations = Counter()
    finish_values = Counter()
    unknown_finishes = Counter()
    missing_oracle_names = Counter()
    source_ids: set[str] = set()
    source_oracle_ids: set[str] = set()
    source_set_codes: set[str] = set()
    natural_finish_owners: dict[tuple[str, str, str, str], str] = {}
    natural_finish_collisions = 0
    new_source_record_count = 0
    new_source_record_line_bytes = 0
    paper_json_line_bytes = 0
    total_json_line_bytes = 0

    for line in _iter_bulk_rows(connector, download_url):
        counters["bulk_rows"] += 1
        line_bytes = len(line.encode("utf-8"))
        total_json_line_bytes += line_bytes
        try:
            card = json.loads(line)
        except json.JSONDecodeError as exc:
            raise AssertionError("Scryfall bulk row is not valid JSON") from exc
        if not isinstance(card, dict):
            counters["non_object_rows"] += 1
            continue
        if not _is_paper(card):
            counters["non_paper_rows"] += 1
            continue

        counters["paper_rows"] += 1
        paper_json_line_bytes += line_bytes
        sid = str(card.get("id") or "").strip()
        oracle = str(card.get("oracle_id") or "").strip()
        set_code = str(card.get("set") or "").strip().lower()
        collector = str(card.get("collector_number") or "").strip()
        lang = str(card.get("lang") or "").strip().lower() or "unknown"
        name = str(card.get("name") or "").strip()
        rarity = str(card.get("rarity") or "").strip().lower() or "unknown"
        layout = str(card.get("layout") or "").strip().lower() or "unknown"
        set_type = str(card.get("set_type") or "").strip().lower() or "unknown"
        finishes = _finish_values(card)

        languages[lang] += 1
        rarities[rarity] += 1
        layouts[layout] += 1
        set_types[set_type] += 1
        finish_combinations["+".join(finishes) if finishes else "<none>"] += 1
        for finish in finishes:
            finish_values[finish] += 1
            if finish not in KNOWN_FINISHES:
                unknown_finishes[finish] += 1

        if sid:
            if sid in source_ids:
                counters["duplicate_scryfall_id_rows"] += 1
            source_ids.add(sid)
        else:
            counters["missing_scryfall_id_rows"] += 1
        if oracle:
            source_oracle_ids.add(oracle)
        else:
            counters["missing_oracle_rows"] += 1
            missing_oracle_names[name or "<blank>"] += 1
        if set_code:
            source_set_codes.add(set_code)
        else:
            counters["missing_set_code_rows"] += 1
        if not collector:
            counters["missing_collector_number_rows"] += 1
        if lang == "unknown":
            counters["missing_language_rows"] += 1
        if not name:
            counters["missing_name_rows"] += 1
        if not _card_image_present(card):
            counters["rows_without_image_evidence"] += 1
        if len(finishes) > 1:
            counters["multi_finish_rows"] += 1
        if not finishes:
            counters["no_finish_evidence_rows"] += 1
        counters["exact_finish_variants"] += max(len(finishes), 1)

        if bool(card.get("foil")) != ("foil" in finishes):
            counters["foil_flag_finish_mismatches"] += 1
        if bool(card.get("nonfoil")) != ("nonfoil" in finishes):
            counters["nonfoil_flag_finish_mismatches"] += 1

        owner = sid or hashlib.sha1(line.encode("utf-8")).hexdigest()
        for finish in finishes or ("unknown",):
            key = (set_code, collector, lang, finish)
            previous = natural_finish_owners.get(key)
            if previous and previous != owner:
                natural_finish_collisions += 1
            else:
                natural_finish_owners[key] = owner

        canonical_checksum = connector.checksum(card)
        if canonical_checksum not in current_source_checksums:
            new_source_record_count += 1
            new_source_record_line_bytes += line_bytes

    source = {
        "bulk_metadata": {
            key: metadata.get(key)
            for key in ("id", "type", "name", "updated_at", "size", "compressed_size", "content_type", "content_encoding", "uri")
            if metadata.get(key) is not None
        },
        "download_contract": "jsonl/gzip streaming read-only",
        "counts": {
            **{key: int(value) for key, value in counters.items()},
            "unique_scryfall_ids": len(source_ids),
            "unique_oracle_ids": len(source_oracle_ids),
            "unique_set_codes": len(source_set_codes),
            "natural_finish_identity_collisions": natural_finish_collisions,
        },
        "distributions": {
            "languages": dict(languages.most_common()),
            "rarities": dict(rarities.most_common()),
            "layouts": dict(layouts.most_common()),
            "set_types": dict(set_types.most_common()),
            "finish_combinations": dict(finish_combinations.most_common()),
            "finish_values": dict(finish_values.most_common()),
            "unknown_finishes": dict(unknown_finishes.most_common()),
        },
        "missing_oracle_name_groups": {
            "unique_names": len(missing_oracle_names),
            "names_with_multiple_print_rows": sum(1 for count in missing_oracle_names.values() if count > 1),
            "largest_groups": dict(missing_oracle_names.most_common(25)),
        },
        "json_volume": {
            "bulk_uncompressed_line_bytes": total_json_line_bytes,
            "paper_uncompressed_line_bytes": paper_json_line_bytes,
            "new_source_record_rows_if_bootstrapped_now": new_source_record_count,
            "new_source_record_uncompressed_line_bytes": new_source_record_line_bytes,
            "new_source_record_uncompressed_mib": round(new_source_record_line_bytes / 1024 / 1024, 2),
        },
    }

    reconciliation = {
        "source_print_ids_already_in_neon": len(source_ids & current_scryfall_ids),
        "source_print_ids_missing_from_neon": len(source_ids - current_scryfall_ids),
        "neon_print_ids_absent_from_current_source": len(current_scryfall_ids - source_ids),
        "source_oracle_ids_already_in_neon": len(source_oracle_ids & current_oracle_ids),
        "source_oracle_ids_missing_from_neon": len(source_oracle_ids - current_oracle_ids),
        "source_set_codes_already_in_neon": len(source_set_codes & current_set_codes),
        "source_set_codes_missing_from_neon": len(source_set_codes - current_set_codes),
    }

    new_object_prints = max(len(source_ids - current_scryfall_ids), 0)
    unresolved_cards_upper_bound = int(counters["missing_oracle_rows"])
    new_logical_cards_upper_bound = max(len(source_oracle_ids - current_oracle_ids), 0) + unresolved_cards_upper_bound
    new_sets = max(len(source_set_codes - current_set_codes), 0)
    finish_prints_total = int(counters["exact_finish_variants"])
    additional_finish_rows_vs_object_model = max(finish_prints_total - len(source_ids), 0)

    metrics = relation_metrics
    source_index_per_row = 0.0
    sr_metric = metrics["source_records"]
    if sr_metric["rows"]:
        source_index_per_row = sr_metric["index_bytes"] / sr_metric["rows"]
    source_record_estimate = int(new_source_record_line_bytes * 1.08 + new_source_record_count * (source_index_per_row + 96.0))

    core_object_additions = {
        "sets": _project_relation(metrics["sets"], new_sets, fallback_bytes=384),
        "cards": _project_relation(metrics["cards"], new_logical_cards_upper_bound, multiplier=1.10, fallback_bytes=640),
        "prints": _project_relation(metrics["prints"], new_object_prints, multiplier=1.10, fallback_bytes=900),
        "print_images": _project_relation(metrics["print_images"], new_object_prints, multiplier=1.05, fallback_bytes=420),
        "print_identifiers": _project_relation(metrics["print_identifiers"], new_object_prints, multiplier=1.05, fallback_bytes=300),
    }
    core_finish_additions = dict(core_object_additions)
    core_finish_additions["prints"] = _project_relation(metrics["prints"], new_object_prints + additional_finish_rows_vs_object_model, multiplier=1.10, fallback_bytes=900)
    core_finish_additions["print_images"] = _project_relation(metrics["print_images"], new_object_prints + additional_finish_rows_vs_object_model, multiplier=1.05, fallback_bytes=420)
    core_finish_additions["print_identifiers"] = _project_relation(metrics["print_identifiers"], new_object_prints + additional_finish_rows_vs_object_model, multiplier=1.05, fallback_bytes=300)

    attrs_object = {
        "card_attributes": _project_relation(metrics["card_attributes"], new_logical_cards_upper_bound, multiplier=1.25, fallback_bytes=800),
        "print_attributes": _project_relation(metrics["print_attributes"], new_object_prints, multiplier=1.35, fallback_bytes=950),
    }
    attrs_finish = {
        "card_attributes": attrs_object["card_attributes"],
        "print_attributes": _project_relation(metrics["print_attributes"], new_object_prints + additional_finish_rows_vs_object_model, multiplier=1.35, fallback_bytes=950),
    }
    search_object = {
        "card_search_profiles": _project_relation(metrics["card_search_profiles"], new_logical_cards_upper_bound, multiplier=1.15, fallback_bytes=1500),
        "print_search_profiles": _project_relation(metrics["print_search_profiles"], new_object_prints, multiplier=1.20, fallback_bytes=1850),
    }
    search_finish = {
        "card_search_profiles": search_object["card_search_profiles"],
        "print_search_profiles": _project_relation(metrics["print_search_profiles"], new_object_prints + additional_finish_rows_vs_object_model, multiplier=1.20, fallback_bytes=1850),
    }

    scenarios = [
        _scenario("legacy_object_catalog_without_raw_history", database_bytes, core_object_additions),
        _scenario("legacy_object_catalog_with_source_records", database_bytes, core_object_additions | {"source_records": source_record_estimate}),
        _scenario("exact_finish_catalog_without_raw_history", database_bytes, core_finish_additions),
        _scenario("exact_finish_catalog_plus_attributes", database_bytes, core_finish_additions | attrs_finish),
        _scenario("exact_finish_catalog_attributes_search_v2", database_bytes, core_finish_additions | attrs_finish | search_finish),
        _scenario("exact_finish_full_current_ingest_contract", database_bytes, core_finish_additions | attrs_finish | search_finish | {"source_records": source_record_estimate}),
    ]

    hard_blockers = []
    design_blockers = []
    if duplicate_identity_groups:
        hard_blockers.append(f"legacy Neon already has {duplicate_identity_groups} duplicate MTG Print identity groups")
    if counters["duplicate_scryfall_id_rows"]:
        hard_blockers.append(f"Scryfall bulk contains {counters['duplicate_scryfall_id_rows']} duplicate paper Scryfall ID rows")
    if natural_finish_collisions:
        design_blockers.append(f"{natural_finish_collisions} natural (set, collector, language, finish) collisions require Scryfall-ID-aware Print identity")
    if counters["multi_finish_rows"]:
        design_blockers.append(f"{counters['multi_finish_rows']} paper Scryfall objects expose multiple physical finishes and are collapsed by the legacy is_foil model")
    if counters["missing_oracle_rows"]:
        design_blockers.append(f"{counters['missing_oracle_rows']} paper print rows lack oracle_id and need an explicit non-oracle Card identity policy")
    if unknown_finishes:
        design_blockers.append(f"unknown finish values require policy: {dict(unknown_finishes)}")
    if source_record_estimate > 16 * 1024 * 1024:
        design_blockers.append("persisting full Scryfall raw SourceRecord history in Neon is materially expensive and should be redesigned before full bootstrap")

    safe_scenarios = [row["name"] for row in scenarios if row["safe_to_materialize"]]
    status = "fail" if hard_blockers else "review_required" if design_blockers or not safe_scenarios else "pass"

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": "read_only_mtg_identity_v2_preflight",
        "status": status,
        "database": {
            "current_bytes": database_bytes,
            "current_mib": round(database_bytes / 1024 / 1024, 2),
            "neon_limit_mib": NEON_LIMIT_MIB,
            "project_safety_ceiling_mib": PROJECT_SAFETY_CEILING_MIB,
            "physical_headroom_mib": round((NEON_LIMIT_MIB * 1024 * 1024 - database_bytes) / 1024 / 1024, 2),
            "headroom_to_safety_ceiling_mib": round((PROJECT_SAFETY_CEILING_MIB * 1024 * 1024 - database_bytes) / 1024 / 1024, 2),
        },
        "current_neon_mtg": current,
        "current_neon_source_records": source_records,
        "legacy_duplicate_print_identity_groups": duplicate_identity_groups,
        "source": source,
        "reconciliation": reconciliation,
        "proposed_identity_dimensions": {
            "card": "oracle_id when present; missing-oracle objects require explicit source-backed fallback policy",
            "set": "Scryfall set code",
            "source_print": "Scryfall card object id",
            "physical_print_candidate": "Scryfall card object id + certified finish (nonfoil/foil/etched)",
            "collector_number": "Scryfall collector_number",
            "language": "Scryfall lang",
            "warning": "legacy Print.scryfall_id uniqueness cannot represent multiple finish-specific rows with the same Scryfall id without a derived finish identifier strategy",
        },
        "projection_inputs": {
            "new_sets": new_sets,
            "new_logical_cards_upper_bound": new_logical_cards_upper_bound,
            "new_source_print_objects": new_object_prints,
            "source_exact_finish_variants_total": finish_prints_total,
            "additional_finish_rows_vs_object_model": additional_finish_rows_vs_object_model,
            "estimated_new_source_record_bytes": source_record_estimate,
            "relation_metrics": metrics,
        },
        "storage_scenarios": scenarios,
        "safe_scenarios": safe_scenarios,
        "hard_blockers": hard_blockers,
        "design_blockers": design_blockers,
        "database_writes": 0,
        "decision_rule": "Do not run a full MTG bootstrap or Search V2 materialization until exact finish identity, missing-oracle identity and raw SourceRecord storage are resolved and the chosen scenario remains below the 480 MiB project safety ceiling with operational reserve.",
    }
    _write(report_path, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report-path", type=Path, default=None)
    args = parser.parse_args()
    run(report_path=args.report_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
