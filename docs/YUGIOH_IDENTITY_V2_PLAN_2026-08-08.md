# Yu-Gi-Oh! Identity V2 — Canonical Rebuild Plan

**Date:** 2026-08-08  
**Branch:** `catalog-v2`  
**Status:** architecture resolved; pre-write validation in progress

## Objective

Replace the incomplete/semantically incorrect legacy Yu-Gi-Oh! slice with a source-backed canonical model without contaminating `Set`, `Print`, release provenance, artwork identity or future pricing.

The rebuild must not be considered complete merely because rows load successfully. Every identity rule below must be backed by source evidence and postconditions.

## Source baseline

Current YGOPRODeck API v7 surface used by the V2 audits:

- database version: **146.37**
- last source update observed: **2026-08-07 12:00:19**
- source Cards: **14,480**
- `card_sets` rows: **44,287**
- unique deterministic physical Print identities: **44,285**
- official `cardsets.php` release rows: **1,032**
- unique official release names: **1,032**
- Cards with no `card_sets` physical evidence: **490**
- Cards exposing multiple artwork IDs: **124**
- cross-Card artwork-ID collisions: **0**

Legacy Neon currently contains only:

- Sets: **5,463**
- Cards: **2,010**
- Prints: **6,216**

The legacy Set count is inflated because the old connector treated the full printing code from `card_sets[].set_code` as a Set identity.

## Canonical architecture

### Game

`Game.slug = yugioh`

### Card

Canonical logical Card identity is the **YGOPRODeck card ID**.

One Card is not duplicated for each release/rarity/art treatment.

### Set

`Set` represents the **physical collector-number family**, not a commercial product.

For normal source codes containing a separator, family is derived directly from the prefix before the first hyphen.

Examples:

- `MRD-EN005` → Set family `MRD`
- `STAS-EN001` → Set family `STAS`
- `25TH-EN001` → Set family `25TH`

This deliberately allows the physical Set family to differ from the commercial release code.

### CatalogRelease

`CatalogRelease` represents the **source-defined commercial release/product/list** from official `cardsets.php`.

Identity uses the official release name, not `cardsets.php.set_code`, because release code is not unique in the source:

- 1,032 release rows
- 644 distinct official release codes
- 142 code values are reused by multiple official releases

The existing `CatalogRelease` model is the correct place for this concept.

### Print

An exact physical source Print identity is:

`Card ID + full card_sets.set_code + rarity code + canonical rarity + language surface`

For this source pass, language is `en`.

The raw full collector code is always preserved exactly as received. It is never silently repaired.

The current source produces **44,285 unique physical identities** with **0 deterministic identity collisions**.

Because the shared `Print` uniqueness constraint does not include rarity directly, Yu-Gi-Oh! V2 must encode the source-backed treatment/rarity identity in `variant` while also preserving the human rarity in `Print.rarity`.

### PrintRelease

`PrintRelease` is the many-to-many provenance link between an exact physical Print and its commercial `CatalogRelease`.

This separation is necessary because commercial release code and collector-number family are independent. The source audit found **79** rows where official release code and physical collector-family prefix differ. These are not errors to normalize away.

Examples include physical families such as `STAS`, `DPCT`, `25TH` or `GTP2` appearing inside releases whose official product code differs.

## Legacy collector codes without a hyphen

Targeted audit found exactly **12** such rows, all in `Dark Beginning 1`:

`DB49`, `DB46`, `DB32`, `DB14`, `DB9`, `DB43`, `DB48`, `DB13`, `DB40`, `DB47`, `DB5`, `DB18`.

Evidence in that same official release:

- total `card_sets` rows: 262
- explicitly hyphenated rows: **250**
- all 250 explicitly parse to family **DB1**
- official release code: **DB1**
- conflicting explicit family in that release: **0**

Therefore the V2 fallback policy may assign family `DB1` to these 12 rows with an explicit provenance flag such as `family_resolution = same_release_unanimous_fallback`.

Important boundaries:

- raw collector number remains the source value (`DB49`, etc.)
- no synthetic `DB1-ENxxx` collector number is fabricated
- fallback is allowed only because same-release explicit family evidence is unanimous and matches the official release code
- any future no-hyphen case that does not satisfy that exact gate remains unresolved rather than guessed

## Rarity policy

The source contains **206** `card_sets.set_rarity` values that are not rarity labels:

- `New`: 83
- `2`: 53
- `3`: 41
- `Reprint`: 11
- `New artwork`: 9
- `European & Oceanian debut`: 6
- `force-SMW`: 1
- `European debut`: 1
- `Oceanian debut`: 1

All 206 also lack a rarity code in the current source surface.

Policy:

- known spelling/alias errors normalize to the canonical rarity label
- the nine non-rarity/noisy labels above normalize to canonical `Unknown`
- raw source label is preserved in `print_attributes` / provenance
- raw rarity code is preserved when present
- no attempt is made to infer a real rarity from price, release name or artwork

## Cards without physical Print evidence

Exactly **490 Cards** have no `card_sets` rows. The targeted resolver found that all 490 also lack release dates on the current source surface.

