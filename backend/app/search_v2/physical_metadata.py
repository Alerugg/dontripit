from __future__ import annotations

from collections import defaultdict
from typing import Any

from sqlalchemy import bindparam, text

from app.search_v2.output_contract import clean_optional_metadata


_RARITY_CONSENSUS_METHOD = "sibling_consensus_v1"


def enrich_representative_rarity_by_consensus(
    session,
    items: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Fill missing representative rarity from strict sibling consensus.

    This is a read-only presentation enrichment. It never changes the selected
    representative ``print_id`` (and therefore never changes the exact market
    identity/pricing semantics attached to that print).

    A missing rarity is filled only when every known rarity among prints with
    the same canonical card, set, collector number and language agrees after
    normalization. Placeholders and blank values are ignored. Conflicts remain
    unresolved rather than being guessed.
    """

    representative_ids: list[int] = []
    missing_items_by_id: dict[int, list[dict[str, Any]]] = defaultdict(list)

    for item in items:
        matched = item.get("matched_print")
        if not isinstance(matched, dict):
            continue

        own_rarity = clean_optional_metadata(matched.get("rarity"))
        if own_rarity is not None:
            # Preserve source-owned values exactly apart from the existing
            # placeholder/whitespace normalization contract.
            matched["rarity"] = own_rarity
            continue

        matched["rarity"] = None
        try:
            representative_id = int(matched.get("print_id"))
        except (TypeError, ValueError):
            continue
        if representative_id <= 0:
            continue

        if representative_id not in missing_items_by_id:
            representative_ids.append(representative_id)
        missing_items_by_id[representative_id].append(matched)

    if not representative_ids:
        return items

    sibling_sql = text(
        """
        SELECT
          representative.id AS representative_print_id,
          sibling.id AS sibling_print_id,
          sibling.rarity AS sibling_rarity
        FROM prints representative
        JOIN prints sibling
          ON sibling.card_id = representative.card_id
         AND sibling.set_id = representative.set_id
         AND COALESCE(TRIM(sibling.collector_number), '') =
             COALESCE(TRIM(representative.collector_number), '')
         AND LOWER(COALESCE(TRIM(sibling.language), '')) =
             LOWER(COALESCE(TRIM(representative.language), ''))
        WHERE representative.id IN :representative_ids
        ORDER BY representative.id ASC, sibling.id ASC
        """
    ).bindparams(bindparam("representative_ids", expanding=True))

    rows = session.execute(
        sibling_sql,
        {"representative_ids": representative_ids},
    ).mappings().all()

    # representative id -> normalized rarity -> {display value, evidence ids}
    evidence: dict[int, dict[str, dict[str, Any]]] = defaultdict(dict)
    for row in rows:
        rarity = clean_optional_metadata(row.get("sibling_rarity"))
        if rarity is None:
            continue
        display_value = str(rarity).strip()
        normalized = display_value.casefold()
        representative_id = int(row["representative_print_id"])
        bucket = evidence[representative_id].setdefault(
            normalized,
            {"value": display_value, "print_ids": []},
        )
        bucket["print_ids"].append(int(row["sibling_print_id"]))

    for representative_id, matched_items in missing_items_by_id.items():
        rarity_buckets = evidence.get(representative_id) or {}
        if len(rarity_buckets) != 1:
            # Zero evidence or conflicting evidence: leave the public value
            # missing. No fuzzy/default rarity is ever promoted to fact.
            continue

        consensus = next(iter(rarity_buckets.values()))
        for matched in matched_items:
            matched["rarity"] = consensus["value"]
            matched["rarity_source"] = _RARITY_CONSENSUS_METHOD
            matched["rarity_evidence_count"] = len(consensus["print_ids"])

    return items
