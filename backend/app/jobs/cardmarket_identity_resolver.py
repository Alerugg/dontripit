from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Iterable

from app.jobs.cardmarket_catalog_audit import ProductListRow


SUPPORTED_GAMES = ("mtg", "pokemon", "yugioh", "onepiece")
CARDMARKET_GAME_PATH = {
    "mtg": "Magic",
    "pokemon": "Pokemon",
    "yugioh": "YuGiOh",
    "onepiece": "OnePiece",
}


@dataclass(frozen=True)
class ResolverIdentity:
    identity_key: str
    game: str
    card_id: int
    set_id: int
    set_code: str
    card_name: str
    collector_number: str
    variant: str
    print_ids: tuple[int, ...]
    pending_print_ids: tuple[int, ...]
    languages: tuple[str, ...]
    finishes: tuple[str, ...]
    rarities: tuple[str, ...]
    known_product_ids: tuple[str, ...] = ()
    mtg_source_product_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class ResolverDecision:
    category: str
    method: str
    product_id: str | None
    candidate_ids: tuple[str, ...]
    score: float | None
    second_score: float | None
    evidence: dict

    @property
    def resolved(self) -> bool:
        return self.category in {"EXACT", "UNIQUE_HIGH_CONFIDENCE"} and bool(self.product_id)


@dataclass(frozen=True)
class CatalogProduct:
    game: str
    product_id: str
    name: str
    expansion_id: str
    metacard_id: str | None
    strict_name: str
    loose_name: str
    base_name: str
    collector_hint: str | None
    descriptor: str | None


def normalize_text(value: str | None) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold().strip()
    return re.sub(r"\s+", " ", text.replace("’", "'").replace("‘", "'"))


def normalize_loose(value: str | None) -> str:
    text = unicodedata.normalize("NFKD", normalize_text(value))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", "", text)


def normalize_collector(value: str | None) -> str:
    return re.sub(r"[^a-z0-9]", "", normalize_text(value))


def split_terminal_collector(name: str) -> tuple[str, str | None]:
    raw = str(name or "").strip()
    match = re.match(r"^(.*?)\s*\(([^()]*)\)\s*$", raw)
    if not match:
        return raw, None
    base, hint = match.group(1).strip(), match.group(2).strip()
    if not base or not hint or not re.search(r"\d", hint) or len(hint) > 64:
        return raw, None
    return base, hint


def split_pokemon_descriptor(name: str) -> tuple[str, str | None]:
    raw = str(name or "").strip()
    match = re.match(r"^(.*?)\s*\[([^\[\]]+)\]\s*$", raw)
    if not match:
        return raw, None
    base, descriptor = match.group(1).strip(), match.group(2).strip()
    if not base or not descriptor:
        return raw, None
    return base, descriptor


def cardmarket_redirect_url(game: str, product_id: str | int) -> str:
    game = str(game or "").strip().lower()
    path = CARDMARKET_GAME_PATH.get(game)
    if not path:
        raise ValueError(f"Unsupported Cardmarket game {game!r}")
    return f"https://www.cardmarket.com/en/{path}/Products?idProduct={str(product_id).strip()}"


def build_catalog_products(game: str, rows: Iterable[ProductListRow]) -> list[CatalogProduct]:
    game = str(game or "").strip().lower()
    if game not in SUPPORTED_GAMES:
        raise ValueError(f"Unsupported game {game!r}")
    products: list[CatalogProduct] = []
    for row in rows:
        raw_name = str(row.name or "").strip()
        if game == "onepiece":
            base, collector_hint = split_terminal_collector(raw_name)
            descriptor = None
        elif game == "pokemon":
            base, descriptor = split_pokemon_descriptor(raw_name)
            collector_hint = None
        else:
            base, collector_hint, descriptor = raw_name, None, None
        products.append(
            CatalogProduct(
                game=game,
                product_id=str(row.product_id),
                name=raw_name,
                expansion_id=str(row.expansion_id or ""),
                metacard_id=str(row.metacard_id) if row.metacard_id else None,
                strict_name=normalize_text(raw_name),
                loose_name=normalize_loose(raw_name),
                base_name=normalize_text(base),
                collector_hint=collector_hint,
                descriptor=descriptor,
            )
        )
    return products


