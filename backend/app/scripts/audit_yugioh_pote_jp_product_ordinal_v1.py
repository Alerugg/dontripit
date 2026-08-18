from __future__ import annotations

import json
import os
from collections import Counter, defaultdict
from pathlib import Path

import psycopg2
from psycopg2.extras import RealDictCursor

ACCEPTED = ("accepted", "mapped", "exact")


def main() -> int:
    url = os.getenv("DATABASE_URL_UNPOOLED") or os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL_UNPOOLED or DATABASE_URL is required")
    conn = psycopg2.connect(url, connect_timeout=30, application_name="dontripit_pote_jp_product_ordinal_v1")
    conn.set_session(readonly=True, autocommit=False)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT id FROM games WHERE slug='yugioh' LIMIT 1")
            game_id = int(cur.fetchone()["id"])
            cur.execute("SELECT max(last_seen_at) capture FROM external_catalog_products WHERE source='cardmarket'")
            capture = cur.fetchone()["capture"]
            cur.execute(
                """SELECT e.id external_product_id,e.external_id id_product,e.name,e.metacard_external_id,
                          l.print_id,l.mapping_method,l.confidence,l.reviewed,
                          p.rarity,p.variant,p.collector_number,p.language,s.code set_code
                   FROM external_catalog_products e
                   LEFT JOIN external_catalog_print_links l
                     ON l.external_product_id=e.id AND l.link_status=ANY(%s)
                   LEFT JOIN prints p ON p.id=l.print_id
                   LEFT JOIN sets s ON s.id=p.set_id
                   WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single'
                     AND e.expansion_external_id='5044' AND e.last_seen_at=%s
                   ORDER BY e.metacard_external_id,e.external_id::bigint""",
                (list(ACCEPTED), game_id, capture),
            )
            rows = [dict(r) for r in cur.fetchall()]
            conn.rollback()
    finally:
        conn.close()

    by_meta = defaultdict(list)
    for row in rows:
        by_meta[str(row.get("metacard_external_id") or "")].append(row)

    groups = []
    fully_mapped_sequences = Counter()
    partial_sequences = Counter()
    residual_group_count = 0
    for meta, items in sorted(by_meta.items(), key=lambda kv: min(int(x["id_product"]) for x in kv[1])):
        items = sorted(items, key=lambda r: int(r["id_product"]))
        mapped = [r for r in items if r.get("print_id") is not None]
        sequence = tuple(str(r.get("rarity") or "UNMAPPED").casefold() if r.get("print_id") else "UNMAPPED" for r in items)
        if len(mapped) == len(items):
            fully_mapped_sequences[sequence] += 1
        else:
            partial_sequences[sequence] += 1
            residual_group_count += 1
        groups.append(
            {
                "idMetacard": meta,
                "name": items[0].get("name"),
                "product_count": len(items),
                "mapped_count": len(mapped),
                "fully_mapped": len(mapped) == len(items),
                "ordinal_sequence": list(sequence),
                "products": [
                    {
                        "ordinal": index + 1,
                        "idProduct": str(r["id_product"]),
                        "print_id": int(r["print_id"]) if r.get("print_id") is not None else None,
                        "rarity": r.get("rarity"),
                        "variant": r.get("variant"),
                        "collector_number": r.get("collector_number"),
                        "mapping_method": r.get("mapping_method"),
                        "confidence": r.get("confidence"),
                        "reviewed": r.get("reviewed"),
                        "language": r.get("language"),
                        "set_code": r.get("set_code"),
                    }
                    for index, r in enumerate(items)
                ],
            }
        )

    report = {
        "status": "pass",
        "production_writes": 0,
        "cardmarket_capture": str(capture),
        "regional_products": len(rows),
        "metacard_groups": len(groups),
        "fully_mapped_groups": sum(1 for g in groups if g["fully_mapped"]),
        "residual_groups": residual_group_count,
        "fully_mapped_ordinal_sequences": {
            " | ".join(k): v for k, v in sorted(fully_mapped_sequences.items(), key=lambda kv: (-kv[1], kv[0]))
        },
        "partial_ordinal_sequences": {
            " | ".join(k): v for k, v in sorted(partial_sequences.items(), key=lambda kv: (-kv[1], kv[0]))
        },
        "groups": groups,
    }
    out = os.getenv("YGO_POTE_JP_PRODUCT_ORDINAL_OUTPUT", "/tmp/yugioh-pote-jp-product-ordinal-v1.json")
    text = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n"
    Path(out).write_text(text, encoding="utf-8")
    print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
