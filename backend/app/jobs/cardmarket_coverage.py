from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Iterable

from sqlalchemy import select

from app.jobs.cardmarket_prices import CardmarketPriceRow
from app.models import Game, Print, PrintIdentifier, Set


CARDMARKET_SOURCE = "cardmarket"


@dataclass(frozen=True)
class CoverageBucket:
    game: str
    set_code: str
    set_name: str
    total_prints: int
    mapped_prints: int
    priced_candidates: int

    @property
    def mapping_coverage(self) -> float:
        return 0.0 if self.total_prints == 0 else self.mapped_prints / self.total_prints

    @property
    def price_candidate_coverage(self) -> float:
        return 0.0 if self.total_prints == 0 else self.priced_candidates / self.total_prints

    def as_dict(self) -> dict:
        return {
            "game": self.game,
            "set_code": self.set_code,
            "set_name": self.set_name,
            "total_prints": self.total_prints,
            "mapped_prints": self.mapped_prints,
            "priced_candidates": self.priced_candidates,
            "mapping_coverage": round(self.mapping_coverage, 4),
            "price_candidate_coverage": round(self.price_candidate_coverage, 4),
        }


def _finish_has_price(row: CardmarketPriceRow, *, is_foil: bool) -> bool:
    if is_foil:
        return any(value is not None for value in (
            row.foil_low,
            row.foil_trend,
            row.foil_avg,
            row.foil_avg1,
            row.foil_avg7,
            row.foil_avg30,
        ))
    return any(value is not None for value in (
        row.low,
        row.low_ex,
        row.trend,
        row.avg,
        row.avg1,
        row.avg7,
        row.avg30,
    ))


def build_cardmarket_coverage(
    session,
    price_rows_by_game: dict[str, Iterable[CardmarketPriceRow]] | None = None,
) -> dict:
    """Return read-only mapping and game-scoped price-candidate coverage.

    Price Guides are keyed by the TCG they belong to. An idProduct found in the
    Magic feed can therefore never make a Pokémon Print look price-ready, even if
    a bad historical mapping reuses that numeric id. Mapping coverage itself is
    diagnostic and does not claim every internal Print must exist on Cardmarket.
    """
    normalized_feeds: dict[str, dict[str, CardmarketPriceRow]] = {}
    duplicate_price_rows = 0
    unique_price_products = 0
    if price_rows_by_game is not None:
        for raw_game, rows in price_rows_by_game.items():
            game = str(raw_game or "").strip().lower()
            by_product: dict[str, CardmarketPriceRow] = {}
            for row in rows:
                if row.product_id in by_product:
                    duplicate_price_rows += 1
                    continue
                by_product[row.product_id] = row
            normalized_feeds[game] = by_product
            unique_price_products += len(by_product)

    print_rows = session.execute(
        select(
            Print.id,
            Print.is_foil,
            Set.id,
            Set.code,
            Set.name,
            Game.slug,
        )
        .join(Set, Set.id == Print.set_id)
        .join(Game, Game.id == Set.game_id)
    ).all()

    identifiers = session.execute(
        select(PrintIdentifier.print_id, PrintIdentifier.external_id)
        .where(PrintIdentifier.source == CARDMARKET_SOURCE)
    ).all()

    external_by_print: dict[int, list[str]] = defaultdict(list)
    for print_id, external_id in identifiers:
        external_by_print[int(print_id)].append(str(external_id))

    bucket_counts: dict[tuple[str, str, str], Counter] = defaultdict(Counter)
    per_game: dict[str, Counter] = defaultdict(Counter)
    ambiguous_print_identifiers = 0
    mapped_products_missing_from_price_guide = 0
    mapped_products_wrong_finish = 0
    mapped_products_cross_game_only = 0

    for print_id, is_foil, _set_id, set_code, set_name, game_slug in print_rows:
        game = str(game_slug)
        key = (game, str(set_code), str(set_name))
        bucket_counts[key]["total"] += 1
        per_game[game]["total"] += 1

        external_ids = external_by_print.get(int(print_id), [])
        if len(external_ids) > 1:
            ambiguous_print_identifiers += 1
            continue
        if len(external_ids) != 1:
            continue

        bucket_counts[key]["mapped"] += 1
        per_game[game]["mapped"] += 1

        if price_rows_by_game is None:
            continue
        product_id = external_ids[0]
        game_feed = normalized_feeds.get(game, {})
        price_row = game_feed.get(product_id)
        if price_row is None:
            appears_elsewhere = any(
                product_id in feed
                for feed_game, feed in normalized_feeds.items()
                if feed_game != game
            )
            if appears_elsewhere:
                mapped_products_cross_game_only += 1
            else:
                mapped_products_missing_from_price_guide += 1
            continue
        if not _finish_has_price(price_row, is_foil=bool(is_foil)):
            mapped_products_wrong_finish += 1
            continue

        bucket_counts[key]["priced"] += 1
        per_game[game]["priced"] += 1

    buckets = [
        CoverageBucket(
            game=game,
            set_code=set_code,
            set_name=set_name,
            total_prints=counts["total"],
            mapped_prints=counts["mapped"],
            priced_candidates=counts["priced"],
        )
        for (game, set_code, set_name), counts in bucket_counts.items()
    ]
    buckets.sort(key=lambda item: (item.game, item.mapping_coverage, -item.total_prints, item.set_code))

    games = []
    for game in sorted(per_game):
        counts = per_game[game]
        total = counts["total"]
        mapped = counts["mapped"]
        priced = counts["priced"]
        games.append({
            "game": game,
            "total_prints": total,
            "mapped_prints": mapped,
            "priced_candidates": priced,
            "mapping_coverage": round(mapped / total, 4) if total else 0.0,
            "price_candidate_coverage": round(priced / total, 4) if total else 0.0,
        })

    total_prints = len(print_rows)
    mapped_prints = sum(item["mapped_prints"] for item in games)
    priced_candidates = sum(item["priced_candidates"] for item in games)

    priority_sets = [
        item.as_dict()
        for item in sorted(
            buckets,
            key=lambda item: (
                item.mapping_coverage,
                -item.total_prints,
                item.game,
                item.set_code,
            ),
        )
        if item.total_prints >= 5 and item.mapped_prints < item.total_prints
    ][:50]

    return {
        "summary": {
            "total_prints": total_prints,
            "mapped_prints": mapped_prints,
            "priced_candidates": priced_candidates,
            "mapping_coverage": round(mapped_prints / total_prints, 4) if total_prints else 0.0,
            "price_candidate_coverage": round(priced_candidates / total_prints, 4) if total_prints else 0.0,
            "price_guides_supplied": price_rows_by_game is not None,
            "price_feed_games": sorted(normalized_feeds),
            "unique_price_products": unique_price_products,
            "duplicate_price_rows": duplicate_price_rows,
            "ambiguous_print_identifiers": ambiguous_print_identifiers,
            "mapped_products_missing_from_price_guide": mapped_products_missing_from_price_guide,
            "mapped_products_cross_game_only": mapped_products_cross_game_only,
            "mapped_products_wrong_finish": mapped_products_wrong_finish,
            "write_mode": "disabled",
        },
        "games": games,
        "sets": [item.as_dict() for item in buckets],
        "priority_sets": priority_sets,
    }
