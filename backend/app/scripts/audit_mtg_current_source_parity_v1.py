from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from pathlib import Path

import psycopg2


def _db_url() -> str:
    url = os.getenv("DATABASE_URL_UNPOOLED") or os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError("No production database URL configured")
    if url.startswith("postgresql+psycopg2://"):
        return "postgresql://" + url[len("postgresql+psycopg2://"):]
    if url.startswith("postgres://"):
        return "postgresql://" + url[len("postgres://"):]
    return url


def _jsonl(path: Path):
    with path.open("r", encoding="utf-8") as handle:
        for line_number, raw in enumerate(handle, start=1):
            line = raw.strip()
            if not line:
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise AssertionError(f"{path.name}:{line_number} is not an object")
            yield value


def _sample(values, limit: int = 50):
    return sorted(values)[:limit]


def run(*, snapshot_dir: Path, output: Path) -> dict:
    manifest = json.loads((snapshot_dir / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("status") != "pass":
        raise AssertionError("Current Scryfall snapshot did not pass its source-integrity gates")
    if manifest.get("snapshot_schema_version") != "mtg-canonical-v2.2":
        raise AssertionError(f"Unexpected MTG snapshot schema: {manifest.get('snapshot_schema_version')!r}")

    source_sets = {str(row["code"]): row for row in _jsonl(snapshot_dir / "sets.jsonl")}
    source_cards = {str(row["card_key"]): row for row in _jsonl(snapshot_dir / "cards.jsonl")}
    source_prints = {str(row["print_key"]): row for row in _jsonl(snapshot_dir / "prints.jsonl")}

    if len(source_sets) != int((manifest.get("counts") or {}).get("sets") or -1):
        raise AssertionError("Source Set manifest count mismatch")
    if len(source_cards) != int((manifest.get("counts") or {}).get("logical_cards") or -1):
        raise AssertionError("Source Card manifest count mismatch")
    if len(source_prints) != int((manifest.get("counts") or {}).get("exact_prints") or -1):
        raise AssertionError("Source Print manifest count mismatch")

    conn = psycopg2.connect(_db_url())
    try:
        conn.set_session(readonly=True, autocommit=False)
        with conn.cursor() as cur:
            cur.execute("SET TRANSACTION READ ONLY")
            cur.execute("SELECT id FROM games WHERE slug='mtg'")
            game_row = cur.fetchone()
            if game_row is None:
                raise AssertionError("Production MTG game row missing")
            game_id = int(game_row[0])

            cur.execute("SELECT code,name,release_date FROM sets WHERE game_id=%s", (game_id,))
            prod_sets = {
                str(code): {"name": str(name), "release_date": release_date.isoformat() if release_date else None}
                for code, name, release_date in cur.fetchall()
            }

            cur.execute("SELECT card_key,name,oracle_id,id FROM cards WHERE game_id=%s", (game_id,))
            prod_cards = {}
            legacy_card_rows = 0
            for card_key, name, oracle_id, card_id in cur.fetchall():
                if card_key:
                    prod_cards[str(card_key)] = {
                        "name": str(name),
                        "oracle_id": str(oracle_id) if oracle_id else None,
                        "id": int(card_id),
                    }
                else:
                    legacy_card_rows += 1

            cur.execute(
                """
                SELECT p.print_key,p.collector_number,p.language,p.rarity,p.is_foil,p.variant,
                       p.scryfall_id,s.code,c.card_key,p.id
                FROM prints p
                JOIN cards c ON c.id=p.card_id
                JOIN sets s ON s.id=p.set_id
                WHERE c.game_id=%s
                """,
                (game_id,),
            )
            prod_prints = {}
            legacy_print_rows = 0
            production_variant_counts = Counter()
            for (
                print_key,
                collector_number,
                language,
                rarity,
                is_foil,
                variant,
                scryfall_id,
                set_code,
                card_key,
                print_id,
            ) in cur.fetchall():
                production_variant_counts[str(variant or "<null>")] += 1
                if print_key and str(print_key).startswith("mtg:scryfall:"):
                    prod_prints[str(print_key)] = {
                        "collector_number": str(collector_number),
                        "language": str(language) if language else None,
                        "rarity": str(rarity) if rarity else None,
                        "is_foil": bool(is_foil),
                        "variant": str(variant),
                        "scryfall_id": str(scryfall_id) if scryfall_id else None,
                        "set_code": str(set_code),
                        "card_key": str(card_key) if card_key else None,
                        "id": int(print_id),
                    }
                else:
                    legacy_print_rows += 1

            economics = {}
            cur.execute("SELECT COUNT(*) FROM prices WHERE game_id=%s", (game_id,))
            economics["prices"] = int(cur.fetchone()[0])
            cur.execute("SELECT to_regclass('public.price_snapshots') IS NOT NULL")
            if bool(cur.fetchone()[0]):
                cur.execute(
                    """
                    SELECT COUNT(*) FROM price_snapshots ps
                    WHERE (ps.entity_type='card' AND ps.entity_id IN (SELECT id FROM cards WHERE game_id=%s))
                       OR (ps.entity_type='print' AND ps.entity_id IN (
                            SELECT p.id FROM prints p JOIN cards c ON c.id=p.card_id WHERE c.game_id=%s
                       ))
                    """,
                    (game_id, game_id),
                )
                economics["price_snapshots"] = int(cur.fetchone()[0])
            cur.execute("SELECT COUNT(*) FROM products WHERE game_id=%s", (game_id,))
            economics["products"] = int(cur.fetchone()[0])

        conn.rollback()
    finally:
        conn.close()

    source_set_keys = set(source_sets)
    source_card_keys = set(source_cards)
    source_print_keys = set(source_prints)
    prod_set_keys = set(prod_sets)
    prod_card_keys = set(prod_cards)
    prod_print_keys = set(prod_prints)

    missing_sets = source_set_keys - prod_set_keys
    extra_sets = prod_set_keys - source_set_keys
    missing_cards = source_card_keys - prod_card_keys
    extra_cards = prod_card_keys - source_card_keys
    missing_prints = source_print_keys - prod_print_keys
    extra_prints = prod_print_keys - source_print_keys

    set_field_mismatches = []
    for key in sorted(source_set_keys & prod_set_keys):
        src = source_sets[key]
        prod = prod_sets[key]
        expected = {
            "name": str(src.get("name") or ""),
            "release_date": src.get("release_date"),
        }
        if expected != {"name": prod["name"], "release_date": prod["release_date"]}:
            set_field_mismatches.append({"code": key, "source": expected, "production": prod})

    card_field_mismatches = []
    for key in sorted(source_card_keys & prod_card_keys):
        src = source_cards[key]
        prod = prod_cards[key]
        expected_name = str(src.get("name") or "")
        expected_oracle = str(src.get("oracle_id")) if src.get("oracle_id") else None
        if prod["name"] != expected_name or prod["oracle_id"] != expected_oracle:
            card_field_mismatches.append(
                {
                    "card_key": key,
                    "source": {"name": expected_name, "oracle_id": expected_oracle},
                    "production": {"name": prod["name"], "oracle_id": prod["oracle_id"]},
                }
            )

    print_field_mismatches = []
    for key in sorted(source_print_keys & prod_print_keys):
        src = source_prints[key]
        prod = prod_prints[key]
        expected = {
            "collector_number": str(src.get("collector_number") or ""),
            "language": str(src.get("language")) if src.get("language") else None,
            "rarity": str(src.get("rarity")) if src.get("rarity") else None,
            "is_foil": bool(src.get("is_foil")),
            "variant": str(src.get("variant") or ""),
            "scryfall_id": str(src.get("scryfall_id")) if src.get("scryfall_id") else None,
            "set_code": str(src.get("set_code") or ""),
            "card_key": str(src.get("card_key")) if src.get("card_key") else None,
        }
        actual = {key_: prod[key_] for key_ in expected}
        if expected != actual:
            print_field_mismatches.append({"print_key": key, "source": expected, "production": actual})

    source_finish_counts = {
        str(key): int(value)
        for key, value in sorted((manifest.get("finish_counts") or {}).items())
    }
    exact_production_finish_counts = Counter(
        row["variant"] for row in prod_prints.values()
    )

    parity = {
        "sets": not missing_sets and not extra_sets and not set_field_mismatches,
        "cards": not missing_cards and not extra_cards and not card_field_mismatches,
        "prints": not missing_prints and not extra_prints and not print_field_mismatches,
    }
    parity["all"] = all(parity.values())

    report = {
        "status": "pass",
        "mode": "read-only-current-source-classification",
        "production_writes": 0,
        "snapshot_schema_version": manifest.get("snapshot_schema_version"),
        "identity_policy_version": manifest.get("identity_policy_version"),
        "source": manifest.get("source"),
        "source_counts": {
            "sets": len(source_sets),
            "cards": len(source_cards),
            "prints": len(source_prints),
            "finishes": source_finish_counts,
        },
        "production_counts": {
            "sets": len(prod_sets),
            "exact_v2_cards": len(prod_cards),
            "legacy_cards_without_card_key": legacy_card_rows,
            "exact_v2_prints": len(prod_prints),
            "legacy_or_non_v2_prints": legacy_print_rows,
            "exact_v2_finishes": dict(sorted(exact_production_finish_counts.items())),
            "all_print_variants": dict(sorted(production_variant_counts.items())),
        },
        "parity": parity,
        "gaps": {
            "source_sets_missing_in_production": len(missing_sets),
            "production_sets_absent_from_source": len(extra_sets),
            "source_cards_missing_in_production": len(missing_cards),
            "production_exact_cards_absent_from_source": len(extra_cards),
            "source_prints_missing_in_production": len(missing_prints),
            "production_exact_prints_absent_from_source": len(extra_prints),
            "set_field_mismatches": len(set_field_mismatches),
            "card_field_mismatches": len(card_field_mismatches),
            "print_field_mismatches": len(print_field_mismatches),
        },
        "samples": {
            "source_sets_missing_in_production": _sample(missing_sets),
            "production_sets_absent_from_source": _sample(extra_sets),
            "source_cards_missing_in_production": _sample(missing_cards),
            "production_exact_cards_absent_from_source": _sample(extra_cards),
            "source_prints_missing_in_production": _sample(missing_prints),
            "production_exact_prints_absent_from_source": _sample(extra_prints),
            "set_field_mismatches": set_field_mismatches[:50],
            "card_field_mismatches": card_field_mismatches[:50],
            "print_field_mismatches": print_field_mismatches[:50],
        },
        "economics_baseline_read_only": economics,
        "source_gates": manifest.get("gates"),
    }

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only current Scryfall exact MTG source vs production classifier")
    parser.add_argument("--snapshot-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    run(snapshot_dir=args.snapshot_dir, output=args.output)


if __name__ == "__main__":
    main()
