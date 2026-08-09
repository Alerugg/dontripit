from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
import re

from sqlalchemy import select

from app.jobs.cardmarket_catalog_audit import ProductListRow, split_product_name_hints, normalize_collector
from app.models import Game, Set


@dataclass(frozen=True)
class SeedCrosswalkDecision:
    expansion_id: str
    status: str
    proposed_set_code: str | None
    product_rows: int
    hinted_rows: int
    matched_hint_rows: int
    dominant_rows: int
    consensus: float
    set_counts: dict[str, int]
    unmatched_hints: int
    evidence: dict

    def as_dict(self) -> dict:
        return {
            "expansion_id": self.expansion_id,
            "status": self.status,
            "proposed_set_code": self.proposed_set_code,
            "product_rows": self.product_rows,
            "hinted_rows": self.hinted_rows,
            "matched_hint_rows": self.matched_hint_rows,
            "dominant_rows": self.dominant_rows,
            "consensus": self.consensus,
            "set_counts": self.set_counts,
            "unmatched_hints": self.unmatched_hints,
            "evidence": self.evidence,
        }


def _hint_set_candidates(hint: str | None, set_codes: list[str]) -> list[str]:
    if not hint:
        return []
    hint_key = normalize_collector(hint)
    if not hint_key:
        return []
    matches = []
    for set_code in set_codes:
        code_key = normalize_collector(set_code)
        if code_key and hint_key.startswith(code_key):
            matches.append(set_code)
    # Longest code wins when one set code is a prefix of another (e.g. PRB vs PRB01).
    if not matches:
        return []
    longest = max(len(normalize_collector(code)) for code in matches)
    return sorted({code for code in matches if len(normalize_collector(code)) == longest})


def derive_onepiece_seed_crosswalk(
    session,
    products: list[ProductListRow],
    *,
    min_hinted_rows: int = 5,
    min_consensus: float = 0.9,
) -> tuple[dict, list[SeedCrosswalkDecision], dict[str, dict]]:
    """Propose Expansion ID -> One Piece set mappings from collector-code evidence only.

    This is intentionally read-only. Collector codes are useful evidence but are not
    sufficient identity proof for reprint products, so even strong consensus produces
    a reviewable proposal, never a database write.
    """
    min_hinted_rows = max(1, int(min_hinted_rows))
    min_consensus = max(0.0, min(1.0, float(min_consensus)))

    game = session.execute(select(Game).where(Game.slug == "onepiece")).scalar_one_or_none()
    if game is None:
        return {
            "expansions_seen": 0,
            "reviewable_proposals": 0,
            "status_counts": {"game_missing": 1},
            "write_mode": "disabled",
        }, [], {}

    set_codes = [
        str(code)
        for code in session.execute(
            select(Set.code).where(Set.game_id == game.id, Set.code.is_not(None))
        ).scalars().all()
        if code
    ]

    grouped: dict[str, list[ProductListRow]] = defaultdict(list)
    for product in products:
        if str(product.category or "").strip().casefold() not in {
            "one piece card game single",
            "one piece single",
        }:
            continue
        grouped[str(product.expansion_id)].append(product)

    decisions: list[SeedCrosswalkDecision] = []
    proposals: dict[str, dict] = {}

    for expansion_id, rows in sorted(grouped.items(), key=lambda item: item[0]):
        counts: Counter[str] = Counter()
        hinted_rows = 0
        matched_rows = 0
        unmatched_hints = 0
        samples: dict[str, list[dict]] = defaultdict(list)

        for product in rows:
            base_name, hint = split_product_name_hints(product.name)
            if not hint:
                continue
            hinted_rows += 1
            candidates = _hint_set_candidates(hint, set_codes)
            if len(candidates) != 1:
                unmatched_hints += 1
                continue
            set_code = candidates[0]
            matched_rows += 1
            counts[set_code] += 1
            if len(samples[set_code]) < 8:
                samples[set_code].append({
                    "product_id": product.product_id,
                    "name": product.name,
                    "base_name": base_name,
                    "collector_hint": hint,
                    "metacard_id": product.metacard_id,
                })

        proposed = None
        dominant_rows = 0
        consensus = 0.0
        if counts:
            proposed, dominant_rows = counts.most_common(1)[0]
            consensus = dominant_rows / max(1, matched_rows)

        if hinted_rows < min_hinted_rows:
            status = "insufficient_collector_hints"
        elif matched_rows < min_hinted_rows:
            status = "insufficient_internal_set_matches"
        elif len(counts) == 1 and consensus >= min_consensus:
            status = "reviewable_collector_consensus"
        elif consensus >= min_consensus:
            # Strong dominant prefix but minority products disagree: likely reprint/mixed product.
            status = "mixed_prefixes_dominant"
        else:
            status = "mixed_prefixes"

        evidence = {
            "sample_products_by_set": dict(samples),
            "known_onepiece_set_codes": len(set_codes),
            "method": "terminal_collector_hint_prefix_against_internal_set_codes",
            "warning": "collector number may identify source card rather than physical reprint set",
        }

        decision = SeedCrosswalkDecision(
            expansion_id=expansion_id,
            status=status,
            proposed_set_code=proposed,
            product_rows=len(rows),
            hinted_rows=hinted_rows,
            matched_hint_rows=matched_rows,
            dominant_rows=dominant_rows,
            consensus=round(consensus, 6),
            set_counts=dict(sorted(counts.items())),
            unmatched_hints=unmatched_hints,
            evidence=evidence,
        )
        decisions.append(decision)

        if status == "reviewable_collector_consensus" and proposed:
            proposals[expansion_id] = {
                "game": "onepiece",
                "set_code": proposed,
                "evidence": {
                    "method": "collector_hint_consensus",
                    "hinted_rows": hinted_rows,
                    "matched_rows": matched_rows,
                    "dominant_rows": dominant_rows,
                    "consensus": round(consensus, 6),
                    "review_required": True,
                },
            }

    status_counts = Counter(item.status for item in decisions)
    summary = {
        "expansions_seen": len(decisions),
        "reviewable_proposals": len(proposals),
        "status_counts": dict(sorted(status_counts.items())),
        "minimum_hinted_rows": min_hinted_rows,
        "minimum_consensus": min_consensus,
        "write_mode": "disabled",
    }
    return summary, decisions, proposals
