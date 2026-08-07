from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone

from sqlalchemy import and_, func, or_, select

from app import db
from app.models import Card, Game, Print, PrintIdentifier, PrintImage, Set
from app.scripts.ingest_status import get_ingest_status


def _scalar_count(session, statement) -> int:
    return int(session.execute(statement).scalar_one() or 0)


def _blank(column):
    return or_(column.is_(None), func.trim(column) == "")


def _iso(value):
    return value.isoformat() if value else None


def _distribution(session, column, *, game_id: int, limit: int = 50) -> list[dict]:
    rows = session.execute(
        select(column, func.count(Print.id).label("count"))
        .join(Set, Set.id == Print.set_id)
        .where(Set.game_id == game_id)
        .group_by(column)
        .order_by(func.count(Print.id).desc(), column.asc())
        .limit(limit)
    ).all()
    return [{"value": value, "count": int(count)} for value, count in rows]


def _duplicate_identity_groups(session, game_id: int, sample_limit: int) -> tuple[int, list[dict]]:
    statement = (
        select(
            Set.code,
            Print.collector_number,
            Print.language,
            Print.is_foil,
            Print.variant,
            func.count(Print.id).label("count"),
        )
        .join(Set, Set.id == Print.set_id)
        .where(Set.game_id == game_id)
        .group_by(Set.code, Print.set_id, Print.collector_number, Print.language, Print.is_foil, Print.variant)
        .having(func.count(Print.id) > 1)
        .order_by(func.count(Print.id).desc(), Set.code.asc(), Print.collector_number.asc())
    )
    rows = session.execute(statement).all()
    samples = [
        {
            "set_code": code,
            "collector_number": collector_number,
            "language": language,
            "is_foil": bool(is_foil),
            "variant": variant,
            "count": int(count),
        }
        for code, collector_number, language, is_foil, variant, count in rows[:sample_limit]
    ]
    return len(rows), samples


def _empty_set_samples(session, game_id: int, sample_limit: int) -> list[dict]:
    rows = session.execute(
        select(Set.code, Set.name, Set.release_date)
        .outerjoin(Print, Print.set_id == Set.id)
        .where(Set.game_id == game_id)
        .group_by(Set.id, Set.code, Set.name, Set.release_date)
        .having(func.count(Print.id) == 0)
        .order_by(Set.release_date.asc(), Set.code.asc())
        .limit(sample_limit)
    ).all()
    return [
        {"code": code, "name": name, "release_date": release_date.isoformat() if release_date else None}
        for code, name, release_date in rows
    ]


