from __future__ import annotations

from app import db
from app.models import Card, Game, Print, Set


def _register(client, *, name: str, email: str, ip: str) -> str:
    response = client.post(
        "/api/v2/auth/register",
        headers={"X-Forwarded-For": ip},
        json={
            "name": name,
            "email": email,
            "password": "CorrectHorseBattery1!",
            "terms_accepted": True,
            "marketing_consent": False,
        },
    )
    assert response.status_code == 201
    return response.get_json()["session_token"]


def _seed_print() -> int:
    with db.SessionLocal() as session:
        game = Game(slug="onepiece", name="One Piece Card Game")
        session.add(game)
        session.flush()
        set_row = Set(game_id=game.id, code="op-05", name="Awakening of the New Era")
        session.add(set_row)
        session.flush()
        card = Card(game_id=game.id, name="Monkey.D.Luffy", card_key="onepiece:luffy")
        session.add(card)
        session.flush()
        print_row = Print(
            set_id=set_row.id,
            card_id=card.id,
            collector_number="OP05-119",
            language="en",
            rarity="SEC",
            variant="default",
            print_key="onepiece:op05-119:en:default",
        )
        session.add(print_row)
        session.commit()
        return print_row.id


def _headers(token: str, ip: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "X-Forwarded-For": ip}


def test_collection_and_wishlist_are_isolated_between_users(client):
    print_id = _seed_print()
    eva = _register(client, name="Eva", email="eva@example.com", ip="203.0.113.210")
    other = _register(client, name="Other", email="other@example.com", ip="203.0.113.211")

    eva_headers = _headers(eva, "203.0.113.210")
    other_headers = _headers(other, "203.0.113.211")
    assert client.post(
        "/api/v2/me/collection",
        headers=eva_headers,
        json={"print_id": print_id, "quantity": 2, "notes": "Eva only"},
    ).status_code == 200
    assert client.post(
        "/api/v2/me/wishlist",
        headers=eva_headers,
        json={"print_id": print_id, "priority": 3},
    ).status_code == 200

    assert client.get("/api/v2/me/collection", headers=other_headers).get_json()["items"] == []
    assert client.get("/api/v2/me/wishlist", headers=other_headers).get_json()["items"] == []

    # Deleting the same print ID as another user must never touch Eva's rows.
    assert client.delete(
        f"/api/v2/me/collection?print_id={print_id}", headers=other_headers
    ).status_code == 200
    assert client.delete(
        f"/api/v2/me/wishlist?print_id={print_id}", headers=other_headers
    ).status_code == 200

    eva_collection = client.get("/api/v2/me/collection", headers=eva_headers).get_json()["items"]
    eva_wishlist = client.get("/api/v2/me/wishlist", headers=eva_headers).get_json()["items"]
    assert len(eva_collection) == 1
    assert eva_collection[0]["notes"] == "Eva only"
    assert len(eva_wishlist) == 1
    assert eva_wishlist[0]["priority"] == 3


def test_library_write_ignores_forged_user_id(client):
    print_id = _seed_print()
    eva = _register(client, name="Eva", email="eva@example.com", ip="203.0.113.212")
    other = _register(client, name="Other", email="other@example.com", ip="203.0.113.213")

    response = client.post(
        "/api/v2/me/collection",
        headers=_headers(other, "203.0.113.213"),
        json={"print_id": print_id, "quantity": 1, "user_id": 1},
    )
    assert response.status_code == 200
    assert client.get(
        "/api/v2/me/collection", headers=_headers(eva, "203.0.113.212")
    ).get_json()["items"] == []
    assert len(client.get(
        "/api/v2/me/collection", headers=_headers(other, "203.0.113.213")
    ).get_json()["items"]) == 1
