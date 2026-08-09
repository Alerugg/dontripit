from __future__ import annotations

import os
import secrets

from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker

from app.search_v2.onepiece_exact_collector import exact_onepiece_collector_search
from app.user_auth_service import issue_session, password_hash, password_matches, resolve_session, utcnow
from app.user_models import User, UserCollectionItem, UserWishlistItem


def _database_url() -> str:
    url = os.getenv("DATABASE_URL_UNPOOLED") or os.getenv("DATABASE_URL")
    if not url:
        raise SystemExit("DATABASE_URL_UNPOOLED or DATABASE_URL is required")
    if url.startswith("postgres://"):
        return "postgresql://" + url[len("postgres://"):]
    return url


def main() -> None:
    engine = create_engine(_database_url(), pool_pre_ping=True, future=True)

    # First prove the exact One Piece behavior on committed catalog data.
    with engine.connect() as conn:
        normalized_count = int(
            conn.execute(
                text(
                    """
                    SELECT COUNT(*)
                    FROM print_search_profiles psp
                    JOIN games g ON g.id = psp.game_id
                    WHERE g.slug = 'onepiece'
                      AND psp.normalized_collector_number = 'OP05-119'
                    """
                )
            ).scalar_one()
        )
    if normalized_count < 2:
        raise SystemExit(f"OP05-119 still collapses to fewer than 2 physical editions in Search V2 data: {normalized_count}")

    # Then exercise auth + exact-print collection writes inside an outer
    # transaction that is always rolled back. No smoke user survives this run.
    connection = engine.connect()
    transaction = connection.begin()
    SmokeSession = sessionmaker(bind=connection, autoflush=False, expire_on_commit=False, future=True)
    session = SmokeSession()
    email = f"mvp-smoke-{secrets.token_hex(8)}@example.invalid"

    try:
        exact = exact_onepiece_collector_search(
            session,
            query="OP05-119",
            game="onepiece",
            limit=100,
        )
        if exact is None or len(exact) < 2:
            raise SystemExit(f"Exact One Piece search returned an invalid result set: {0 if exact is None else len(exact)}")
        print_ids = [int(row["print_id"]) for row in exact]
        if len(print_ids) != len(set(print_ids)):
            raise SystemExit("Exact One Piece search returned duplicate physical print IDs")
        if any(row.get("type") != "print" for row in exact):
            raise SystemExit("Exact One Piece collector search must return physical print rows")

        password = f"Smoke-{secrets.token_urlsafe(18)}"
        user = User(
            name="MVP Smoke",
            email=email,
            password_hash=password_hash(password),
            marketing_consent=False,
            terms_accepted_at=utcnow(),
            is_active=True,
        )
        session.add(user)
        session.flush()
        if not password_matches(user, password):
            raise SystemExit("Password hash verification failed")
        if password_matches(user, password + "-wrong"):
            raise SystemExit("Incorrect password unexpectedly verified")

        raw_token, user_session = issue_session(
            session,
            user=user,
            remember=False,
            user_agent="Dontripit-MVP-Smoke/1.0",
        )
        session.flush()
        if raw_token in user_session.token_hash or len(user_session.token_hash) != 64:
            raise SystemExit("Session token is not stored as an opaque SHA-256 hash")
        resolved = resolve_session(session, raw_token)
        if not resolved or resolved[0].id != user.id:
            raise SystemExit("Issued user session could not be resolved")

        selected_print_id = print_ids[0]
        session.add(UserCollectionItem(user_id=user.id, print_id=selected_print_id, quantity=2))
        session.add(UserWishlistItem(user_id=user.id, print_id=selected_print_id, priority=1))
        session.flush()

        collection_count = int(
            session.execute(
                select(text("COUNT(*)")).select_from(UserCollectionItem).where(UserCollectionItem.user_id == user.id)
            ).scalar_one()
        )
        wishlist_count = int(
            session.execute(
                select(text("COUNT(*)")).select_from(UserWishlistItem).where(UserWishlistItem.user_id == user.id)
            ).scalar_one()
        )
        if collection_count != 1 or wishlist_count != 1:
            raise SystemExit(
                f"User library writes failed: collection={collection_count} wishlist={wishlist_count}"
            )

        print(
            {
                "op05_119_physical_editions": len(exact),
                "op05_119_print_ids_unique": len(set(print_ids)),
                "session_hash_only": True,
                "collection_rows": collection_count,
                "wishlist_rows": wishlist_count,
                "rollback_only": True,
            }
        )
    finally:
        session.close()
        transaction.rollback()
        connection.close()

    with engine.connect() as conn:
        leaked = int(conn.execute(text("SELECT COUNT(*) FROM users WHERE email=:email"), {"email": email}).scalar_one())
    if leaked != 0:
        raise SystemExit(f"Smoke user leaked after rollback: {email}")
    print("MVP LIVE CONTRACT SMOKE: PASS")


if __name__ == "__main__":
    main()
