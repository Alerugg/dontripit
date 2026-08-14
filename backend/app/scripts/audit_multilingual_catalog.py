from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import psycopg2
from psycopg2.extras import RealDictCursor


ACTIVE_GAMES = ("pokemon", "onepiece", "mtg", "yugioh")
TARGET_LANGUAGES = ("en", "es", "ja")
OUTPUT_JSON = Path("/tmp/multilingual-catalog-audit.json")
OUTPUT_MD = Path("/tmp/multilingual-catalog-audit.md")

CANONICAL_LANGUAGE_SQL = """
CASE
  WHEN lower(trim(coalesce(p.language, ''))) IN ('en', 'eng', 'english')
       OR lower(trim(coalesce(p.language, ''))) LIKE 'en[_-]%' THEN 'en'
  WHEN lower(trim(coalesce(p.language, ''))) IN ('es', 'spa', 'spanish', 'español')
       OR lower(trim(coalesce(p.language, ''))) LIKE 'es[_-]%' THEN 'es'
  WHEN lower(trim(coalesce(p.language, ''))) IN ('ja', 'jp', 'jpn', 'japanese', '日本語')
       OR lower(trim(coalesce(p.language, ''))) LIKE 'ja[_-]%'
       OR lower(trim(coalesce(p.language, ''))) LIKE 'jp[_-]%' THEN 'ja'
  WHEN nullif(trim(coalesce(p.language, '')), '') IS NULL THEN 'unknown'
  ELSE lower(trim(p.language))
END
""".strip()


def _fetch_all(cur, sql: str, params=None) -> list[dict]:
    cur.execute(sql, params or ())
    return [dict(row) for row in cur.fetchall()]


def _to_ints(rows: list[dict]) -> list[dict]:
    out: list[dict] = []
    for row in rows:
        clean: dict = {}
        for key, value in row.items():
            if isinstance(value, bool) or value is None:
                clean[key] = value
            elif isinstance(value, int):
                clean[key] = int(value)
            else:
                clean[key] = value
        out.append(clean)
    return out


