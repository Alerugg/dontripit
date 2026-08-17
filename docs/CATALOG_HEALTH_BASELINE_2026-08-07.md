# Catalog Health Baseline — 2026-08-07

Generated from the real Neon PostgreSQL database using the read-only `Catalog Health Audit` workflow on branch `catalog-v2`.

This is the V2 baseline. Future catalog and ingestion repairs should be measured against this snapshot.

## Executive summary

Canonical database totals:

- Games: 4
- Sets: 5,770
- Cards: 3,458
- Prints: 8,309
- Images: 8,315
- Games marked healthy: 0
- Games marked warning: 4
- Games marked critical: 0

Important: `warning` is an internal data-quality status, not an external completeness certification.

## Highest-priority findings

1. **One Piece is absent from the canonical Neon catalog.** Connector/frontend code exists in the repository, but there is no `onepiece` game in the audited canonical database.
2. **Pokémon is severely incomplete:** 8 sets and 503 prints only. 502/503 prints have rarity `unknown` and language normalization contains both `en` and `EN`.
3. **Riftbound is effectively empty:** 1 set, 2 cards and 2 prints. The remote connector is failing because `RIFTBOUND_FALLBACK_BASE_URL` is required.
4. **MTG is stale:** the current incremental Scryfall query returns HTTP 400 and no successful canonical refresh has moved print creation beyond 2026-06-04.
5. **Yu-Gi-Oh! needs canonical normalization review:** 5,463 sets for 6,216 prints, every set lacks a release date, and `variant` contains rarity-like and legacy values.
6. `card_key` and `print_key` are largely absent across the existing catalog.
7. The daily ingest has not produced fresh source data since early July and can report overall success even when important connectors fail.
8. Existing canonical prints have very strong image coverage and no potential duplicate print identity groups were detected by the current identity check.

## MTG

Counts:

- Sets: 298
- Cards: 1,183
- Prints: 1,588
- Images: 1,589
- Prints with any image: 1,588
- Prints with primary image: 1,588
- Prints with structured identifier: 1,588
- Prints with direct external identifier: 1,588
- Prints with any external identifier: 1,588

Issues:

- Sets without prints: 0
- Cards without prints: 0
- Sets missing release date: 0
- Cards missing `card_key`: 1,183
- Prints missing language: 0
- Prints missing rarity: 0
- Prints missing `print_key`: 1,588
- Prints without images: 0
- Prints without primary image: 0
- Prints without external identifier: 0
- Potential duplicate print identity groups: 0

Languages:

- en: 1,575
- ja: 7
- es: 4
- fr: 1
- it: 1

Variants:

- default: 1,588

Newest canonical print creation timestamp: 2026-06-04.

Source records for Scryfall: 9,170.

Current blocker:

`requests.exceptions.HTTPError: 400 Client Error: Bad Request for url: https://api.scryfall.com/cards/search?q=game%3Apaper+date%3E%3D2026-06-04&order=released&dir=desc&unique=prints`

## Pokémon

Counts:

- Sets: 8
- Cards: 263
- Prints: 503
- Images: 503
- Prints with any image: 503
- Prints with primary image: 503
- Prints with structured identifier: 503
- Prints with direct external identifier: 502
- Prints with any external identifier: 503

Issues:

- Sets without prints: 0
- Cards without prints: 0
- Sets missing release date: 0
- Cards missing `card_key`: 161
- Prints missing language: 0
- Prints missing rarity: 0 at SQL null/blank level
- Prints missing `print_key`: 503
- Prints without images: 0
- Prints without primary image: 0
- Prints without external identifier: 0
- Potential duplicate print identity groups: 0

Normalization findings:

- rarity `unknown`: 502
- rarity `common`: 1
- language `en`: 502
- language `EN`: 1

Newest canonical print creation timestamp: 2026-03-12.

Source records for TCGdex: 680.

The scheduled ingest historically targeted `base1`, so the current eight-set catalog must not be treated as representative Pokémon coverage.

## Riftbound

Counts:

- Sets: 1
- Cards: 2
- Prints: 2
- Images: 2
- Prints with any image: 2
- Prints with primary image: 2
- Prints with structured identifier: 0
- Prints with direct external identifier: 2
- Prints with any external identifier: 2

Issues:

