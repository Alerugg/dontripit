# Pokémon Baseline V2 Certification

**Status: CERTIFIED — canonical physical identity baseline**

This certification covers the physical Pokémon Trading Card Game identity layer in `catalog-v2` / Neon. It explicitly excludes **Pokémon TCG Pocket**, which is treated as a separate game surface.

## Canonical source surface

- Physical TCGdex sets: **203**
- Physical TCGdex cards: **20,964**
- Pokémon TCG Pocket sets excluded: **15**
- Pokémon TCG Pocket cards excluded: **2,480**
- Unassigned physical source cards: **0**

## Neon identity coverage

Independent post-bootstrap audit:

- Physical source sets matched by `Set.tcgdex_id`: **203 / 203**
- Missing physical source sets: **0**
- Physical source cards matched: **20,964 / 20,964**
- Missing physical source cards: **0**
- Physical source coverage: **100%**
- Partially covered physical sets: **0**
- Duplicate DB TCGdex IDs: **0**
- Pokémon TCG Pocket IDs inside the physical Pokémon catalog: **0**
- Print/Card TCGdex identity mismatches after bootstrap: **0**

## Bootstrap mutation evidence

The transactional V2 bootstrap committed the following deterministic changes after a zero-conflict preflight:

- Sets inserted: **196**
- Existing sets updated: **7**
- Cards inserted: **20,702**
- Existing Cards updated: **262**
- Baseline Prints inserted: **20,464**
- Existing Prints updated: **500**
- Legacy Prints relinked to their canonical Card: **238**
- TCGdex Print identifiers inserted: **20,464**
- Source image rows inserted: **19,007**

The bootstrap used one database transaction and hard postconditions before commit. A previous attempt that violated the real Neon `prints.rarity NOT NULL` constraint rolled back completely; the successful run uses the literal baseline rarity `unknown` until authoritative rich-source enrichment replaces it.

## Why Neon table totals are slightly larger

After bootstrap the Pokémon tables contained:

- Sets: **204**
- Cards: **20,965**
- Prints: **20,967**

The canonical physical source is still exactly **203 / 20,964 / 20,964**. The extra rows are intentionally preserved legacy data, not counted as current physical source identity.

Known stale TCGdex IDs preserved for later controlled cleanup:

- `sv1-1`
- `sv1-62`

No destructive delete was performed during bootstrap.

## Valid zero-card source sets

TCGdex contains four valid physical Set identities that currently own zero cards in the global `/cards` surface:

- `jumbo`
- `rc`
- `sp`
- `wp`

They are considered **present** when their canonical `Set.tcgdex_id` exists. Set completeness is not inferred from card count.

## Source quality caveats before rich enrichment

The baseline certifies **identity and coverage**, not final Pokémon metadata quality. The next phase must enrich the baseline using a pinned official `tcgdex/cards-database` commit with:

- rarity
- illustrator
- category / card type
- HP
- Pokémon types
- stage / evolution
- regulation mark
- trainer / energy type
- gameplay attributes
- explicit physical variants, stamps and foil patterns

The legacy Pokémon TCGdex daily ingest remains quarantined. It must not mutate this catalog; a V2 refresh pipeline will replace it after rich-source certification.
