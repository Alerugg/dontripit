from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass

from sqlalchemy import select

from app.jobs.cardmarket_catalog_audit import ProductListRow, infer_game_from_category
from app.models import Game, Print, PrintIdentifier, Set


CARDMARKET_SOURCE = "cardmarket"


@dataclass(frozen=True)
class ExpansionDecision:
    expansion_id: str
    category: str
    category_game: str | None
    status: str
    game: str | None
    set_code: str | None
    mapped_products: int
    unique_internal_sets: int
    evidence: dict

    def as_dict(self) -> dict:
        return {
            "expansion_id": self.expansion_id,
            "category": self.category,
            "category_game": self.category_game,
            "status": self.status,
            "game": self.game,
            "set_code": self.set_code,
            "mapped_products": self.mapped_products,
            "unique_internal_sets": self.unique_internal_sets,
            "evidence": self.evidence,
        }


def derive_expansion_crosswalk(
    session,
    products: list[ProductListRow],
    *,
    min_samples: int = 3,
    game_filter: str = "",
) -> tuple[dict, list[ExpansionDecision], dict[str, dict]]:
    """Infer reviewable Expansion ID -> Don’tRipIt set proposals from existing exact mappings.

    This function is deliberately read-only. It uses already-existing Cardmarket
    PrintIdentifier rows as evidence and never inserts or updates identifiers or sets.
    A proposal is emitted only when all mapped products for an expansion agree on one
    internal game+set and enough mapped products support that consensus.
    """
    min_samples = max(1, int(min_samples or 1))
    game_filter = str(game_filter or "").strip().lower()

    by_product: dict[str, ProductListRow] = {}
    duplicate_product_ids: set[str] = set()
    for product in products:
        if product.product_id in by_product:
            duplicate_product_ids.add(product.product_id)
            continue
        by_product[product.product_id] = product

    mapped_query = (
        select(
            PrintIdentifier.external_id,
            Print.id,
            Set.code,
            Game.slug,
        )
        .join(Print, Print.id == PrintIdentifier.print_id)
        .join(Set, Set.id == Print.set_id)
        .join(Game, Game.id == Set.game_id)
        .where(PrintIdentifier.source == CARDMARKET_SOURCE)
    )
    if game_filter:
        mapped_query = mapped_query.where(Game.slug == game_filter)
    mapped_rows = session.execute(mapped_query).all()

    evidence_by_expansion: dict[str, list[dict]] = defaultdict(list)
    missing_from_product_list = 0
    category_conflicts = 0

    for external_id, print_id, set_code, game_slug in mapped_rows:
        product = by_product.get(str(external_id))
        if product is None:
            missing_from_product_list += 1
            continue
        category_game = infer_game_from_category(product.category)
        if category_game and category_game != str(game_slug):
            category_conflicts += 1

        evidence_by_expansion[product.expansion_id].append({
            "product_id": str(external_id),
            "print_id": int(print_id),
            "set_code": str(set_code),
            "game": str(game_slug),
            "category": product.category,
            "category_game": category_game,
            "name": product.name,
        })

    decisions: list[ExpansionDecision] = []
    proposals: dict[str, dict] = {}

    for expansion_id, rows in sorted(evidence_by_expansion.items(), key=lambda item: item[0]):
        categories = Counter(row["category"] for row in rows)
        category = categories.most_common(1)[0][0] if categories else ""
        category_games = {row["category_game"] for row in rows if row["category_game"]}
        category_game = next(iter(category_games)) if len(category_games) == 1 else None

        internal_targets = Counter((row["game"], row["set_code"]) for row in rows)
        unique_targets = len(internal_targets)
        top_target, top_count = internal_targets.most_common(1)[0]
        top_game, top_set = top_target
        mapped_products = len({row["product_id"] for row in rows})
        all_names = sorted({row["name"] for row in rows})
        sample_rows = rows[:20]

        if len(category_games) > 1:
            status = "category_game_conflict"
            game = None
            set_code = None
        elif category_game and category_game != top_game:
            status = "category_game_conflict"
            game = None
            set_code = None
        elif unique_targets > 1:
            status = "conflicting_internal_sets"
            game = None
            set_code = None
        elif mapped_products < min_samples:
            status = "insufficient_evidence"
            game = top_game
            set_code = top_set
        else:
            status = "reviewable_unique_consensus"
            game = top_game
            set_code = top_set
            proposals[expansion_id] = {
                "game": top_game,
                "set_code": top_set,
                "evidence": {
                    "mapped_products": mapped_products,
                    "consensus": 1.0,
                    "source": "existing_exact_cardmarket_print_identifiers",
                },
            }

        decisions.append(ExpansionDecision(
            expansion_id=expansion_id,
            category=category,
            category_game=category_game,
            status=status,
            game=game,
            set_code=set_code,
            mapped_products=mapped_products,
            unique_internal_sets=unique_targets,
            evidence={
                "target_counts": [
                    {"game": target_game, "set_code": target_set, "count": count}
                    for (target_game, target_set), count in internal_targets.most_common()
                ],
                "sample_products": sample_rows,
                "unique_names": len(all_names),
                "top_target_count": top_count,
            },
        ))

    status_counts = Counter(item.status for item in decisions)
    summary = {
        "product_list_rows": len(products),
        "unique_product_ids": len(by_product),
        "duplicate_product_ids": len(duplicate_product_ids),
        "mapped_identifiers_examined": len(mapped_rows),
        "mapped_identifiers_missing_from_product_list": missing_from_product_list,
        "category_conflict_rows": category_conflicts,
        "expansions_with_evidence": len(decisions),
        "reviewable_unique_consensus": status_counts.get("reviewable_unique_consensus", 0),
        "insufficient_evidence": status_counts.get("insufficient_evidence", 0),
        "conflicting_internal_sets": status_counts.get("conflicting_internal_sets", 0),
        "category_game_conflict": status_counts.get("category_game_conflict", 0),
        "minimum_samples": min_samples,
        "game_filter": game_filter or None,
        "write_mode": "disabled",
    }
    return summary, decisions, proposals
