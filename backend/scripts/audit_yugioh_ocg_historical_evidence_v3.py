from __future__ import annotations

import json
import os
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import psycopg2
from psycopg2.extras import RealDictCursor

GAME = "yugioh"
ACCEPTED = ("accepted", "mapped", "exact")
TARGET_METHODS = (
    "cardmarket_ocg_certified_full_logical_bijection_v1",
    "cardmarket_ocg_certified_independent_ordinal_rarity_near_exact_v1",
    "cardmarket_ocg_certified_independent_ordinal_rarity_next223_v2",
    "cardmarket_ocg_certified_independent_ordinal_rarity_partial_core_v1",
    "cardmarket_ocg_certified_independent_ordinal_rarity_v1",
    "cardmarket_ocg_certified_version_ordinal_v1",
    "cardmarket_ocg_certified_public_code_singleton_v1",
    "cardmarket_ocg_certified_public_code_singleton_v2",
    "cardmarket_ocg_certified_public_super_secret_contract_v1",
    "cardmarket_ocg_certified_public_version_contract_v1",
    "cardmarket_ocg_certified_public_version_contract_v2",
)


def _url() -> str:
    value = os.getenv("DATABASE_URL_UNPOOLED") or os.getenv("DATABASE_URL")
    if not value:
        raise RuntimeError("DATABASE URL required")
    return value


