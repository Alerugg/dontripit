from __future__ import annotations

import json
import time

from app.main import create_app


def _require(response, status: int = 200):
    if response.status_code != status:
        raise AssertionError(
            f"{response.request.path} returned {response.status_code}: {response.get_data(as_text=True)[:1000]}"
        )
    payload = response.get_json(silent=True)
    if payload is None:
        raise AssertionError(f"{response.request.path} did not return JSON")
    return payload


def _timed(client, method: str, path: str, **kwargs):
    started = time.perf_counter()
    response = getattr(client, method)(path, **kwargs)
    elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
    return response, elapsed_ms


def main() -> int:
    app = create_app()
    app.testing = True
    checks = []

    with app.test_client() as client:
        response, ms = _timed(client, "get", "/api/v2/search?game=yugioh&q=Dark%20Magician&limit=5")
        payload = _require(response)
        items = payload.get("items") or []
        if not items or not any(str(row.get("name") or "").casefold() == "dark magician" for row in items):
            raise AssertionError("HTTP natural search did not return Dark Magician")
        checks.append({"name": "search", "ms": ms, "count": len(items)})

        response, ms = _timed(client, "get", "/api/v2/search/suggest?game=yugioh&q=Blue-Eyes%20White%20Dragon&limit=5")
        payload = _require(response)
        suggestions = payload.get("items") or []
        if not suggestions or not any(str(row.get("name") or "").casefold() == "blue-eyes white dragon" for row in suggestions):
            raise AssertionError("HTTP suggest did not return Blue-Eyes White Dragon")
        checks.append({"name": "suggest", "ms": ms, "count": len(suggestions)})

        response, ms = _timed(client, "get", "/api/v2/games/yugioh/facets")
        payload = _require(response)
        facets = payload.get("facets") or []
        keys = {row.get("key") for row in facets}
        required = {"set", "release", "collector_number", "card_class", "card_type", "attribute", "race", "archetype", "level", "rank", "atk", "def", "link_value", "rarity"}
        if len(facets) != 19 or not required.issubset(keys):
            raise AssertionError(f"HTTP active facets mismatch: count={len(facets)} missing={sorted(required - keys)}")
        checks.append({"name": "facets", "ms": ms, "count": len(facets)})

        for key in ("release", "archetype", "rarity"):
            response, ms = _timed(client, "get", f"/api/v2/games/yugioh/facets/{key}/values?limit=25")
            payload = _require(response)
            values = payload.get("items") or []
            if not values:
                raise AssertionError(f"HTTP facet values {key} returned no values")
            checks.append({"name": f"facet_values_{key}", "ms": ms, "count": len(values)})

        response, ms = _timed(
            client,
            "post",
            "/api/v2/search/advanced",
            json={"game": "yugioh", "filters": {"card_class": "Monster", "attribute": "DARK"}, "limit": 5},
        )
        payload = _require(response)
        items = payload.get("items") or []
        if int(payload.get("total") or 0) <= 0 or not items:
            raise AssertionError("HTTP advanced Monster + DARK returned zero")
        for row in items:
            attrs = row.get("attributes") or {}
            if str(attrs.get("card_class") or "").casefold() != "monster" or str(attrs.get("attribute") or "").upper() != "DARK":
                raise AssertionError("HTTP advanced Monster + DARK returned mismatched evidence")
        checks.append({"name": "advanced_monster_dark", "ms": ms, "total": int(payload.get("total") or 0)})

        response, ms = _timed(
            client,
            "post",
            "/api/v2/search/advanced",
            json={
                "game": "yugioh",
                "q": "Dark Magician",
                "filters": {"card_class": "Monster", "attribute": "DARK"},
                "limit": 10,
            },
        )
        payload = _require(response)
        items = payload.get("items") or []
        if not items:
            raise AssertionError("HTTP advanced Dark Magician + Monster + DARK returned zero")
        for row in items:
            attrs = row.get("attributes") or {}
            if "dark magician" not in str(row.get("name") or "").casefold():
                raise AssertionError(f"Advanced text/facet intersection leaked unrelated card: {row.get('name')}")
            if str(attrs.get("card_class") or "").casefold() != "monster" or str(attrs.get("attribute") or "").upper() != "DARK":
                raise AssertionError("Advanced text/facet intersection returned mismatched gameplay evidence")
        checks.append({"name": "advanced_query_monster_dark_intersection", "ms": ms, "total": int(payload.get("total") or 0)})

        response, ms = _timed(
            client,
            "post",
            "/api/v2/search/advanced",
            json={"game": "yugioh", "filters": {"atk": {"min": 3000}}, "limit": 5},
        )
        payload = _require(response)
        items = payload.get("items") or []
        if not items or any(int((row.get("attributes") or {}).get("atk") or -1) < 3000 for row in items):
            raise AssertionError("HTTP advanced ATK >= 3000 failed")
        checks.append({"name": "advanced_atk", "ms": ms, "total": int(payload.get("total") or 0)})

        response = client.post(
            "/api/v2/search/advanced",
            json={"game": "yugioh", "filters": {"finish": "holo"}},
        )
        payload = _require(response, 400)
        if payload.get("error") != "invalid_filters":
            raise AssertionError(f"Unsupported finish did not return invalid_filters: {payload}")
        checks.append({"name": "unsupported_finish_rejected", "status": 400})

    report = {
        "status": "pass",
        "game": "yugioh",
        "checks": checks,
        "max_http_ms": max(float(row.get("ms") or 0) for row in checks),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