- Sets without prints: 0
- Cards without prints: 0
- Sets missing release date: 1
- Cards missing `card_key`: 2
- Prints missing language: 0
- Prints missing rarity: 0 at SQL null/blank level
- Prints missing `print_key`: 2
- Prints without images: 0
- Prints without primary image: 0
- Prints without external identifier: 0
- Potential duplicate print identity groups: 0

Normalization findings:

- rarity `unknown`: 2
- language `en`: 2

Newest canonical print creation timestamp: 2026-03-04.

Source records: 2.

Current blocker:

`RuntimeError: RIFTBOUND_FALLBACK_BASE_URL is required for remote fallback mode`

## Yu-Gi-Oh!

Counts:

- Sets: 5,463
- Cards: 2,010
- Prints: 6,216
- Images: 6,221
- Prints with any image: 6,216
- Prints with primary image: 6,216
- Prints with structured identifier: 0
- Prints with direct external identifier: 6,216
- Prints with any external identifier: 6,216

Issues:

- Sets without prints: 0
- Cards without prints: 2
- Sets missing release date: 5,463
- Cards missing `card_key`: 1,801
- Prints missing language: 0
- Prints missing rarity: 0
- Prints missing `print_key`: 5,676
- Prints without images: 0
- Prints without primary image: 0
- Prints without external identifier: 0
- Potential duplicate print identity groups: 0

Language:

- en: 6,216

The `variant` field is currently overloaded. High-frequency values include:

- common: 2,614
- ultra-rare: 792
- super-rare: 747
- rare: 577
- secret-rare: 363
- ultimate-rare: 155
- quarter-century-secret-rare: 142

Suspicious/legacy values also include `default`, `new`, `2`, `3`, `european-oceanian-debut`, `reprint` and `unknown`.

The separate `rarity` field already contains the corresponding rarity labels, so this is a canonical semantic problem rather than simply missing data.

Newest canonical print creation timestamp: 2026-06-23.

Source records for YGOPRODeck: 2,983.

## Ingestion freshness

Latest observed source state:

- fixture_local: 7 records; last run state 2026-03-04
- riftbound: 2 records; source sync state 2026-03-12
- scryfall_mtg: 9,170 records; source sync state 2026-06-04
- tcgdex_pokemon: 680 records; source sync state 2026-07-04
- ygoprodeck_yugioh: 2,983 records; source sync state 2026-07-04

Newest source record globally: 2026-07-03.

Newest ingest run: 2026-07-04.

## Recent connector outcomes

### Pokémon / TCGdex

Latest audited run: success.

- files seen: 102
- skipped: 102
- inserted: 0
- updated: 0

### MTG / Scryfall

Latest audited run: failed.

Reason: HTTP 400 from the incremental Scryfall search query.

### Yu-Gi-Oh! / YGOPRODeck

Latest audited run: success.

- files seen: 200
- skipped: 195
- inserted: 0
- updated: 18

### Riftbound

Latest audited run: failed.

Reason: missing `RIFTBOUND_FALLBACK_BASE_URL` for remote fallback mode.

### One Piece

Not present in the canonical database and not part of the current `daily_refresh.py` pipeline.

## Positive findings

The existing data is not garbage. Several foundations are strong:

- 8,309 canonical prints exist today.
- Image coverage is effectively complete for those prints.
- No potential duplicate print identity groups were found by the current identity dimensions.
- MTG identifiers are particularly clean for the data that has already been canonicalized.
- The database can be audited safely using GitHub Actions and Neon secrets without exposing credentials or mutating production data.

## Repair order

Do not start pricing yet.

Recommended next block:

1. Make daily refresh surface partial failures correctly.
2. Add One Piece to the first-class refresh pipeline.
3. Replace scheduled Pokémon `base1` behavior with a complete/incremental all-set strategy.
4. Repair Scryfall incremental ingestion.
5. Repair Riftbound remote ingestion/configuration.
6. Correct search reindex behavior after incremental mutations.
7. Audit and repair One Piece canonical ingestion.
8. Normalize Pokémon rarity/language.
9. Audit Yu-Gi-Oh! set identity, release dates and misuse of `variant`.
10. Introduce deterministic canonical `card_key` / `print_key` policy where required.
11. Rerun Catalog Health and compare against this baseline.
12. Only after internal health is understood, build External Coverage Certification for each TCG.
