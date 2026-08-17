from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass

from sqlalchemy import select

from app.jobs.cardmarket_catalog_audit import (
    ProductListRow,
    normalize_collector,
    normalize_name,
    split_product_name_hints,
)
from app.models import Card, Game, Print, Set


@dataclass(frozen=True)
class SetScore:
    set_id: int
    set_code: str
    name_matches: int
    pair_matches: int
    cm_name_coverage: float
    internal_name_coverage: float
    pair_coverage: float
    score: float

    def as_dict(self) -> dict:
        return {
            "set_id": self.set_id,
            "set_code": self.set_code,
            "name_matches": self.name_matches,
            "pair_matches": self.pair_matches,
            "cm_name_coverage": round(self.cm_name_coverage, 6),
            "internal_name_coverage": round(self.internal_name_coverage, 6),
            "pair_coverage": round(self.pair_coverage, 6),
            "score": round(self.score, 6),
        }


@dataclass(frozen=True)
class BootstrapDecision:
    expansion_id: str
    game: str
    category: str
    products: int
    unique_names: int
    collector_pairs: int
    status: str
    set_codes: tuple[str, ...]
    evidence: dict

    def as_dict(self) -> dict:
        return {
            "expansion_id": self.expansion_id,
            "game": self.game,
            "category": self.category,
            "products": self.products,
            "unique_names": self.unique_names,
            "collector_pairs": self.collector_pairs,
            "status": self.status,
            "set_codes": list(self.set_codes),
            "evidence": self.evidence,
        }


def market_identity_hints(game_slug: str, raw_name: str) -> tuple[str, str | None]:
    """Return conservative name + optional collector hints from Cardmarket labels.

    Cardmarket is a commercial catalog, so names contain presentation metadata
    that canonical game sources do not always preserve. We only remove syntax we
    can identify safely per game; this helper does not infer a physical variant.
    """
    game_slug = str(game_slug or "").strip().lower()
    raw = str(raw_name or "").strip()

    base, collector = split_product_name_hints(raw)
    if game_slug == "onepiece":
        return base, collector

    if game_slug == "pokemon":
        # Current Cardmarket Pokémon labels commonly add attack/disambiguation
        # text in square brackets. The canonical card name is the prefix.
        stripped = re.sub(r"\s*\[[^\[\]]+\]\s*$", "", raw).strip()
        return stripped or raw, None

    return raw, None


def _safe_ratio(numerator: int, denominator: int) -> float:
    return float(numerator) / float(denominator) if denominator else 0.0


def _internal_signatures(session, game_slug: str):
    rows = session.execute(
        select(Set.id, Set.code, Card.name, Print.collector_number)
        .join(Print, Print.set_id == Set.id)
        .join(Card, Card.id == Print.card_id)
        .join(Game, Game.id == Set.game_id)
        .where(Game.slug == game_slug)
    ).all()

    names_by_set: dict[int, set[str]] = defaultdict(set)
    pairs_by_set: dict[int, set[tuple[str, str]]] = defaultdict(set)
    code_by_set: dict[int, str] = {}
    name_to_sets: dict[str, set[int]] = defaultdict(set)
    pair_to_sets: dict[tuple[str, str], set[int]] = defaultdict(set)

    for set_id, set_code, card_name, collector_number in rows:
        sid = int(set_id)
        code_by_set[sid] = str(set_code)
        name_key = normalize_name(card_name or "")
        if not name_key:
            continue
        names_by_set[sid].add(name_key)
        name_to_sets[name_key].add(sid)
        collector_key = normalize_collector(collector_number)
        if collector_key:
            pair = (name_key, collector_key)
            pairs_by_set[sid].add(pair)
            pair_to_sets[pair].add(sid)

    return names_by_set, pairs_by_set, code_by_set, name_to_sets, pair_to_sets