def _game_health(session, game_id: int, slug: str, name: str, sample_limit: int) -> dict:
    sets_total = _scalar_count(session, select(func.count(Set.id)).where(Set.game_id == game_id))
    cards_total = _scalar_count(session, select(func.count(Card.id)).where(Card.game_id == game_id))
    prints_total = _scalar_count(
        session,
        select(func.count(Print.id)).join(Set, Set.id == Print.set_id).where(Set.game_id == game_id),
    )

    images_total = _scalar_count(
        session,
        select(func.count(PrintImage.id))
        .join(Print, Print.id == PrintImage.print_id)
        .join(Set, Set.id == Print.set_id)
        .where(Set.game_id == game_id),
    )
    prints_with_image = _scalar_count(
        session,
        select(func.count(func.distinct(Print.id)))
        .join(Set, Set.id == Print.set_id)
        .join(PrintImage, PrintImage.print_id == Print.id)
        .where(Set.game_id == game_id),
    )
    prints_with_primary_image = _scalar_count(
        session,
        select(func.count(func.distinct(Print.id)))
        .join(Set, Set.id == Print.set_id)
        .join(PrintImage, and_(PrintImage.print_id == Print.id, PrintImage.is_primary.is_(True)))
        .where(Set.game_id == game_id),
    )

    prints_with_structured_identifier = _scalar_count(
        session,
        select(func.count(func.distinct(Print.id)))
        .join(Set, Set.id == Print.set_id)
        .join(PrintIdentifier, PrintIdentifier.print_id == Print.id)
        .where(Set.game_id == game_id),
    )
    direct_external_id = or_(
        Print.scryfall_id.is_not(None),
        Print.tcgdex_id.is_not(None),
        Print.yugioh_id.is_not(None),
        Print.riftbound_id.is_not(None),
    )
    prints_with_direct_identifier = _scalar_count(
        session,
        select(func.count(Print.id))
        .join(Set, Set.id == Print.set_id)
        .where(Set.game_id == game_id, direct_external_id),
    )
    prints_with_any_identifier = _scalar_count(
        session,
        select(func.count(func.distinct(Print.id)))
        .join(Set, Set.id == Print.set_id)
        .outerjoin(PrintIdentifier, PrintIdentifier.print_id == Print.id)
        .where(Set.game_id == game_id, or_(direct_external_id, PrintIdentifier.id.is_not(None))),
    )

    sets_without_prints = _scalar_count(
        session,
        select(func.count()).select_from(
            select(Set.id)
            .outerjoin(Print, Print.set_id == Set.id)
            .where(Set.game_id == game_id)
            .group_by(Set.id)
            .having(func.count(Print.id) == 0)
            .subquery()
        ),
    )
    cards_without_prints = _scalar_count(
        session,
        select(func.count()).select_from(
            select(Card.id)
            .outerjoin(Print, Print.card_id == Card.id)
            .where(Card.game_id == game_id)
            .group_by(Card.id)
            .having(func.count(Print.id) == 0)
            .subquery()
        ),
    )

    duplicate_groups, duplicate_samples = _duplicate_identity_groups(session, game_id, sample_limit)

    newest_set = session.execute(select(func.max(Set.created_at)).where(Set.game_id == game_id)).scalar_one()
    newest_card = session.execute(select(func.max(Card.created_at)).where(Card.game_id == game_id)).scalar_one()
    newest_print = session.execute(
        select(func.max(Print.created_at)).join(Set, Set.id == Print.set_id).where(Set.game_id == game_id)
    ).scalar_one()

    issues = {
        "sets_without_prints": sets_without_prints,
        "cards_without_prints": cards_without_prints,
        "sets_missing_release_date": _scalar_count(
            session, select(func.count(Set.id)).where(Set.game_id == game_id, Set.release_date.is_(None))
        ),
        "cards_missing_card_key": _scalar_count(
            session, select(func.count(Card.id)).where(Card.game_id == game_id, _blank(Card.card_key))
        ),
        "prints_missing_language": _scalar_count(
            session,
            select(func.count(Print.id)).join(Set, Set.id == Print.set_id).where(Set.game_id == game_id, _blank(Print.language)),
        ),
        "prints_missing_rarity": _scalar_count(
            session,
            select(func.count(Print.id)).join(Set, Set.id == Print.set_id).where(Set.game_id == game_id, _blank(Print.rarity)),
        ),
        "prints_missing_print_key": _scalar_count(
            session,
            select(func.count(Print.id)).join(Set, Set.id == Print.set_id).where(Set.game_id == game_id, _blank(Print.print_key)),
        ),
        "prints_without_any_image": max(prints_total - prints_with_image, 0),
        "prints_without_primary_image": max(prints_total - prints_with_primary_image, 0),
        "prints_without_external_identifier": max(prints_total - prints_with_any_identifier, 0),
        "potential_duplicate_print_identity_groups": duplicate_groups,
    }

    severe = prints_total == 0 or duplicate_groups > 0
    warning = any(value > 0 for key, value in issues.items() if key not in {"cards_missing_card_key", "sets_missing_release_date"})
    status = "critical" if severe else "warning" if warning else "healthy"

    return {
        "slug": slug,
        "name": name,
        "status": status,
        "counts": {
            "sets": sets_total,
            "cards": cards_total,
            "prints": prints_total,
            "images": images_total,
            "prints_with_any_image": prints_with_image,
            "prints_with_primary_image": prints_with_primary_image,
            "prints_with_structured_identifier": prints_with_structured_identifier,
            "prints_with_direct_identifier": prints_with_direct_identifier,
            "prints_with_any_external_identifier": prints_with_any_identifier,
        },
        "issues": issues,
        "distributions": {
            "languages": _distribution(session, Print.language, game_id=game_id),
            "variants": _distribution(session, Print.variant, game_id=game_id),
            "rarities": _distribution(session, Print.rarity, game_id=game_id),
        },
        "samples": {
            "sets_without_prints": _empty_set_samples(session, game_id, sample_limit),
            "potential_duplicate_print_identities": duplicate_samples,
        },
        "newest_created_at": {
            "set": _iso(newest_set),
            "card": _iso(newest_card),
            "print": _iso(newest_print),
        },
    }


