from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation

from flask import Blueprint, jsonify, request
from sqlalchemy import select

from app import db
from app.models import Card, Game, Print, PrintImage, Set
from app.routes.market_reference import _build_print_market_payloads, _load_print_market_rows
from app.user_auth_service import resolve_session
from app.user_models import UserCollectionItem, UserWishlistItem


user_library_bp = Blueprint("user_library", __name__)


def _bearer_token() -> str | None:
    value = str(request.headers.get("Authorization") or "").strip()
    if value.lower().startswith("bearer "):
        return value[7:].strip() or None
    return None


def _current_user(session):
    resolved = resolve_session(session, _bearer_token())
    return resolved[0] if resolved else None


def _decimal(value, *, field: str) -> Decimal | None:
    if value in {None, ""}:
        return None
    try:
        parsed = Decimal(str(value)).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        raise ValueError(field)
    if parsed < 0:
        raise ValueError(field)
    return parsed


def _date(value, *, field: str) -> date | None:
    if value in {None, ""}:
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        raise ValueError(field)


def _print_exists(session, print_id: int) -> bool:
    return session.execute(select(Print.id).where(Print.id == print_id)).scalar_one_or_none() is not None


def _current_cardmarket_price(session, print_id: int) -> dict | None:
    """Return only today's exact Cardmarket valuation for a physical Print.

    Collection/wishlist must never fall back to a sibling printing, another
    source, a stale canonical snapshot, or a last-known Cardmarket value. The
    same current-only physical identity reader powers the public Print page.
    """
    rows = _load_print_market_rows(session, [print_id])
    payload = _build_print_market_payloads(rows, [print_id]).get(int(print_id)) or {}
    price = payload.get("price") if payload.get("status") == "priced" else None
    if not isinstance(price, dict):
        return None

    conservative = price.get("conservative")
    display_value = conservative
    display_kind = "conservative" if conservative is not None else None
    if display_value is None and price.get("trend") is not None:
        display_value = price.get("trend")
        display_kind = "trend"
    if display_value is None and price.get("average") is not None:
        display_value = price.get("average")
        display_kind = "average"
    if display_value is None and price.get("minimum") is not None:
        display_value = price.get("minimum")
        display_kind = "minimum"
    if display_value is None:
        return None

    return {
        "value": float(display_value),
        "valuation_value": float(conservative) if conservative is not None else None,
        "minimum": float(price["minimum"]) if price.get("minimum") is not None else None,
        "conservative": float(conservative) if conservative is not None else None,
        "trend": float(price["trend"]) if price.get("trend") is not None else None,
        "average": float(price["average"]) if price.get("average") is not None else None,
        "currency": price.get("currency") or "EUR",
        "source": "cardmarket",
        "as_of": price.get("as_of"),
        "kind": display_kind,
        "finish": price.get("finish"),
        "portfolio_method": "cardmarket_low_ex_plus_or_foil_low" if conservative is not None else None,
    }


def _latest_price(session, print_id: int) -> dict | None:
    # Kept as the internal library call-site name for API compatibility. Its
    # semantics are now deliberately current-only and Cardmarket-only.
    return _current_cardmarket_price(session, print_id)


def _image_url(session, print_id: int) -> str | None:
    return session.execute(
        select(PrintImage.url)
        .where(PrintImage.print_id == print_id)
        .order_by(PrintImage.is_primary.desc(), PrintImage.id.asc())
        .limit(1)
    ).scalar_one_or_none()


def _print_payload(session, print_row: Print, card: Card, set_row: Set, game: Game) -> dict:
    return {
        "id": print_row.id,
        "card_id": card.id,
        "card_name": card.name,
        "game": game.slug,
        "set_code": set_row.code,
        "set_name": set_row.name,
        "collector_number": print_row.collector_number,
        "language": print_row.language,
        "rarity": print_row.rarity,
        "variant": print_row.variant,
        "is_foil": bool(print_row.is_foil),
        "image_url": _image_url(session, print_row.id),
    }


def _collection_payload(session, row, print_row, card, set_row, game) -> dict:
    return {
        "id": row.id,
        "quantity": row.quantity,
        "condition": row.condition,
        "notes": row.notes,
        "purchase_price": float(row.purchase_price) if row.purchase_price is not None else None,
        "purchase_currency": row.purchase_currency,
        "acquired_at": row.acquired_at.isoformat() if row.acquired_at else None,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "print": _print_payload(session, print_row, card, set_row, game),
        "latest_price": _latest_price(session, print_row.id),
    }


def _wishlist_payload(session, row, print_row, card, set_row, game) -> dict:
    return {
        "id": row.id,
        "priority": row.priority,
        "target_price": float(row.target_price) if row.target_price is not None else None,
        "target_currency": row.target_currency,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "print": _print_payload(session, print_row, card, set_row, game),
        "latest_price": _latest_price(session, print_row.id),
    }


def _joined_rows(session, model, user_id: int):
    return session.execute(
        select(model, Print, Card, Set, Game)
        .join(Print, Print.id == model.print_id)
        .join(Card, Card.id == Print.card_id)
        .join(Set, Set.id == Print.set_id)
        .join(Game, Game.id == Set.game_id)
        .where(model.user_id == user_id)
        .order_by(model.updated_at.desc(), model.id.desc())
        .limit(500)
    ).all()