def run() -> dict:
    database_url = os.getenv("DATABASE_URL_UNPOOLED") or os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL_UNPOOLED or DATABASE_URL is required")

    conn = psycopg2.connect(
        database_url,
        connect_timeout=20,
        application_name="dontripit_multilingual_readonly_audit",
    )
    # Hard safety boundary: every transaction opened by this connection is read-only.
    conn.set_session(readonly=True, autocommit=False)

    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SHOW transaction_read_only")
            transaction_read_only = str(cur.fetchone()["transaction_read_only"]).lower()
            if transaction_read_only != "on":
                raise RuntimeError(f"Read-only guard failed: transaction_read_only={transaction_read_only!r}")

            cur.execute("SELECT current_database() AS database_name, current_user AS database_user")
            db_identity = dict(cur.fetchone())

            raw_languages = _fetch_all(
                cur,
                """
                SELECT
                  g.slug AS game,
                  COALESCE(NULLIF(trim(p.language), ''), 'unknown') AS raw_language,
                  COUNT(*) AS prints
                FROM prints p
                JOIN cards c ON c.id = p.card_id
                JOIN games g ON g.id = c.game_id
                GROUP BY g.slug, COALESCE(NULLIF(trim(p.language), ''), 'unknown')
                ORDER BY g.slug, prints DESC, raw_language
                """,
            )

            coverage = _fetch_all(
                cur,
                f"""
                WITH image_stats AS (
                  SELECT
                    print_id,
                    COUNT(*) AS image_rows,
                    BOOL_OR(COALESCE(is_primary, false)) AS has_primary_image
                  FROM print_images
                  GROUP BY print_id
                ),
                externally_priced_products AS (
                  SELECT DISTINCT external_product_id
                  FROM external_market_price_snapshots
                ),
                accepted_cardmarket_links AS (
                  SELECT
                    l.print_id,
                    COUNT(DISTINCT e.id) AS mapped_products,
                    BOOL_OR(epp.external_product_id IS NOT NULL) AS has_external_market_price
                  FROM external_catalog_print_links l
                  JOIN external_catalog_products e
                    ON e.id = l.external_product_id
                   AND lower(e.source) = 'cardmarket'
                   AND e.product_group = 'single'
                  LEFT JOIN externally_priced_products epp
                    ON epp.external_product_id = e.id
                  WHERE lower(COALESCE(l.link_status, '')) IN ('accepted', 'mapped', 'exact')
                  GROUP BY l.print_id
                ),
                projected_cardmarket_prices AS (
                  SELECT DISTINCT ps.entity_id AS print_id
                  FROM price_snapshots ps
                  JOIN price_sources src ON src.id = ps.source_id
                  WHERE lower(src.name) = 'cardmarket'
                    AND ps.entity_type = 'print'
                    AND ps.currency = 'EUR'
                ),
                base AS (
                  SELECT
                    g.slug AS game,
                    p.id AS print_id,
                    COALESCE(NULLIF(trim(p.language), ''), 'unknown') AS raw_language,
                    {CANONICAL_LANGUAGE_SQL} AS canonical_language,
                    COALESCE(img.image_rows, 0) AS image_rows,
                    COALESCE(img.has_primary_image, false) AS has_primary_image,
                    COALESCE(cm.mapped_products, 0) AS mapped_products,
                    COALESCE(cm.has_external_market_price, false) AS has_external_market_price,
                    (proj.print_id IS NOT NULL) AS has_projected_cardmarket_price
                  FROM prints p
                  JOIN cards c ON c.id = p.card_id
                  JOIN games g ON g.id = c.game_id
                  LEFT JOIN image_stats img ON img.print_id = p.id
                  LEFT JOIN accepted_cardmarket_links cm ON cm.print_id = p.id
                  LEFT JOIN projected_cardmarket_prices proj ON proj.print_id = p.id
                )
                SELECT
                  game,
                  canonical_language AS language,
                  COUNT(*) AS prints,
                  COUNT(*) FILTER (WHERE image_rows > 0) AS prints_with_image,
                  COUNT(*) FILTER (WHERE has_primary_image) AS prints_with_primary_image,
                  SUM(image_rows) AS image_rows,
                  COUNT(*) FILTER (WHERE mapped_products > 0) AS cardmarket_mapped,
                  COUNT(*) FILTER (WHERE mapped_products = 1) AS cardmarket_exactly_one,
                  COUNT(*) FILTER (WHERE mapped_products > 1) AS cardmarket_ambiguous,
                  COUNT(*) FILTER (WHERE has_external_market_price) AS external_market_priced,
                  COUNT(*) FILTER (WHERE has_projected_cardmarket_price) AS projected_cardmarket_priced
                FROM base
                GROUP BY game, canonical_language
                ORDER BY game, canonical_language
                """,
            )

            image_sources = _fetch_all(
                cur,
                f"""
                SELECT
                  g.slug AS game,
                  {CANONICAL_LANGUAGE_SQL} AS language,
                  COALESCE(NULLIF(trim(pi.source), ''), 'unknown') AS image_source,
                  COUNT(*) AS image_rows,
                  COUNT(DISTINCT pi.print_id) AS prints
                FROM print_images pi
                JOIN prints p ON p.id = pi.print_id
                JOIN cards c ON c.id = p.card_id
                JOIN games g ON g.id = c.game_id
                GROUP BY g.slug, {CANONICAL_LANGUAGE_SQL}, COALESCE(NULLIF(trim(pi.source), ''), 'unknown')
                ORDER BY g.slug, language, image_rows DESC, image_source
                """,
            )

            pokemon_tcgdex = _fetch_all(
                cur,
                f"""
                SELECT
                  {CANONICAL_LANGUAGE_SQL} AS language,
                  COUNT(*) AS prints,
                  COUNT(*) FILTER (WHERE NULLIF(trim(p.tcgdex_id), '') IS NOT NULL) AS prints_with_tcgdex_id
                FROM prints p
                JOIN cards c ON c.id = p.card_id
                JOIN games g ON g.id = c.game_id
                WHERE g.slug = 'pokemon'
                GROUP BY {CANONICAL_LANGUAGE_SQL}
                ORDER BY language
                """,
            )

            samples_missing_images = _fetch_all(
                cur,
                f"""
                SELECT g.slug AS game, {CANONICAL_LANGUAGE_SQL} AS language,
                       p.id AS print_id, s.code AS set_code, p.collector_number, c.name
                FROM prints p
                JOIN cards c ON c.id = p.card_id
                JOIN games g ON g.id = c.game_id
                JOIN sets s ON s.id = p.set_id
                LEFT JOIN print_images pi ON pi.print_id = p.id
                WHERE pi.print_id IS NULL
                  AND g.slug = ANY(%s)
                  AND ({CANONICAL_LANGUAGE_SQL}) = ANY(%s)
                ORDER BY g.slug, language, s.code, p.collector_number, p.id
                LIMIT 60
                """,
                (list(ACTIVE_GAMES), list(TARGET_LANGUAGES)),
            )

            samples_unmapped = _fetch_all(
                cur,
                f"""
                WITH accepted AS (
                  SELECT DISTINCT l.print_id
                  FROM external_catalog_print_links l
                  JOIN external_catalog_products e
                    ON e.id = l.external_product_id
                   AND lower(e.source) = 'cardmarket'
                   AND e.product_group = 'single'
                  WHERE lower(COALESCE(l.link_status, '')) IN ('accepted', 'mapped', 'exact')
                )
                SELECT g.slug AS game, {CANONICAL_LANGUAGE_SQL} AS language,
                       p.id AS print_id, s.code AS set_code, p.collector_number, c.name
                FROM prints p
                JOIN cards c ON c.id = p.card_id
                JOIN games g ON g.id = c.game_id
                JOIN sets s ON s.id = p.set_id
                LEFT JOIN accepted a ON a.print_id = p.id
                WHERE a.print_id IS NULL
                  AND g.slug = ANY(%s)
                  AND ({CANONICAL_LANGUAGE_SQL}) = ANY(%s)
                ORDER BY g.slug, language, s.code, p.collector_number, p.id
                LIMIT 60
                """,
                (list(ACTIVE_GAMES), list(TARGET_LANGUAGES)),
            )

            report = {
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "mode": "strict-read-only",
                "transaction_read_only": transaction_read_only,
                "database_identity": db_identity,
                "scope": {
                    "active_games": list(ACTIVE_GAMES),
                    "target_languages": list(TARGET_LANGUAGES),
                    "personal_data_tables_queried": False,
                },
                "raw_language_distribution": _to_ints(raw_languages),
                "coverage": _to_ints(coverage),
                "image_source_distribution": _to_ints(image_sources),
                "pokemon_tcgdex_language_distribution": _to_ints(pokemon_tcgdex),
                "samples_missing_images": _to_ints(samples_missing_images),
                "samples_without_accepted_cardmarket_link": _to_ints(samples_unmapped),
            }

            conn.rollback()
            return report
    finally:
        conn.close()


