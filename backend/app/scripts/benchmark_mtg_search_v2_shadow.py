from __future__ import annotations

import argparse
import json
import statistics
import time
from pathlib import Path

from sqlalchemy import text

from app import db
from app.search_v2.mtg_advanced import advanced_mtg_search
from app.search_v2.mtg_facet_values import mtg_facet_values
from app.search_v2.query import normal_search


NATURAL_CASES = [
    ("Black Lotus", "black lotus"),
    ("Lightning Bolt", "lightning bolt"),
    ("Sol Ring", "sol ring"),
    ("Counterspell", "counterspell"),
    ("Llanowar Elves", "llanowar elves"),
    ("Omniscience", "omniscience"),
]


def _timed(fn, repeats: int = 3):
    timings = []
    value = None
    for _ in range(repeats):
        start = time.perf_counter()
        value = fn()
        timings.append((time.perf_counter() - start) * 1000.0)
    return value, {
        "min_ms": round(min(timings), 2),
        "median_ms": round(statistics.median(timings), 2),
        "max_ms": round(max(timings), 2),
    }


def run(output: Path) -> dict:
    db.init_engine()
    report = {"status": "running", "natural": [], "advanced": [], "facets": []}
    blockers = []
    with db.SessionLocal() as session:
        session.execute(text("SET statement_timeout='10s'"))

        for query, expected_name in NATURAL_CASES:
            rows, timing = _timed(lambda q=query: normal_search(session, query=q, game_slug="mtg", limit=12))
            first = rows[0] if rows else None
            first_name = str((first or {}).get("name") or "").strip().lower()
            ok = bool(first) and first_name == expected_name
            report["natural"].append({
                "query": query,
                "expected_first": expected_name,
                "first": (first or {}).get("name"),
                "count": len(rows),
                **timing,
                "pass": ok,
            })
            if not ok:
                blockers.append(f"natural:{query}:{(first or {}).get('name')!r}")

        advanced_cases = [
            ("foil", {"finish":"foil"}),
            ("blue_creature", {"color_identity":"U","card_type":"Creature"}),
            ("low_mana", {"mana_value":{"min":0,"max":2}}),
            ("promo", {"promo":True}),
            ("artist", {"artist":"Mark Poole"}),
            ("set_finish", {"set":"lea","finish":"nonfoil"}),
        ]
        for name, filters in advanced_cases:
            result, timing = _timed(lambda f=filters: advanced_mtg_search(session, filters=f, limit=20, offset=0))
            ok = result["total"] > 0 and len(result["items"]) > 0
            report["advanced"].append({"case":name,"filters":filters,"total":result["total"],"returned":result["count"],**timing,"pass":ok})
            if not ok:
                blockers.append(f"advanced:{name}:empty")

        facet_cases = [("set","mh2"),("finish",""),("color_identity",""),("card_type","crea"),("artist","poole")]
        for key, query in facet_cases:
            rows, timing = _timed(lambda k=key,q=query: mtg_facet_values(session, key=k, query=q, limit=20))
            ok = len(rows) > 0
            report["facets"].append({"key":key,"query":query,"count":len(rows),"first":rows[0] if rows else None,**timing,"pass":ok})
            if not ok:
                blockers.append(f"facet:{key}:empty")

        game_id = int(session.execute(text("SELECT id FROM games WHERE slug='mtg'")).scalar_one())
        counts = {
            "card_search_profiles": int(session.execute(text("SELECT COUNT(*) FROM card_search_profiles WHERE game_id=:g"), {"g":game_id}).scalar_one()),
            "print_search_profiles": int(session.execute(text("SELECT COUNT(*) FROM print_search_profiles WHERE game_id=:g"), {"g":game_id}).scalar_one()),
            "facets": int(session.execute(text("SELECT COUNT(*) FROM facet_definitions WHERE game_id=:g AND active=true"), {"g":game_id}).scalar_one()),
        }
        if counts != {"card_search_profiles":37624,"print_search_profiles":161275,"facets":21}:
            blockers.append(f"counts:{counts}")

        session.rollback()

    natural_max = max((row["max_ms"] for row in report["natural"]), default=0)
    advanced_max = max((row["max_ms"] for row in report["advanced"]), default=0)
    facet_max = max((row["max_ms"] for row in report["facets"]), default=0)
    # Shadow runners are intentionally slower/noisier than Neon. These gates
    # catch pathological plans without forcing expensive production tuning.
    if natural_max > 3000:
        blockers.append(f"natural_latency_max_ms:{natural_max}>3000")
    if advanced_max > 3000:
        blockers.append(f"advanced_latency_max_ms:{advanced_max}>3000")
    if facet_max > 3000:
        blockers.append(f"facet_latency_max_ms:{facet_max}>3000")

    report.update({
        "status":"pass" if not blockers else "blocked",
        "counts":counts,
        "latency_gates":{"natural_max_ms":natural_max,"advanced_max_ms":advanced_max,"facet_max_ms":facet_max},
        "blockers":blockers,
        "database_writes_to_neon":0,
    })
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    print(json.dumps(report,indent=2,sort_keys=True))
    if blockers:
        raise SystemExit("MTG Search V2 shadow benchmark BLOCKED")
    return report


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--output",type=Path,required=True)
    args=parser.parse_args()
    run(args.output)


if __name__ == "__main__":
    main()
