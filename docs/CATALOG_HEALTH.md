# Catalog Health

`Catalog Health` is the read-only quality audit for the canonical TCG catalog.

It exists to answer a different question from normal ingestion monitoring:

> Can we trust the canonical catalog that downstream search, pricing and portfolio features will use?

## Run locally

From `backend/`:

```bash
python -m app.scripts.catalog_health
```

JSON output:

```bash
python -m app.scripts.catalog_health --json
```

The command only executes SELECT queries. It does not insert, update or delete catalog data.

## Current checks by TCG

### Inventory

- sets
- cards
- prints
- print images
- prints with any image
- prints with a primary image
- prints with structured `PrintIdentifier`
- prints with a connector-native external ID
- prints with any external identifier

### Missing or suspicious data

- sets without prints
- cards without prints
- sets missing release date
- cards missing `card_key`
- prints missing language
- prints missing rarity
- prints missing `print_key`
- prints without images
- prints without a primary image
- prints without any external identifier
- potential duplicate print identity groups

The duplicate identity check intentionally normalizes nothing and groups by the current canonical identity dimensions:

- set
- collector number
- language
- foil flag
- variant

This check is especially important when language is `NULL`, because a normal SQL unique constraint can still permit multiple rows containing `NULL` in otherwise identical identities.

### Distributions

The report includes the most common:

- languages
- variants
- rarities

This is useful for spotting connector normalization problems such as `EN`, `en`, `English`, empty strings or inconsistent variant labels.

### Freshness

For each game the audit reports the newest `created_at` timestamp for:

- sets
- cards
- prints

It also embeds ingestion status, recent connector runs and source freshness from `ingest_status.py`.

## Status labels

The current labels are intentionally conservative and are not a completeness score.

- `healthy`: no currently tracked print-level quality issue was detected.
- `warning`: one or more tracked quality issues exist.
- `critical`: the game has zero prints or a potential duplicate canonical print identity was detected.

A `healthy` result does **not** mean the TCG is complete versus the outside world.

## Important limitation: internal health vs external completeness

Version 1 audits what exists in our database.

It cannot yet prove statements such as:

- every official One Piece set ever released is present;
- every Pokémon promo is present;
- every MTG printing from Scryfall is represented;
- every Yu-Gi-Oh! reprint/edition has been captured.

That requires a second layer: **External Coverage Certification**.

For each TCG, an authoritative or best-available source inventory will be compared against our canonical database. That future report should include:

- expected sets vs canonical sets;
- expected source print IDs vs mapped canonical prints;
- missing source entities;
- unmatched source entities;
- ambiguous source-to-print matches;
- image coverage;
- variant coverage;
- language/region coverage when supported by the source.

This separation is deliberate:

1. Internal Catalog Health makes sure our database is structurally trustworthy.
2. External Coverage Certification proves how complete it is relative to real releases.

## CI / Neon workflow

`.github/workflows/catalog-health.yml` contains two jobs:

1. `Validate audit logic` — runs the dedicated SQLite tests.
2. `Audit Neon catalog (read only)` — connects using the repository's existing `DATABASE_URL_UNPOOLED` secret and produces `catalog-health.json`.

The JSON is uploaded as a GitHub Actions artifact for 30 days.

The workflow never runs migrations and never modifies Neon.

## Reliability fix made with Catalog Health

The previous `ingest_status.py` joined `source_records` and `ingest_runs` directly in one aggregate query. For a source with multiple records and multiple runs, that could multiply rows and inflate `source_records_total`.

The aggregation now computes source-record statistics and ingest-run statistics independently before joining them back to `sources`.

A regression test guarantees that two source records plus three ingest runs still report exactly two source records, not six.

## Next step after the first real Neon report

Do not start Catalog Model V2 immediately.

First classify every finding from the initial report into:

- ingestion bug;
- historical data gap;
- normalization problem;
- canonical identity problem;
- expected/acceptable null;
- source limitation.

Then fix the ingestion/catalog defects and rerun Catalog Health until the baseline is understood.
