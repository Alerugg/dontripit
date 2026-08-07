from __future__ import annotations

import json
from datetime import datetime, timezone

from app import db
from app.search_v2.advanced import advanced_onepiece_search
from app.search_v2.query import facet_definitions, normal_search


def _compact(rows: list[dict], limit: int = 5) -> list[dict]:
    result = []
    for row in rows[:limit]:
        matched = row.get("matched_print") or {}
        result.append(
            {
                "name": row.get("name"),
                "card_key": row.get("card_key"),
                "collector_number": matched.get("collector_number"),
                "set_code": matched.get("set_code"),
                "variant": matched.get("exact_variant"),
                "score": row.get("score"),
            }
        )
    return result


def _contains_name(rows: list[dict], needle: str, *, first_n: int = 10) -> bool:
    needle = needle.lower()
    return any(needle in str(row.get("name") or "").lower() for row in rows[:first_n])


def run() -> dict:
    db.init_engine()
    report: dict = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "database": "neon",
        "game": "onepiece",
        "normal": {},
        "advanced": {},
        "facets": {},
    }

    with db.SessionLocal() as session:
        cases = [
            ("Luffy", "luffy"),
            ("Zoro", "zoro"),
            ("OP05-119", "luffy"),
            ("Luffy OP05", "luffy"),
            ("monky lufi", "luffy"),
        ]
        for query, expected_name in cases:
            rows = normal_search(session, query=query, game_slug="onepiece", limit=20)
            report["normal"][query] = {
                "count": len(rows),
                "top": _compact(rows),
            }
            if not rows:
                raise AssertionError(f"Search returned zero results: {query}")
            if not _contains_name(rows, expected_name):
                raise AssertionError(
                    f"Expected {expected_name!r} within first 10 results for {query!r}: {_compact(rows, 10)}"
                )

        exact_rows = normal_search(session, query="OP05-119", game_slug="onepiece", limit=10)
        if not any(row.get("card_key") == "onepiece:op05-119" for row in exact_rows[:3]):
            raise AssertionError(f"Exact collector OP05-119 did not rank in top 3: {_compact(exact_rows, 10)}")

        compound_rows = normal_search(session, query="Luffy OP05", game_slug="onepiece", limit=20)
        if not any(
            "luffy" in str(row.get("name") or "").lower()
            and (row.get("matched_print") or {}).get("set_code") == "op-05"
            for row in compound_rows[:10]
        ):
            raise AssertionError(f"Compound Luffy OP05 did not surface an OP-05 Luffy: {_compact(compound_rows, 10)}")

        exact_parallel = advanced_onepiece_search(
            session,
            filters={"collector_number": "OP05-119", "exact_variant": "p1"},
            limit=20,
        )
        report["advanced"]["OP05-119 p1"] = {
            "total": exact_parallel["total"],
            "top": exact_parallel["items"][:3],
        }
        if not exact_parallel["items"]:
            raise AssertionError("Advanced exact collector + p1 returned zero rows")
        first = exact_parallel["items"][0]
        if first["collector_number"] != "OP05-119" or first["exact_variant"] != "p1":
            raise AssertionError(f"Advanced exact print mismatch: {first}")

        purple_power = advanced_onepiece_search(
            session,
            filters={"color": "Purple", "power": {"min": 10000}},
            limit=10,
        )
        report["advanced"]["Purple power>=10000"] = {
            "total": purple_power["total"],
            "top": purple_power["items"][:3],
        }
        if purple_power["total"] <= 0:
            raise AssertionError("Advanced Purple + power>=10000 returned zero rows")
        for item in purple_power["items"]:
            attrs = item.get("attributes") or {}
            if "Purple" not in (attrs.get("color") or []) or int(attrs.get("power") or 0) < 10000:
                raise AssertionError(f"Advanced filter leaked a nonmatching row: {item}")

        facets = facet_definitions(session, game_slug="onepiece")
        facet_keys = [row["key"] for row in facets]
        report["facets"] = {"active_count": len(facets), "keys": facet_keys}
        if len(facets) < 19:
            raise AssertionError(f"Too few active facets: {facet_keys}")
        if "manga" in facet_keys or "illustration_type" in facet_keys:
            raise AssertionError(f"Unclassified facets must remain hidden: {facet_keys}")

    report["status"] = "pass"
    return report


def main() -> int:
    payload = run()
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
