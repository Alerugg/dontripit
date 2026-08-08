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
        response, ms = _timed(client, "get", "/api/v2/search?game=pokemon&q=Pikachu&limit=5")
        payload = _require(response)
        items = payload.get("items") or []
        if not items or not any("pikachu" in str(row.get("name") or "").lower() for row in items):
            raise AssertionError("HTTP natural search did not return Pikachu")
        checks.append({"name": "search", "ms": ms, "count": len(items)})

        response, ms = _timed(client, "get", "/api/v2/search/suggest?game=pokemon&q=Chariz&limit=5")
        payload = _require(response)
        suggestions = payload.get("items") or []
        if not suggestions or not any("charizard" in str(row.get("name") or "").lower() for row in suggestions):
            raise AssertionError("HTTP suggest did not return Charizard")
        checks.append({"name": "suggest", "ms": ms, "count": len(suggestions)})

        response, ms = _timed(client, "get", "/api/v2/games/pokemon/facets")
        payload = _require(response)
        facets = payload.get("facets") or []
        keys = {row.get("key") for row in facets}
        required = {"set", "types", "stage", "hp", "rarity", "regulation_mark", "finish", "stamp", "dex_id"}
        if len(facets) != 23 or not required.issubset(keys):
            raise AssertionError(f"HTTP facets contract mismatch: count={len(facets)} missing={sorted(required - keys)}")
        checks.append({"name": "facets", "ms": ms, "count": len(facets)})

        for key, expected in (("types", "fire"), ("rarity", "special illustration rare"), ("finish", "holo"), ("stamp", "set-logo")):
            response, ms = _timed(client, "get", f"/api/v2/games/pokemon/facets/{key}/values?limit=100")
            payload = _require(response)
            values = payload.get("items") or []
            normalized = {str(row.get("value") or "").lower() for row in values}
            if expected not in normalized:
                raise AssertionError(f"HTTP facet values {key} missing {expected}")
            checks.append({"name": f"facet_values_{key}", "ms": ms, "count": len(values)})

        response, ms = _timed(
            client,
            "post",
            "/api/v2/search/advanced",
            json={"game": "pokemon", "filters": {"finish": ["holo"]}, "limit": 5, "offset": 0},
        )
        payload = _require(response)
        items = payload.get("items") or []
        if int(payload.get("total") or 0) <= 0 or not items:
            raise AssertionError("HTTP advanced holo returned zero")
        if not all(str((row.get("attributes") or {}).get("finish") or "").lower() == "holo" for row in items):
            raise AssertionError("HTTP advanced holo returned a non-holo print")
        checks.append({"name": "advanced_holo", "ms": ms, "total": int(payload.get("total") or 0)})

        response, ms = _timed(
            client,
            "post",
            "/api/v2/search/advanced",
            json={"game": "pokemon", "q": "Pikachu", "filters": {"dex_id": {"min": 25, "max": 25}}, "limit": 5},
        )
        payload = _require(response)
        items = payload.get("items") or []
        if not items or not any("pikachu" in str(row.get("name") or "").lower() for row in items):
            raise AssertionError("HTTP advanced Pokédex #25 did not return Pikachu")
        checks.append({"name": "advanced_dex25", "ms": ms, "total": int(payload.get("total") or 0)})

        response = client.post(
            "/api/v2/search/advanced",
            json={"game": "pokemon", "filters": {"invented_filter": ["x"]}},
        )
        payload = _require(response, 400)
        if payload.get("error") != "invalid_filters":
            raise AssertionError(f"Unsupported filter did not return invalid_filters: {payload}")
        checks.append({"name": "invalid_filter_rejected", "status": 400})

    report = {
        "status": "pass",
        "game": "pokemon",
        "checks": checks,
        "max_http_ms": max(float(row.get("ms") or 0) for row in checks),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
