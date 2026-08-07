# Pokémon Catalog V2 Identity Certification

**Status: CERTIFIED — canonical English physical identity**

This certification covers the English physical Pokémon Trading Card Game identity layer in `catalog-v2` / Neon. It explicitly excludes **Pokémon TCG Pocket**, which is treated as a separate game surface.

## Final canonical English identity

The current English catalog is **21,065 canonical card identities**.

It is composed of:

- TCGdex REST physical baseline: **20,964**
- Released-English supplements found in the pinned official `tcgdex/cards-database` repository but omitted by REST `/en/cards`: **101**
- Total canonical English identities: **21,065**

The 101 reconciled supplements are:

- `mep`: **29**
- `swshp`: **3**
- `tk-hs-g`: **30**
- `tk-hs-r`: **30**
- `tk-sm-r`: **9**

They passed a zero-collision Card/Print preflight and were inserted transactionally.

## Pinned rich-source reconciliation

Official source repository:

- `tcgdex/cards-database`
- pinned source commit: `771a8381c57c73182b9776657a15cd1166c66d36`

The pinned repository contains **21,159** physical, non-Pocket source rows after canonical Trainer Gallery alias normalization.

Classification against the English catalog:

- Canonical English identities mapped in Neon: **21,065 / 21,065**
- Canonical rich-source coverage: **100%**
- Released-English source identities still pending: **0**
- Regional / no-English-name source rows intentionally outside the English catalog: **94**
- Future unreleased English rows: **0**
- English rows with unknown release date pending review: **0**

Historical Trainer Gallery aliases normalized during source reconciliation:

- `swsh9tg` → `swsh9.5tg`
- `swsh10tg` → `swsh10.5tg`
- `swsh11tg` → `swsh11.5tg`
- `swsh12tg` → `swsh12.5tg`

## REST physical baseline evidence

- Physical TCGdex sets: **203**
- REST physical cards: **20,964**
- Pokémon TCG Pocket sets excluded: **15**
- Pokémon TCG Pocket cards excluded: **2,480**
- Unassigned physical REST source cards: **0**

Independent post-bootstrap audit:

- Physical source sets matched by `Set.tcgdex_id`: **203 / 203**
- Missing physical source sets: **0**
- REST source cards matched: **20,964 / 20,964**
- Missing REST source cards: **0**
- Partially covered physical sets: **0**
- Duplicate DB TCGdex IDs: **0**
- Pokémon TCG Pocket IDs inside the physical Pokémon catalog: **0**
- Print/Card TCGdex identity mismatches after bootstrap: **0**

## Bootstrap mutation evidence

The transactional REST-baseline bootstrap committed the following deterministic changes after a zero-conflict preflight:

- Sets inserted: **196**
- Existing sets updated: **7**
- Cards inserted: **20,702**
- Existing Cards updated: **262**
- Baseline Prints inserted: **20,464**
- Existing Prints updated: **500**
- Legacy Prints relinked to their canonical Card: **238**
- TCGdex Print identifiers inserted: **20,464**
- Source image rows inserted: **19,007**

A previous bootstrap attempt that violated the real Neon `prints.rarity NOT NULL` constraint rolled back completely. The successful baseline used the literal rarity `unknown` until authoritative rich-source enrichment.

## Released-English supplementation evidence

The 101 official-repository identities omitted by REST were handled as a separate controlled identity augmentation:

- Preflight conflicts: **0**
- Cards inserted: **101**
- Prints inserted: **101**
- TCGdex identifiers inserted: **101**
- Released-English supplements accepted in Neon: **101 / 101**
- Released-English supplements still pending: **0**

No existing card was overwritten to manufacture these identities.

## Legacy rows preserved outside the canonical source set

After the baseline bootstrap the Pokémon tables contained a few intentionally preserved historical rows in addition to the canonical source identities. They are not counted as canonical English source identity.

Known stale TCGdex IDs preserved for later controlled cleanup:

- `sv1-1`
- `sv1-62`

No destructive delete was performed during bootstrap or released-English supplementation.

## Valid zero-card source sets

TCGdex contains four valid physical Set identities that currently own zero cards in the global REST `/cards` surface:

- `jumbo`
- `rc`
- `sp`
- `wp`

They are considered **present** when their canonical `Set.tcgdex_id` exists. Set completeness is not inferred from card count.

## Rich metadata and physical variants

The pinned rich source provides structured metadata for the canonical English catalog, including:

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

The physical-variant preflight has independently normalized **27,241 safe variant definitions** across **14,549 cards**, with **0 deterministic Print-key collisions** and **0 unresolved variant groups** after resolving the documented `sv10-096` main-set vs additional-promo release context.

Variant expansion itself is a separate gated mutation and is not implied by this identity certification.

## Pipeline protection

The legacy Pokémon TCGdex daily ingest remains quarantined. It must not mutate this catalog. A V2 refresh pipeline will replace it only after rich attributes, physical variants and Pokémon Search V2 are certified.
