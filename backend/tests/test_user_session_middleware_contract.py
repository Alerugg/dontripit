from __future__ import annotations

import os
import unittest

from flask import Flask, jsonify

from app.auth.middleware import _is_user_session_route, _required_scope, register_api_product_middleware


class UserSessionMiddlewareContractTest(unittest.TestCase):
    def setUp(self):
        os.environ["USER_AUTH_IP_RATE_LIMIT_RPM"] = "2"
        os.environ["USER_SESSION_IP_RATE_LIMIT_RPM"] = "5"
        os.environ["PUBLIC_HUB_CATALOG_ENABLED"] = "false"

        app = Flask(__name__)
        app.config.update(TESTING=True)

        @app.post("/api/v2/auth/register")
        def register():
            return jsonify({"ok": True})

        @app.post("/api/v2/auth/login")
        def login():
            return jsonify({"ok": True})

        @app.get("/api/v2/auth/me")
        def me():
            return jsonify({"ok": True})

        @app.get("/api/v2/me/collection")
        def collection():
            return jsonify({"ok": True})

        @app.get("/api/v2/search")
        def search():
            return jsonify({"ok": True})

        register_api_product_middleware(app)
        self.client = app.test_client()

    def test_user_routes_are_not_catalog_api_key_routes(self):
        for path in (
            "/api/v2/auth/register",
            "/api/v2/auth/login",
            "/api/v2/auth/me",
            "/api/v2/auth/logout",
            "/api/v2/me/collection",
            "/api/v2/me/wishlist",
        ):
            self.assertTrue(_is_user_session_route(path), path)
            self.assertIsNone(_required_scope(path), path)

    def test_catalog_route_still_requires_catalog_scope(self):
        self.assertFalse(_is_user_session_route("/api/v2/search"))
        self.assertEqual(_required_scope("/api/v2/search"), "read:catalog")

    def test_public_auth_does_not_require_internal_api_key(self):
        response = self.client.post(
            "/api/v2/auth/register",
            json={},
            headers={"X-Forwarded-For": "203.0.113.91"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers.get("X-Plan"), "user")
        self.assertEqual(response.headers.get("X-RateLimit-Limit"), "2")

    def test_user_bearer_is_not_interpreted_as_catalog_api_key(self):
        response = self.client.get(
            "/api/v2/me/collection",
            headers={
                "Authorization": "Bearer user-session-token-not-an-api-key",
                "X-Forwarded-For": "203.0.113.92",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers.get("X-Plan"), "user")

    def test_auth_entry_rate_limit_is_enforced(self):
        headers = {"X-Forwarded-For": "203.0.113.93"}
        self.assertEqual(self.client.post("/api/v2/auth/login", json={}, headers=headers).status_code, 200)
        self.assertEqual(self.client.post("/api/v2/auth/login", json={}, headers=headers).status_code, 200)
        blocked = self.client.post("/api/v2/auth/login", json={}, headers=headers)
        self.assertEqual(blocked.status_code, 429)
        self.assertEqual(blocked.get_json().get("error"), "rate_limited")

    def test_catalog_still_blocks_missing_key_when_public_disabled(self):
        response = self.client.get(
            "/api/v2/search",
            headers={"X-Forwarded-For": "203.0.113.94"},
        )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.get_json().get("error"), "missing_api_key")


if __name__ == "__main__":
    unittest.main()