def _pct(numerator: int, denominator: int) -> str:
    if not denominator:
        return "0.00%"
    return f"{(100.0 * numerator / denominator):.2f}%"


def render_markdown(report: dict) -> str:
    lines = [
        "# Don’tRipIt multilingual catalog audit",
        "",
        f"Generated: `{report['generated_at']}`",
        "",
        f"Database transaction mode: **{report['transaction_read_only']}** (strict read-only)",
        "",
        "## EN / ES / JA physical coverage",
        "",
        "| Game | Lang | Prints | Images | Primary | Cardmarket | External price | Projected price | Ambiguous |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    rows = [
        row for row in report["coverage"]
        if row["game"] in ACTIVE_GAMES and row["language"] in TARGET_LANGUAGES
    ]
    for row in rows:
        prints = int(row["prints"] or 0)
        lines.append(
            "| {game} | {lang} | {prints} | {img} ({img_pct}) | {primary} | {mapped} ({map_pct}) | "
            "{external} ({ext_pct}) | {projected} ({proj_pct}) | {ambiguous} |".format(
                game=row["game"],
                lang=row["language"],
                prints=prints,
                img=row["prints_with_image"],
                img_pct=_pct(int(row["prints_with_image"] or 0), prints),
                primary=row["prints_with_primary_image"],
                mapped=row["cardmarket_mapped"],
                map_pct=_pct(int(row["cardmarket_mapped"] or 0), prints),
                external=row["external_market_priced"],
                ext_pct=_pct(int(row["external_market_priced"] or 0), prints),
                projected=row["projected_cardmarket_priced"],
                proj_pct=_pct(int(row["projected_cardmarket_priced"] or 0), prints),
                ambiguous=row["cardmarket_ambiguous"],
            )
        )

    lines.extend(["", "## Raw language values", "", "| Game | Raw language | Prints |", "|---|---|---:|"])
    for row in report["raw_language_distribution"]:
        lines.append(f"| {row['game']} | {row['raw_language']} | {row['prints']} |")

    lines.extend(["", "## Pokémon / TCGdex identity distribution", "", "| Lang | Prints | With TCGdex ID |", "|---|---:|---:|"])
    for row in report["pokemon_tcgdex_language_distribution"]:
        lines.append(f"| {row['language']} | {row['prints']} | {row['prints_with_tcgdex_id']} |")

    lines.extend([
        "",
        "## Safety proof",
        "",
        "- The DB connection sets every transaction to read-only before the first catalog query.",
        "- The audit executes catalog/market SELECTs only; no account, auth, email or other personal-data table is queried.",
        "- The transaction is rolled back before the connection closes.",
        "",
    ])
    return "\n".join(lines)


def main() -> int:
    report = run()
    OUTPUT_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    markdown = render_markdown(report)
    OUTPUT_MD.write_text(markdown + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