Policy:

- keep the canonical Card identity
- do **not** manufacture a Print
- expose the gap in Catalog Health
- a later source may add exact physical Print evidence

The platform must distinguish “Card exists” from “physical English Print proven by this source”.

## Artwork policy

The current source contains **14,644 unique image IDs** and **124 Cards** with alternate artwork candidates. No image ID belongs to two different Cards.

However, `card_images[]` does not provide a reliable per-Print mapping for all physical variants.

Policy:

- preserve every source artwork candidate at Card-level provenance
- do not claim an artwork is the exact artwork for a Print without explicit evidence
- if a default card image is used operationally for display, mark it as an unresolved Card-level representative image rather than exact physical-art identity
- future entity-resolution work may promote a Card artwork candidate to a Print only when another source proves the mapping

## Legacy dependency gate

Initial read-only Neon dependency audit found:

- Print images: **6,221**
- legacy Search Documents: **13,689**
- prices: **0**
- price snapshots / OHLC: **0**
- products: **0**
- catalog releases / print releases: **0**
- market observations/indexes: **0**
- holdings: **0**

The image/search rows are rebuildable/source-recreatable. No durable business/pricing/portfolio dependency was found in the initial audit.

A second exhaustive FK introspection gate is required before replacement. Any nonzero unknown or durable FK relationship blocks the destructive phase.

## Rebuild strategy

### Phase A — source snapshot outside canonical tables

Build a deterministic snapshot/artifact from the official source containing:

- Cards
- Set families
- Catalog Releases
- Prints
- PrintRelease mappings
- Card attributes
- Print attributes
- artwork candidates
- representative image policy
- source/version metadata

The snapshot must pass all expected-count and collision gates before Neon is modified.

### Phase B — dry-run row-shape validation

Validate the planned rows against current database constraints without committing canonical changes.

Required checks include:

- `(game_id, Set.code)` uniqueness
- Card `yugoprodeck_id` uniqueness
- `Print.yugioh_id` uniqueness
- `Print.print_key` uniqueness
- global `(set_id, collector_number, language, is_foil, variant)` compatibility
- CatalogRelease `(game_id, source, external_id)` uniqueness
- PrintRelease `(print_id, release_id)` uniqueness

No uniqueness requirement may be weakened merely to make YGO load.

### Phase C — legacy recovery evidence

Before canonical replacement, export the current YGO legacy slice needed for forensic recovery:

- Sets
- Cards
- Prints
- PrintImages
- any nonzero FK dependents
- legacy SearchDocuments or a count/hash manifest

This is a recovery artifact, not the new canonical source.

### Phase D — one transactional replacement

Use PostgreSQL set-based staging/COPY, not per-row ORM writes.

Within one transaction:

1. load V2 snapshot into temporary staging tables
2. re-run source/snapshot postconditions
3. remove rebuildable YGO dependents in FK-safe order
4. remove only the YGO legacy core scope
5. insert canonical V2 Sets/Cards/Prints
6. insert Catalog Releases and PrintRelease provenance
7. insert attributes / identifiers / representative-image evidence
8. run all postconditions against actual canonical tables
9. commit only when every gate passes

Any failure before commit must roll back the entire canonical replacement.

Because Neon storage is limited to 512 MB, do not create a long-lived duplicate YGO catalog in permanent shadow tables. Prefer external artifacts + PostgreSQL temporary staging + a single transaction.

## Expected canonical postconditions before Search V2

At minimum:

- Cards = **14,480**
- source-backed physical Print identities = **44,285**
- Cards without a physical source Print = **490**
- official Catalog Releases = **1,032**
- duplicate exact release names = **0**
- physical Print identity collisions = **0**
- cross-Card image-ID collisions = **0**
- noisy rarity rows preserved as raw provenance = **206**
- synthetic Prints for no-evidence Cards = **0**
- raw full collector code preserved for every source Print
- no unresolved no-hyphen Set-family row under the accepted fallback gate

The final Set-family count is source-derived and must be frozen by the deterministic snapshot audit rather than hardcoded prematurely.

## After canonical rebuild

Only after canonical data passes should work continue to:

1. Yu-Gi-Oh!-specific Card/Print attributes
2. Search V2 facets
3. Search/index build
4. functional benchmarks
5. latency optimization
6. HTTP contract QA
7. frontend `/games/yugioh`
8. Chromium desktop/mobile QA
9. final Yu-Gi-Oh! certification

Candidate facets include source-backed dimensions such as card class (Monster/Spell/Trap), attribute, race/type, archetype, level/rank/link, ATK/DEF, Pendulum data, rarity, Set family, Catalog Release, year and legal/banlist status where the source supports it.

## Non-negotiable trust rules

- A commercial release is not a Set merely because the source calls a field `set`.
- A raw printing code is never silently rewritten.
- A noisy metadata label is not promoted to rarity.
- A Card without physical evidence does not get a fake Print.
- Multiple artwork candidates do not imply a known Print↔art mapping.
- Existing durable dependencies must be remapped, never discarded.
- The legacy YGO catalog is not deleted until the V2 snapshot and dependency gates pass.