@user_library_bp.get("/api/v2/me/collection")
def get_collection():
    with db.SessionLocal() as session:
        user = _current_user(session)
        if not user:
            return jsonify({"error": "authentication_required"}), 401
        rows = _joined_rows(session, UserCollectionItem, user.id)
        items = [_collection_payload(session, *row) for row in rows]
        session.commit()

    valued_items = [
        item for item in items
        if item.get("latest_price")
        and item["latest_price"].get("currency") == "EUR"
        and item["latest_price"].get("valuation_value") is not None
    ]
    priced_value = sum(
        item["latest_price"]["valuation_value"] * item["quantity"]
        for item in valued_items
    )
    return jsonify({
        "items": items,
        "count": len(items),
        "known_value_eur": round(priced_value, 2),
        "valuation_coverage_count": len(valued_items),
        "valuation_method": "cardmarket_low_ex_plus_or_foil_low",
    })


@user_library_bp.post("/api/v2/me/collection")
def upsert_collection_item():
    body = request.get_json(silent=True) or {}
    try:
        print_id = int(body.get("print_id"))
        quantity = int(body.get("quantity", 1))
    except (TypeError, ValueError):
        return jsonify({"error": "invalid_print_or_quantity"}), 400
    if print_id <= 0 or quantity < 1 or quantity > 9999:
        return jsonify({"error": "invalid_print_or_quantity"}), 400

    condition = str(body.get("condition") or "").strip()[:32] or None
    notes = str(body.get("notes") or "").strip()[:4000] or None
    currency = str(body.get("purchase_currency") or "").strip().upper()[:3] or None
    try:
        purchase_price = _decimal(body.get("purchase_price"), field="purchase_price")
        acquired_at = _date(body.get("acquired_at"), field="acquired_at")
    except ValueError as exc:
        return jsonify({"error": "invalid_field", "field": str(exc)}), 400

    with db.SessionLocal() as session:
        user = _current_user(session)
        if not user:
            return jsonify({"error": "authentication_required"}), 401
        if not _print_exists(session, print_id):
            return jsonify({"error": "print_not_found"}), 404
        row = session.execute(
            select(UserCollectionItem).where(
                UserCollectionItem.user_id == user.id,
                UserCollectionItem.print_id == print_id,
            )
        ).scalar_one_or_none()
        if row is None:
            row = UserCollectionItem(user_id=user.id, print_id=print_id)
            session.add(row)
        row.quantity = quantity
        row.condition = condition
        row.notes = notes
        row.purchase_price = purchase_price
        row.purchase_currency = currency
        row.acquired_at = acquired_at
        session.commit()
    return jsonify({"ok": True, "print_id": print_id, "quantity": quantity})


@user_library_bp.delete("/api/v2/me/collection")
def delete_collection_item():
    body = request.get_json(silent=True) or {}
    raw_print_id = request.args.get("print_id") or body.get("print_id")
    try:
        print_id = int(raw_print_id)
    except (TypeError, ValueError):
        return jsonify({"error": "print_id_required"}), 400
    with db.SessionLocal() as session:
        user = _current_user(session)
        if not user:
            return jsonify({"error": "authentication_required"}), 401
        row = session.execute(
            select(UserCollectionItem).where(
                UserCollectionItem.user_id == user.id,
                UserCollectionItem.print_id == print_id,
            )
        ).scalar_one_or_none()
        if row:
            session.delete(row)
        session.commit()
    return jsonify({"ok": True})


@user_library_bp.get("/api/v2/me/wishlist")
def get_wishlist():
    with db.SessionLocal() as session:
        user = _current_user(session)
        if not user:
            return jsonify({"error": "authentication_required"}), 401
        rows = _joined_rows(session, UserWishlistItem, user.id)
        items = [_wishlist_payload(session, *row) for row in rows]
        session.commit()
    return jsonify({"items": items, "count": len(items)})


@user_library_bp.post("/api/v2/me/wishlist")
def upsert_wishlist_item():
    body = request.get_json(silent=True) or {}
    try:
        print_id = int(body.get("print_id"))
        priority = int(body.get("priority", 0))
    except (TypeError, ValueError):
        return jsonify({"error": "invalid_print_or_priority"}), 400
    if print_id <= 0 or priority < 0 or priority > 3:
        return jsonify({"error": "invalid_print_or_priority"}), 400
    currency = str(body.get("target_currency") or "").strip().upper()[:3] or None
    try:
        target_price = _decimal(body.get("target_price"), field="target_price")
    except ValueError as exc:
        return jsonify({"error": "invalid_field", "field": str(exc)}), 400

    with db.SessionLocal() as session:
        user = _current_user(session)
        if not user:
            return jsonify({"error": "authentication_required"}), 401
        if not _print_exists(session, print_id):
            return jsonify({"error": "print_not_found"}), 404
        row = session.execute(
            select(UserWishlistItem).where(
                UserWishlistItem.user_id == user.id,
                UserWishlistItem.print_id == print_id,
            )
        ).scalar_one_or_none()
        if row is None:
            row = UserWishlistItem(user_id=user.id, print_id=print_id)
            session.add(row)
        row.priority = priority
        row.target_price = target_price
        row.target_currency = currency
        session.commit()
    return jsonify({"ok": True, "print_id": print_id})


@user_library_bp.delete("/api/v2/me/wishlist")
def delete_wishlist_item():
    body = request.get_json(silent=True) or {}
    raw_print_id = request.args.get("print_id") or body.get("print_id")
    try:
        print_id = int(raw_print_id)
    except (TypeError, ValueError):
        return jsonify({"error": "print_id_required"}), 400
    with db.SessionLocal() as session:
        user = _current_user(session)
        if not user:
            return jsonify({"error": "authentication_required"}), 401
        row = session.execute(
            select(UserWishlistItem).where(
                UserWishlistItem.user_id == user.id,
                UserWishlistItem.print_id == print_id,
            )
        ).scalar_one_or_none()
        if row:
            session.delete(row)
        session.commit()
    return jsonify({"ok": True})
