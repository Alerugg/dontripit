from __future__ import annotations

from decimal import Decimal
from urllib.parse import urljoin

from flask import Blueprint, jsonify, request
from sqlalchemy import bindparam, text
from sqlalchemy.exc import SQLAlchemyError

from app import db


card_prints_bp = Blueprint("card_prints", __name__)
_CARDMARKET_BASE = "https://www.cardmarket.com"
_LANGUAGE_ORDER = {
    "es": 0,
    "en": 1,
    "fr": 2,
    "de": 3,
    "it": 4,
    "pt": 5,
    "ja": 6,
    "ko": 7,
    "zh": 8,
    "zhs": 8,
    "zht": 9,
}


def _bounded_int(value, *, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return min(max(parsed, minimum), maximum)


def _release_payload(row: dict) -> dict:
    release = {
        "id": int(row["id"]),
        "source": row["source"],
        "external_id": row["external_id"],
        "name": row["name"],
        "code": row["code"],
        "release_type": row["release_type"],
        "release_date": row["release_date"],
        "language": row["language"],
        "region": row["region"],
    }
    if hasattr(release.get("release_date"), "isoformat"):
        release["release_date"] = release["release_date"].isoformat()
    return release


def _cardmarket_url(path: str | None) -> str | None:
    raw = str(path or "").strip()
    if not raw:
        return None
    if raw.startswith("http://") or raw.startswith("https://"):
        return raw
    return urljoin(_CARDMARKET_BASE, raw if raw.startswith("/") else f"/{raw}")


def _number(value):
    if value is None:
        return None
    if isinstance(value, Decimal):
        return float(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _language_key(value: str | None) -> tuple[int, str]:
    language = str(value or "").strip().lower()
    return (_LANGUAGE_ORDER.get(language, 100), language)


def _print_payload(row: dict) -> dict:
    return {
        "id": int(row["print_id"]),
        "print_id": int(row["print_id"]),
        "language": str(row.get("language") or "").lower() or None,
        "collector_number": row.get("collector_number"),
        "rarity": row.get("rarity"),
        "is_foil": bool(row.get("is_foil")),
        "variant": row.get("variant"),
        "set_id": int(row["set_id"]),
        "set_code": row.get("set_code"),
        "set_name": row.get("set_name"),
        "set_region": row.get("set_region"),
        "primary_image_url": row.get("primary_image_url"),
    }


def _fallback_version_key(row: dict) -> str:
    return "|".join(
        [
            str(row.get("set_id") or ""),
            str(row.get("collector_number") or "").strip().lower(),
            str(row.get("rarity") or "").strip().lower(),
            "foil" if row.get("is_foil") else "nonfoil",
            str(row.get("variant") or "default").strip().lower(),
        ]
    )


@card_prints_bp.get("/api/v1/cards/<int:card_id>/versions")
def list_card_versions(card_id: int):
    """Return a simple Card -> market version -> physical language hierarchy.

    Cardmarket product identity is authoritative only for Prints that have one
    unambiguous accepted current Cardmarket product link. Several physical
    languages may therefore share one Cardmarket product. Prints without a
    certified market link remain visible in a fallback version group but never
    receive a guessed Cardmarket URL.
    """

    card_sql = text(
        """
        SELECT c.id,c.name,g.slug AS game
        FROM cards c
        JOIN games g ON g.id=c.game_id
        WHERE c.id=:card_id
        """
    )
    prints_sql = text(
        """
        SELECT p.id AS print_id,p.language,p.collector_number,p.rarity,p.is_foil,p.variant,
               s.id AS set_id,s.code AS set_code,s.name AS set_name,s.region AS set_region,
               (
                 SELECT pi.url
                 FROM print_images pi
                 WHERE pi.print_id=p.id
                 ORDER BY pi.is_primary DESC,pi.id ASC
                 LIMIT 1
               ) AS primary_image_url
        FROM prints p
        JOIN sets s ON s.id=p.set_id
        WHERE p.card_id=:card_id
        ORDER BY lower(COALESCE(s.code,'')),lower(COALESCE(p.collector_number,'')),p.id
        """
    )
    links_sql = text(
        """
        WITH candidates AS (
          SELECT l.print_id,e.id AS market_row_id,e.external_id,e.name,e.website_path,
                 e.metacard_external_id,e.expansion_external_id,e.raw_json,
                 COUNT(*) OVER (PARTITION BY l.print_id) AS accepted_product_count
          FROM external_catalog_print_links l
          JOIN external_catalog_products e ON e.id=l.external_product_id
          WHERE e.source='cardmarket'
            AND e.product_group='single'
            AND l.link_status IN ('accepted','mapped','exact')
            AND l.print_id IN :print_ids
            AND e.last_seen_at=(
              SELECT MAX(e2.last_seen_at)
              FROM external_catalog_products e2
              WHERE e2.source='cardmarket' AND e2.game_id=e.game_id
            )
        )
        SELECT * FROM candidates
        WHERE accepted_product_count=1
        ORDER BY print_id
        """
    ).bindparams(bindparam("print_ids", expanding=True))
    prices_sql = text(
        """
        SELECT DISTINCT ON (mp.external_product_id,mp.price_variant)
               mp.external_product_id,mp.currency,mp.price_variant,mp.price_low,
               mp.price_mid,mp.price_market,mp.price_last,mp.avg1,mp.avg7,mp.avg30,mp.as_of
        FROM external_market_price_snapshots mp
        WHERE mp.external_product_id IN :market_ids
        ORDER BY mp.external_product_id,mp.price_variant,mp.as_of DESC,mp.id DESC
        """
    ).bindparams(bindparam("market_ids", expanding=True))

    try:
        with db.SessionLocal() as session:
            card = session.execute(card_sql, {"card_id": card_id}).mappings().first()
            if card is None:
                return jsonify({"error": "not_found", "detail": f"card {card_id} not found"}), 404
            print_rows = [dict(row) for row in session.execute(prints_sql, {"card_id": card_id}).mappings().all()]
            print_ids = [int(row["print_id"]) for row in print_rows]
            link_rows = [
                dict(row)
                for row in session.execute(links_sql, {"print_ids": print_ids or [-1]}).mappings().all()
            ]
            market_ids = sorted({int(row["market_row_id"]) for row in link_rows})
            price_rows = [
                dict(row)
                for row in session.execute(prices_sql, {"market_ids": market_ids or [-1]}).mappings().all()
            ]
    except SQLAlchemyError:
        return jsonify({"error": "card_versions_unavailable"}), 503

    link_by_print = {int(row["print_id"]): row for row in link_rows}
    prices_by_market: dict[int, list[dict]] = {}
    for row in price_rows:
        market_id = int(row["external_product_id"])
        as_of = row.get("as_of")
        prices_by_market.setdefault(market_id, []).append(
            {
                "variant": row.get("price_variant"),
                "currency": row.get("currency"),
                "low": _number(row.get("price_low")),
                "mid": _number(row.get("price_mid")),
                "market": _number(row.get("price_market")),
                "last": _number(row.get("price_last")),
                "avg1": _number(row.get("avg1")),
                "avg7": _number(row.get("avg7")),
                "avg30": _number(row.get("avg30")),
                "as_of": as_of.isoformat() if hasattr(as_of, "isoformat") else as_of,
            }
        )

    groups: dict[str, dict] = {}
    for row in print_rows:
        print_id = int(row["print_id"])
        market = link_by_print.get(print_id)
        if market:
            key = f"cardmarket:{market['market_row_id']}"
            market_id = int(market["market_row_id"])
            group = groups.setdefault(
                key,
                {
                    "key": key,
                    "market_status": "linked",
                    "cardmarket": {
                        "external_product_id": str(market.get("external_id") or "") or None,
                        "external_metacard_id": str(market.get("metacard_external_id") or "") or None,
                        "name": market.get("name"),
                        "url": _cardmarket_url(market.get("website_path")),
                        "website_path": market.get("website_path"),
                    },
                    "price_guides": prices_by_market.get(market_id, []),
                    "prints": [],
                },
            )
        else:
            fallback = _fallback_version_key(row)
            key = f"catalog:{fallback}"
            group = groups.setdefault(
                key,
                {
                    "key": key,
                    "market_status": "unlinked",
                    "cardmarket": None,
                    "price_guides": [],
                    "prints": [],
                },
            )
        group["prints"].append(_print_payload(row))

    versions = []
    all_languages: set[str] = set()
    for group in groups.values():
        prints = sorted(
            group["prints"],
            key=lambda item: (_language_key(item.get("language")), int(item["print_id"])),
        )
        languages: dict[str, list[int]] = {}
        for item in prints:
            language = str(item.get("language") or "unknown").lower()
            languages.setdefault(language, []).append(int(item["print_id"]))
            if language != "unknown":
                all_languages.add(language)
        representative = next((item for item in prints if item.get("primary_image_url")), prints[0] if prints else None)
        group["prints"] = prints
        group["languages"] = [
            {"code": language, "print_ids": ids, "print_count": len(ids)}
            for language, ids in sorted(languages.items(), key=lambda pair: _language_key(pair[0]))
        ]
        group["representative_print"] = representative
        group["set_code"] = representative.get("set_code") if representative else None
        group["set_name"] = representative.get("set_name") if representative else None
        group["collector_number"] = representative.get("collector_number") if representative else None
        group["rarity"] = representative.get("rarity") if representative else None
        group["variant"] = representative.get("variant") if representative else None
        group["is_foil"] = representative.get("is_foil") if representative else False
        versions.append(group)

    versions.sort(
        key=lambda item: (
            0 if item.get("market_status") == "linked" else 1,
            str(item.get("set_code") or "").lower(),
            str(item.get("collector_number") or "").lower(),
            str(item.get("cardmarket", {}).get("external_product_id") if item.get("cardmarket") else item.get("key")),
        )
    )

    return jsonify(
        {
            "card": {"id": int(card["id"]), "name": card["name"], "game": card["game"]},
            "languages": sorted(all_languages, key=_language_key),
            "version_count": len(versions),
            "print_count": len(print_rows),
            "versions": versions,
        }
    )


@card_prints_bp.get("/api/v1/cards/<int:card_id>/prints")
def list_card_prints(card_id: int):
    """Paginate every exact physical Print for one logical Card.

    This endpoint exists so high-reprint cards (basic lands, Sol Ring,
    Blue-Eyes, etc.) are never silently truncated by the card detail reader.
    Each row keeps the stable Print id and its independent physical releases.
    """
    limit = _bounded_int(request.args.get("limit"), default=24, minimum=1, maximum=50)
    offset = _bounded_int(request.args.get("offset"), default=0, minimum=0, maximum=100_000)

    card_sql = text(
        """
        SELECT c.id, c.name, g.slug AS game
        FROM cards c
        JOIN games g ON g.id=c.game_id
        WHERE c.id=:card_id
        """
    )
    count_sql = text("SELECT COUNT(*) FROM prints WHERE card_id=:card_id")
    rows_sql = text(
        """
        SELECT p.id,
               p.id AS print_id,
               p.card_id,
               c.name AS card_name,
               g.slug AS game,
               s.id AS set_id,
               s.code AS set_code,
               s.name AS set_name,
               p.collector_number,
               p.language,
               p.rarity,
               p.is_foil,
               p.variant,
               (
                 SELECT pi.url
                 FROM print_images pi
                 WHERE pi.print_id=p.id
                 ORDER BY pi.is_primary DESC,pi.id ASC
                 LIMIT 1
               ) AS primary_image_url
        FROM prints p
        JOIN cards c ON c.id=p.card_id
        JOIN games g ON g.id=c.game_id
        JOIN sets s ON s.id=p.set_id
        WHERE p.card_id=:card_id
        ORDER BY lower(COALESCE(s.code,'')) ASC,
                 COALESCE(NULLIF(substring(COALESCE(p.collector_number,'') from '([0-9]+)[^0-9]*$'), '')::bigint, 9223372036854775807) ASC,
                 lower(COALESCE(p.collector_number,'')) ASC,
                 CASE WHEN lower(COALESCE(p.variant,'')) IN ('default','base','') THEN 0 ELSE 1 END ASC,
                 lower(COALESCE(p.variant,'')) ASC,
                 p.id ASC
        LIMIT :limit OFFSET :offset
        """
    )
    releases_sql = text(
        """
        SELECT pr.print_id,
               cr.id,cr.source,cr.external_id,cr.name,cr.code,
               cr.release_type,cr.release_date,cr.language,cr.region
        FROM print_releases pr
        JOIN catalog_releases cr ON cr.id=pr.release_id
        WHERE pr.print_id = ANY(:print_ids)
        ORDER BY pr.print_id,cr.release_date NULLS LAST,cr.id
        """
    )

    try:
        with db.SessionLocal() as session:
            card = session.execute(card_sql, {"card_id": card_id}).mappings().first()
            if card is None:
                return jsonify({"error": "not_found", "detail": f"card {card_id} not found"}), 404
            total = int(session.execute(count_sql, {"card_id": card_id}).scalar_one())
            rows = [dict(row) for row in session.execute(rows_sql, {"card_id": card_id, "limit": limit, "offset": offset}).mappings().all()]
            ids = [int(row["print_id"]) for row in rows]
            release_rows = session.execute(releases_sql, {"print_ids": ids or [-1]}).mappings().all()
    except SQLAlchemyError:
        return jsonify({"error": "card_prints_unavailable"}), 503

    releases_by_print: dict[int, list[dict]] = {}
    for source_row in release_rows:
        row = dict(source_row)
        releases_by_print.setdefault(int(row["print_id"]), []).append(_release_payload(row))

    items = []
    for row in rows:
        print_id = int(row["print_id"])
        row["physical_releases"] = releases_by_print.get(print_id, [])
        row["physical_release_names"] = [release["name"] for release in row["physical_releases"] if release.get("name")]
        items.append(row)

    return jsonify({
        "card": {"id": int(card["id"]), "name": card["name"], "game": card["game"]},
        "items": items,
        "total": total,
        "limit": limit,
        "offset": offset,
        "complete": offset + len(items) >= total,
    })


@card_prints_bp.get("/api/v1/prints/<int:print_id>/physical-releases")
def list_print_physical_releases(print_id: int):
    identity_sql = text(
        """
        SELECT p.id AS print_id,p.card_id,c.name AS card_name,g.slug AS game,
               s.id AS set_id,s.code AS set_code,s.name AS set_name,
               p.collector_number,p.language,p.rarity,p.is_foil,p.variant
        FROM prints p
        JOIN cards c ON c.id=p.card_id
        JOIN games g ON g.id=c.game_id
        JOIN sets s ON s.id=p.set_id
        WHERE p.id=:print_id
        """
    )
    releases_sql = text(
        """
        SELECT cr.id,cr.source,cr.external_id,cr.name,cr.code,
               cr.release_type,cr.release_date,cr.language,cr.region
        FROM print_releases pr
        JOIN catalog_releases cr ON cr.id=pr.release_id
        WHERE pr.print_id=:print_id
        ORDER BY cr.release_date NULLS LAST,cr.id
        """
    )
    try:
        with db.SessionLocal() as session:
            identity = session.execute(identity_sql, {"print_id": print_id}).mappings().first()
            if identity is None:
                return jsonify({"error": "not_found", "detail": f"print {print_id} not found"}), 404
            releases = [_release_payload(dict(row)) for row in session.execute(releases_sql, {"print_id": print_id}).mappings().all()]
    except SQLAlchemyError:
        return jsonify({"error": "print_physical_releases_unavailable"}), 503

    return jsonify({
        "print": dict(identity),
        "physical_releases": releases,
        "physical_release_names": [release["name"] for release in releases if release.get("name")],
    })