def bootstrap_expansion_set_families(
    session,
    products: list[ProductListRow],
    *,
    game_slug: str,
    min_name_matches: int = 3,
    min_pair_matches: int = 2,
) -> tuple[dict, list[BootstrapDecision], dict[str, dict]]:
    """Infer reviewable Cardmarket expansion -> internal set/family candidates.

    Unlike the existing crosswalk derivation, this requires no pre-existing
    Cardmarket identifiers. It compares Cardmarket product-list content with the
    canonical Card/Print catalog and is deliberately read-only.

    The output is proposal evidence, never an automatic mapping instruction.
    """
    game_slug = str(game_slug or "").strip().lower()
    min_name_matches = max(1, int(min_name_matches or 1))
    min_pair_matches = max(1, int(min_pair_matches or 1))

    (
        names_by_set,
        pairs_by_set,
        code_by_set,
        name_to_sets,
        pair_to_sets,
    ) = _internal_signatures(session, game_slug)

    by_expansion: dict[str, list[ProductListRow]] = defaultdict(list)
    missing_expansion_rows = 0
    for product in products:
        expansion_id = str(product.expansion_id or "").strip()
        if not expansion_id:
            missing_expansion_rows += 1
            continue
        by_expansion[expansion_id].append(product)

    decisions: list[BootstrapDecision] = []
    proposals: dict[str, dict] = {}

    for expansion_id, rows in sorted(by_expansion.items(), key=lambda item: item[0]):
        categories = Counter(row.category for row in rows if row.category)
        category = categories.most_common(1)[0][0] if categories else ""
        cm_names: set[str] = set()
        cm_pairs: set[tuple[str, str]] = set()

        for row in rows:
            base_name, collector_hint = market_identity_hints(game_slug, row.name)
            name_key = normalize_name(base_name)
            if not name_key:
                continue
            cm_names.add(name_key)
            collector_key = normalize_collector(collector_hint)
            if collector_key:
                cm_pairs.add((name_key, collector_key))

        candidate_ids: set[int] = set()
        for name_key in cm_names:
            candidate_ids.update(name_to_sets.get(name_key, ()))
        for pair in cm_pairs:
            candidate_ids.update(pair_to_sets.get(pair, ()))

        scores: list[SetScore] = []
        for set_id in candidate_ids:
            internal_names = names_by_set.get(set_id, set())
            internal_pairs = pairs_by_set.get(set_id, set())
            name_matches = len(cm_names & internal_names)
            pair_matches = len(cm_pairs & internal_pairs)
            cm_name_coverage = _safe_ratio(name_matches, len(cm_names))
            internal_name_coverage = _safe_ratio(name_matches, len(internal_names))
            pair_coverage = _safe_ratio(pair_matches, len(cm_pairs))

            # Pair evidence is strongest (notably One Piece). Name coverage in
            # both directions prevents tiny generic sets from winning merely by
            # sharing common names such as basic energies.
            score = (
                (4.0 * pair_coverage if cm_pairs else 0.0)
                + (2.0 * cm_name_coverage)
                + internal_name_coverage
                + min(name_matches, 50) / 100.0
            )
            scores.append(SetScore(
                set_id=set_id,
                set_code=code_by_set[set_id],
                name_matches=name_matches,
                pair_matches=pair_matches,
                cm_name_coverage=cm_name_coverage,
                internal_name_coverage=internal_name_coverage,
                pair_coverage=pair_coverage,
                score=score,
            ))

        scores.sort(key=lambda item: (item.score, item.pair_matches, item.name_matches), reverse=True)
        plausible = [
            item for item in scores
            if item.name_matches >= min_name_matches
            or item.pair_matches >= min_pair_matches
        ]
        top = plausible[0] if plausible else None

        status = "catalog_source_gap"
        selected: list[SetScore] = []
        evidence: dict = {
            "top_candidates": [item.as_dict() for item in scores[:10]],
        }

        if top is not None:
            # Collector-pair evidence can prove a set even when product-name
            # duplication from parallels makes raw name coverage look modest.
            pair_strong = bool(
                cm_pairs
                and top.pair_matches >= min_pair_matches
                and top.pair_coverage >= 0.65
            )
            name_strong = bool(
                top.name_matches >= min_name_matches
                and top.cm_name_coverage >= 0.60
                and top.internal_name_coverage >= 0.35
            )
            second = plausible[1] if len(plausible) > 1 else None
            dominant_margin = second is None or top.score >= max(second.score * 1.20, second.score + 0.20)

            if (pair_strong or name_strong) and dominant_margin:
                status = "reviewable_unique_set"
                selected = [top]
            else:
                # Build a small family only from candidates that materially add
                # coverage. This captures legitimate split families such as a
                # main Pokémon set + trainer/gallery subset without pretending
                # that every weak overlap belongs to the expansion.
                family: list[SetScore] = []
                covered_names: set[str] = set()
                covered_pairs: set[tuple[str, str]] = set()
                for candidate in plausible[:8]:
                    candidate_names = cm_names & names_by_set.get(candidate.set_id, set())
                    candidate_pairs = cm_pairs & pairs_by_set.get(candidate.set_id, set())
                    added_names = candidate_names - covered_names
                    added_pairs = candidate_pairs - covered_pairs
                    if not family:
                        if candidate.name_matches < min_name_matches and candidate.pair_matches < min_pair_matches:
                            continue
                    elif len(added_names) < min_name_matches and len(added_pairs) < min_pair_matches:
                        continue
                    family.append(candidate)
                    covered_names.update(candidate_names)
                    covered_pairs.update(candidate_pairs)
                    if len(family) >= 5:
                        break

                family_name_coverage = _safe_ratio(len(covered_names), len(cm_names))
                family_pair_coverage = _safe_ratio(len(covered_pairs), len(cm_pairs))
                evidence["family_name_coverage"] = round(family_name_coverage, 6)
                evidence["family_pair_coverage"] = round(family_pair_coverage, 6)
                evidence["family_size"] = len(family)

                family_strong = bool(
                    len(family) >= 2
                    and (
                        (cm_pairs and family_pair_coverage >= 0.75)
                        or family_name_coverage >= 0.72
                    )
                    and (
                        family_name_coverage >= top.cm_name_coverage + 0.08
                        or (cm_pairs and family_pair_coverage >= top.pair_coverage + 0.08)
                    )
                )
                if family_strong:
                    status = "reviewable_set_family"
                    selected = family
                elif pair_strong or name_strong:
                    status = "ambiguous_overlap"
                    selected = [top]
                else:
                    status = "weak_overlap"
                    selected = [top]

        set_codes = tuple(item.set_code for item in selected)
        if status in {"reviewable_unique_set", "reviewable_set_family"}:
            proposals[expansion_id] = {
                "game": game_slug,
                "set_codes": list(set_codes),
                "kind": "set_family" if len(set_codes) > 1 else "unique_set",
                "evidence": {
                    "source": "cardmarket_product_list_vs_canonical_catalog",
                    "products": len(rows),
                    "unique_names": len(cm_names),
                    "collector_pairs": len(cm_pairs),
                    "selected": [item.as_dict() for item in selected],
                    "family_name_coverage": evidence.get("family_name_coverage"),
                    "family_pair_coverage": evidence.get("family_pair_coverage"),
                },
            }

        decisions.append(BootstrapDecision(
            expansion_id=expansion_id,
            game=game_slug,
            category=category,
            products=len(rows),
            unique_names=len(cm_names),
            collector_pairs=len(cm_pairs),
            status=status,
            set_codes=set_codes,
            evidence=evidence,
        ))

    status_counts = Counter(item.status for item in decisions)
    summary = {
        "game": game_slug,
        "product_rows": len(products),
        "rows_missing_expansion_id": missing_expansion_rows,
        "expansions": len(decisions),
        "internal_sets": len(code_by_set),
        "reviewable_unique_set": status_counts.get("reviewable_unique_set", 0),
        "reviewable_set_family": status_counts.get("reviewable_set_family", 0),
        "ambiguous_overlap": status_counts.get("ambiguous_overlap", 0),
        "weak_overlap": status_counts.get("weak_overlap", 0),
        "catalog_source_gap": status_counts.get("catalog_source_gap", 0),
        "status_counts": dict(sorted(status_counts.items())),
        "write_mode": "disabled",
    }
    return summary, decisions, proposals