def get_catalog_health(session, *, sample_limit: int = 20, runs_limit: int = 20) -> dict:
    game_rows = session.execute(select(Game.id, Game.slug, Game.name).order_by(Game.slug.asc())).all()
    games = [_game_health(session, game_id, slug, name, sample_limit) for game_id, slug, name in game_rows]

    totals = {
        "games": len(games),
        "sets": sum(item["counts"]["sets"] for item in games),
        "cards": sum(item["counts"]["cards"] for item in games),
        "prints": sum(item["counts"]["prints"] for item in games),
        "images": sum(item["counts"]["images"] for item in games),
    }
    status_counts = {
        "healthy": sum(item["status"] == "healthy" for item in games),
        "warning": sum(item["status"] == "warning" for item in games),
        "critical": sum(item["status"] == "critical" for item in games),
    }

    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "database_dialect": session.get_bind().dialect.name,
        "totals": totals,
        "status_counts": status_counts,
        "games": games,
        "ingest": get_ingest_status(session, runs_limit=runs_limit),
        "notes": [
            "This report audits the internal canonical database only; it does not yet prove completeness against every external TCG release.",
            "Potential duplicate print identities intentionally include NULL-language collisions that database unique constraints may not prevent.",
            "No data is modified by this audit.",
        ],
    }


def _print_human(payload: dict) -> None:
    totals = payload["totals"]
    print("=== CATALOG HEALTH ===")
    print(
        f"games={totals['games']} sets={totals['sets']} cards={totals['cards']} "
        f"prints={totals['prints']} images={totals['images']}"
    )
    print()
    for game in payload["games"]:
        counts = game["counts"]
        issues = game["issues"]
        print(f"[{game['status'].upper()}] {game['slug']} — {game['name']}")
        print(f"  sets={counts['sets']} cards={counts['cards']} prints={counts['prints']} images={counts['images']}")
        print(
            "  missing: "
            f"image={issues['prints_without_any_image']} "
            f"primary_image={issues['prints_without_primary_image']} "
            f"language={issues['prints_missing_language']} "
            f"rarity={issues['prints_missing_rarity']} "
            f"external_id={issues['prints_without_external_identifier']} "
            f"print_key={issues['prints_missing_print_key']}"
        )
        print(
            "  structure: "
            f"empty_sets={issues['sets_without_prints']} "
            f"cards_without_prints={issues['cards_without_prints']} "
            f"duplicate_identity_groups={issues['potential_duplicate_print_identity_groups']}"
        )
        print()


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only health audit for the canonical TCG catalog")
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of human-readable output")
    parser.add_argument("--sample-limit", type=int, default=20)
    parser.add_argument("--runs-limit", type=int, default=20)
    args = parser.parse_args()

    db.init_engine()
    with db.SessionLocal() as session:
        payload = get_catalog_health(
            session,
            sample_limit=max(args.sample_limit, 1),
            runs_limit=max(args.runs_limit, 1),
        )

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    else:
        _print_human(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