def _safe_scalar(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    text = str(value)
    return text[:500]


def _shape(value: Any, prefix: str = "") -> list[str]:
    out: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            out.append(path)
            if isinstance(child, (dict, list)):
                out.extend(_shape(child, path))
    elif isinstance(value, list):
        marker = f"{prefix}[]" if prefix else "[]"
        out.append(marker)
        for child in value[:3]:
            if isinstance(child, (dict, list)):
                out.extend(_shape(child, marker))
    return out


def _sample_evidence(ev: Any) -> dict:
    if not isinstance(ev, dict):
        return {"raw_type": type(ev).__name__, "value": _safe_scalar(ev)}
    preferred = (
        "source",
        "identity_basis",
        "workflow_run_id",
        "workflow_run_ids",
        "global_audit_workflow_run_id",
        "identity_workflow_run_ids",
        "audit_workflow_run_id",
        "audit_run_id",
        "run_id",
        "idExpansion",
        "expansion_external_id",
        "canonical_set",
        "set_code",
        "idProduct",
        "idMetacard",
        "collector_number",
        "canonical_rarity",
        "canonical_variant",
        "rarity",
        "variant",
        "resolution_method",
        "stable_identity_sha256",
        "frozen_proposal_sha256",
        "proposal_sha256",
        "image_sha256",
        "image_hash",
        "public_code",
        "public_code_source",
        "version",
        "version_ordinal",
        "ordinal",
        "region",
        "language",
    )
    result = {}
    for key in preferred:
        if key not in ev:
            continue
        value = ev[key]
        if isinstance(value, list):
            result[key] = [_safe_scalar(v) if not isinstance(v, dict) else {k: _safe_scalar(x) for k, x in list(v.items())[:12]} for v in value[:20]]
        elif isinstance(value, dict):
            result[key] = {k: _safe_scalar(v) for k, v in list(value.items())[:30]}
        else:
            result[key] = _safe_scalar(value)
    for key, value in ev.items():
        if key in result or key in preferred:
            continue
        if not isinstance(value, (dict, list)):
            result[key] = _safe_scalar(value)
    return result


def main() -> int:
    conn = psycopg2.connect(
        _url(),
        connect_timeout=30,
        application_name="dontripit_ygo_ocg_historical_evidence_v3",
    )
    conn.set_session(readonly=True, autocommit=False)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT id FROM games WHERE slug=%s LIMIT 1", (GAME,))
            game = cur.fetchone()
            if not game:
                raise RuntimeError("Yu-Gi-Oh game missing")
            gid = int(game["id"])
            cur.execute(
                "SELECT max(last_seen_at) capture FROM external_catalog_products "
                "WHERE source='cardmarket' AND game_id=%s AND product_group='single'",
                (gid,),
            )
            capture = cur.fetchone()["capture"]
            cur.execute(
                """
                SELECT l.mapping_method,l.evidence,l.confidence,l.reviewed,l.link_status,
                       e.id external_product_id,e.external_id id_product,e.metacard_external_id,
                       e.expansion_external_id,e.last_seen_at,e.name product_name,
                       p.id print_id,p.card_id,p.collector_number,p.rarity,p.variant,p.language,
                       c.name card_name,s.code set_code
                FROM external_catalog_print_links l
                JOIN external_catalog_products e ON e.id=l.external_product_id
                JOIN prints p ON p.id=l.print_id
                JOIN cards c ON c.id=p.card_id
                JOIN sets s ON s.id=p.set_id
                WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single'
                  AND l.link_status=ANY(%s)
                  AND l.confidence='exact' AND l.reviewed=true
                  AND lower(coalesce(p.language,''))='ja'
                  AND l.mapping_method=ANY(%s)
                ORDER BY l.mapping_method,s.code,e.expansion_external_id,e.external_id::bigint,p.id
                """,
                (gid, list(ACCEPTED), list(TARGET_METHODS)),
            )
            rows = [dict(r) for r in cur.fetchall()]
            conn.rollback()
    finally:
        conn.close()

    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        grouped[str(row["mapping_method"])].append(row)

    methods = {}
    failures = []
    for method in TARGET_METHODS:
        items = grouped.get(method, [])
        key_counts = Counter()
        top_level_counts = Counter()
        sets = Counter()
        expansions = Counter()
        sources = Counter()
        run_ids = Counter()
        hashes = Counter()
        for row in items:
            ev = row.get("evidence")
            if not isinstance(ev, dict):
                continue
            for key in ev:
                top_level_counts[str(key)] += 1
            for path in _shape(ev):
                key_counts[path] += 1
            source = ev.get("source")
            if source is not None:
                sources[str(source)] += 1
            for key in (
                "workflow_run_id", "global_audit_workflow_run_id", "audit_workflow_run_id",
                "audit_run_id", "run_id",
            ):
                if ev.get(key) is not None:
                    run_ids[f"{key}:{ev[key]}"] += 1
            for key in ("workflow_run_ids", "identity_workflow_run_ids"):
                value = ev.get(key)
                if isinstance(value, list):
                    for run_id in value:
                        run_ids[f"{key}:{run_id}"] += 1
            for key in (
                "stable_identity_sha256", "frozen_proposal_sha256", "proposal_sha256",
                "image_sha256", "image_hash",
            ):
                if ev.get(key):
                    hashes[f"{key}:{ev[key]}"] += 1
            sets[str(row.get("set_code") or "")] += 1
            expansions[str(row.get("expansion_external_id") or "")] += 1

        samples = []
        seen_shapes = set()
        for row in items:
            ev = row.get("evidence")
            shape = tuple(sorted(_shape(ev))) if isinstance(ev, (dict, list)) else (type(ev).__name__,)
            fingerprint = shape[:100]
            if fingerprint in seen_shapes and len(samples) >= 5:
                continue
            seen_shapes.add(fingerprint)
            samples.append(
                {
                    "external_product_id": int(row["external_product_id"]),
                    "idProduct": str(row["id_product"]),
                    "idMetacard": str(row.get("metacard_external_id") or ""),
                    "print_id": int(row["print_id"]),
                    "set_code": str(row.get("set_code") or ""),
                    "idExpansion": str(row.get("expansion_external_id") or ""),
                    "product_name": str(row.get("product_name") or ""),
                    "card_name": str(row.get("card_name") or ""),
                    "collector_number": str(row.get("collector_number") or ""),
                    "rarity": row.get("rarity"),
                    "variant": row.get("variant"),
                    "evidence": _sample_evidence(ev),
                }
            )
            if len(samples) >= 12:
                break

        product_count = len({int(r["external_product_id"]) for r in items})
        print_count = len({int(r["print_id"]) for r in items})
        if len(items) != product_count or len(items) != print_count:
            failures.append(f"historical_one_to_one_drift:{method}")
        methods[method] = {
            "rows": len(items),
            "unique_products": product_count,
            "unique_prints": print_count,
            "current_catalog_rows": sum(r.get("last_seen_at") == capture for r in items),
            "top_sets": dict(sets.most_common(30)),
            "top_expansions": dict(expansions.most_common(30)),
            "evidence_top_level_keys": dict(top_level_counts.most_common()),
            "evidence_shape_paths": dict(key_counts.most_common(100)),
            "sources": dict(sources.most_common()),
            "workflow_run_references": dict(run_ids.most_common(50)),
            "hash_references": dict(hashes.most_common(50)),
            "samples": samples,
        }

    report = {
        "status": "PASS" if not failures else "FAIL",
        "mode": "read_only",
        "production_writes": 0,
        "game": GAME,
        "cardmarket_capture": str(capture),
        "target_methods": len(TARGET_METHODS),
        "target_rows": len(rows),
        "methods": methods,
        "failures": failures,
        "contract": {
            "purpose": "inventory stored provenance to reconstruct independent contemporary physical-identity replays",
            "evidence_is_not_proof": True,
            "writes_allowed": False,
        },
    }
    out = Path(os.getenv("YGO_OCG_HISTORICAL_EVIDENCE_V3_OUTPUT", "/tmp/yugioh-ocg-historical-evidence-v3.json"))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True, default=str))
    return 0 if not failures else 2


if __name__ == "__main__":
    raise SystemExit(main())
