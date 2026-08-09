from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation

from flask import Blueprint, jsonify, request
from sqlalchemy import or_, select

from app import db
from app.models import Card, Game, Price, PriceSnapshot, PriceSource, Print, PrintImage, Set
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


def _latest_price(session, print_id: int) -> dict | None:
    snapshot = session.execute(
        select(
            PriceSnapshot.price_market,
            PriceSnapshot.price_last,
            PriceSnapshot.currency,
            PriceSnapshot.as_of,
            PriceSource.name,
        )
        .join(PriceSource, PriceSource.id == PriceSnapshot.source_id)
        .where(
            PriceSnapshot.entity_type == "print",
            PriceSnapshot.entity_id == print_id,
            or_(PriceSnapshot.price_market.is_not(None), PriceSnapshot.price_last.is_not(None)),
        )
        .order_by(PriceSnapshot.as_of.desc())
        .limit(1)
    ).first()
    if snapshot:
        market, last, currency, as_of, source = snapshot
        value = market if market is not None else last
        return {
            "value": float(value),
            "currency": currency,
            "source": source,
            "as_of": as_of.isoformat() if as_of else None,
            "kind": "market" if market is not None else "last",
        }

    observed = session.execute(
        select(Price.price, Price.currency, Price.captured_at, PriceSource.name)
        .join(PriceSource, PriceSource.id == Price.source_id)
        .where(Price.print_id == print_id)
        .order_by(Price.captured_at.desc())
        .limit(1)
    ).first()
    if observed:
        value, currency, captured_at, source = observed
        return {
            "value": float(value),
            "currency": currency,
            "source": source,
            "as_of": captured_at.isoformat() if captured_at else None,
            "kind": "observed",
        }
    return None


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
    priced_value = sum(
        (item["latest_price"]["value"] * item["quantity"])
        for item in items
        if item.get("latest_price") and item["latest_price"].get("currency") == "EUR"
    )
    return jsonify({"items": items, "count": len(items), "known_value_eur": round(priced_value, 2)})


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