def _decision(
    category: str,
    method: str,
    product_id: str | None = None,
    candidates: Iterable[CatalogProduct] = (),
    *,
    score: float | None = None,
    second_score: float | None = None,
    evidence: dict | None = None,
) -> ResolverDecision:
    candidate_ids = tuple(dict.fromkeys(item.product_id for item in candidates))
    return ResolverDecision(
        category=category,
        method=method,
        product_id=product_id,
        candidate_ids=candidate_ids,
        score=score,
        second_score=second_score,
        evidence=evidence or {},
    )


def resolve_identity(
    identity: ResolverIdentity,
    *,
    products_by_id: dict[str, CatalogProduct],
    products_by_expansion: dict[str, list[CatalogProduct]],
    trusted_expansion_ids: Iterable[str],
    high_confidence_threshold: float = 0.985,
    min_margin: float = 0.05,
) -> ResolverDecision:
    """Resolve one grouped canonical identity without writes.

    Exact source IDs and deterministic unique matches are allowed to return EXACT.
    Fuzzy similarity can only return UNIQUE_HIGH_CONFIDENCE and is never treated as
    an auto-write decision by this module.

    Pokemon and One Piece have an additional commercial-variant hazard: a canonical
    Don’tRipIt Print can retain the original set/card code while ``variant`` represents
    a later market reprint/parallel. The V1 gold set proved that expansion+name (and OP
    collector code) alone can therefore select a different Cardmarket idProduct even
    when only one product remains in the set-level expansion bucket. Until a
    variant-aware expansion/reprint bridge is certified, those C-level matches are
    deliberately review-only rather than EXACT.
    """
    valid_known = tuple(dict.fromkeys(pid for pid in identity.known_product_ids if pid in products_by_id))
    missing_known = tuple(pid for pid in identity.known_product_ids if pid not in products_by_id)
    if len(valid_known) == 1:
        product = products_by_id[valid_known[0]]
        if product.game != identity.game:
            return _decision(
                "AMBIGUOUS",
                "A_known_product_game_conflict",
                candidates=(product,),
                evidence={"known_ids_absent_current_catalog": missing_known},
            )
        return _decision(
            "EXACT",
            "A_existing_exact_or_sibling",
            product.product_id,
            (product,),
            evidence={"known_ids_absent_current_catalog": missing_known},
        )
    if len(valid_known) > 1:
        return _decision(
            "AMBIGUOUS",
            "A_multiple_existing_exact_products",
            candidates=(products_by_id[pid] for pid in valid_known),
            evidence={"known_ids_absent_current_catalog": missing_known},
        )

    if identity.game == "mtg":
        valid_source = tuple(dict.fromkeys(pid for pid in identity.mtg_source_product_ids if pid in products_by_id))
        if len(valid_source) == 1:
            product = products_by_id[valid_source[0]]
            return _decision(
                "EXACT",
                "B_scryfall_cardmarket_id",
                product.product_id,
                (product,),
                evidence={"source_ids": list(identity.mtg_source_product_ids)},
            )
        if len(valid_source) > 1:
            return _decision(
                "AMBIGUOUS",
                "B_scryfall_cardmarket_id_conflict",
                candidates=(products_by_id[pid] for pid in valid_source),
                evidence={"source_ids": list(identity.mtg_source_product_ids)},
            )

    expansion_ids = tuple(dict.fromkeys(str(value) for value in trusted_expansion_ids if str(value)))
    if not expansion_ids:
        return _decision(
            "UNMATCHED",
            "C_no_trusted_expansion_crosswalk",
            evidence={"known_ids_absent_current_catalog": missing_known},
        )

    pool: list[CatalogProduct] = []
    seen_ids: set[str] = set()
    for expansion_id in expansion_ids:
        for product in products_by_expansion.get(expansion_id, []):
            if product.product_id not in seen_ids:
                seen_ids.add(product.product_id)
                pool.append(product)
    if not pool:
        return _decision(
            "UNMATCHED",
            "C_trusted_expansion_has_no_current_products",
            evidence={"trusted_expansion_ids": list(expansion_ids)},
        )

    name_key = normalize_text(identity.card_name)
    collector_key = normalize_collector(identity.collector_number)

    if identity.game == "onepiece":
        exact = [
            product
            for product in pool
            if product.base_name == name_key
            and product.collector_hint
            and normalize_collector(product.collector_hint) == collector_key
        ]
    else:
        exact = [product for product in pool if product.strict_name == name_key]

    if len(exact) == 1:
        if identity.game in {"pokemon", "onepiece"}:
            return _decision(
                "UNIQUE_HIGH_CONFIDENCE",
                "C_unique_exact_composite_variant_unproven",
                exact[0].product_id,
                exact,
                evidence={
                    "trusted_expansion_ids": list(expansion_ids),
                    "review_required": True,
                    "reason": "game_requires_variant_aware_reprint_bridge_before_C_can_be_exact",
                },
            )
        return _decision(
            "EXACT",
            "C_expansion_identity_unique_exact",
            exact[0].product_id,
            exact,
            evidence={"trusted_expansion_ids": list(expansion_ids)},
        )
    if len(exact) > 1:
        return _decision(
            "AMBIGUOUS",
            "C_multiple_exact_products",
            candidates=exact,
            evidence={"trusted_expansion_ids": list(expansion_ids)},
        )

    if identity.game == "pokemon":
        base_hits = [product for product in pool if product.base_name == name_key]
        if len(base_hits) == 1:
            return _decision(
                "UNIQUE_HIGH_CONFIDENCE",
                "D_pokemon_unique_base_name_in_expansion",
                base_hits[0].product_id,
                base_hits,
                evidence={
                    "descriptor": base_hits[0].descriptor,
                    "trusted_expansion_ids": list(expansion_ids),
                    "review_required": True,
                },
            )
        if len(base_hits) > 1:
            return _decision(
                "AMBIGUOUS",
                "D_pokemon_multiple_descriptor_products",
                candidates=base_hits,
                evidence={"trusted_expansion_ids": list(expansion_ids)},
            )

    target_loose = normalize_loose(identity.card_name)
    if not target_loose:
        return _decision(
            "UNMATCHED",
            "E_empty_normalized_name",
            evidence={"trusted_expansion_ids": list(expansion_ids)},
        )

    scored: list[tuple[float, CatalogProduct]] = []
    for product in pool:
        candidate_name = product.base_name if identity.game in {"pokemon", "onepiece"} else product.strict_name
        candidate_loose = normalize_loose(candidate_name)
        if not candidate_loose:
            continue
        score = SequenceMatcher(None, target_loose, candidate_loose).ratio()
        scored.append((score, product))
    scored.sort(key=lambda item: (-item[0], item[1].product_id))
    if not scored:
        return _decision(
            "UNMATCHED",
            "E_no_name_candidates",
            evidence={"trusted_expansion_ids": list(expansion_ids)},
        )

    top_score, top_product = scored[0]
    second_score = scored[1][0] if len(scored) > 1 else 0.0
    margin = top_score - second_score
    if top_score >= high_confidence_threshold and margin >= min_margin:
        return _decision(
            "UNIQUE_HIGH_CONFIDENCE",
            "E_fuzzy_unique_inside_trusted_expansion",
            top_product.product_id,
            (top_product,),
            score=round(top_score, 6),
            second_score=round(second_score, 6),
            evidence={
                "margin": round(margin, 6),
                "trusted_expansion_ids": list(expansion_ids),
                "review_required": True,
            },
        )

    top_candidates = [product for _, product in scored[:10]]
    return _decision(
        "AMBIGUOUS",
        "F_ranked_candidates_no_safe_winner",
        candidates=top_candidates,
        score=round(top_score, 6),
        second_score=round(second_score, 6),
        evidence={
            "margin": round(margin, 6),
            "trusted_expansion_ids": list(expansion_ids),
        },
    )
