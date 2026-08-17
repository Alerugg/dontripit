# Pokémon Catalog V2 — Final Certification

**Certification date:** 2026-08-08  
**Branch:** `catalog-v2`  
**Status:** **FINAL CERTIFIED**

This document freezes the evidence used to certify the Pokémon catalog/search slice before moving certification work to Yu-Gi-Oh!.

> Project rule: do not advance because the system merely works; advance when the data and behavior can be trusted.

## 1. Certified canonical scope

| Gate | Certified value |
|---|---:|
| Canonical Cards | **21,065** |
| Exact physical variant identities | **27,241** |
| Exact canonical Prints in Search V2 scope | **33,757** |
| Additional physical Prints beyond baseline | **12,692** |
| Pokémon facet definitions | **23** |
| Stale source identities admitted to canonical/Search V2 scope | **0** |

Canonical Card identity and exact physical Print identity are separate. Marketplace product IDs are not treated as exact Print identity when the source reuses one marketplace ID across multiple physical variants.

During physical expansion, **44,298** marketplace references were evaluated. **15,471** references were shared across variants and were therefore not promoted as exact identity. **12,636** marketplace references could be promoted safely as 1:1 evidence. Exact physical identity remains source/canonical rather than marketplace-defined.

## 2. Enrichment coverage

The canonical Pokémon card set was enriched from the pinned source snapshot before Search V2 certification.

- **21,065 / 21,065** canonical Cards enriched.
- Canonical category coverage: complete for the certified scope.
- **20,350** illustrator values present.
- **8,291** regulation marks present.
- **14,590** Cards expose source-backed physical variant definitions.
- No stale source IDs were allowed to acquire canonical attributes.

Rich gameplay/effect metadata remains in canonical attributes. Search projections deliberately keep only data needed for search/filter behavior to avoid duplicating large payloads per physical variant.

## 3. Search V2 contract

Pokémon Search V2 is certified with **23 game-specific facets**. Core collector/gameplay dimensions include:

- Set
- Collector Number
- Series / Era
- Release Year
- Language
- Category
- Pokémon Type
- Stage
- HP
- Trainer Type
- Energy Type
- Evolves From
- Pokédex number
- Rarity
- Regulation Mark
- Illustrator
- Finish
- Foil Pattern
- Stamp
- Variant Subtype
- Release Context
- Card Size
- Exact Variant

The final Quick Filter contract includes:

`types`, `stage`, `rarity`, `regulation_mark`, `finish`, `stamp`

The final lightweight facet synchronization changed only Pokémon `facet_definitions`; it touched **0 canonical Card/Print rows** and performed **no Search profile rebuild**.

## 4. Functional Search V2 certification

Certified behavior includes:

- natural card-name search (`Pikachu`, `Charizard`)
- set / collector-number search
- Pokémon Type filters
- Stage filters
- HP range filters
- rarity filtering including collector rarities
- illustrator filtering
- finish filtering
- regulation-mark filtering
- stamp filtering
- Pokédex-number filtering
- exact physical Print results
- rejection of unsupported/invented filters
- facet values and suggestion endpoints

The HTTP contract was tested through the Flask application against Neon for Search, Suggest, Facets, Facet Values and Advanced Search.

## 5. Latency gate

Search V2 was not accepted after functional correctness alone. The first implementation was rejected for excessive latency and then redesigned around a Pokémon-specific card-first path and narrower advanced-query pagination.

Representative certified timings against Neon after optimization:

| Query / filter | Observed latency |
|---|---:|
| Pikachu | ~**289 ms** |
| Charizard | ~**187 ms** |
| Finish = Holo | ~**365 ms** |
| Worst measured benchmark before final compaction | ~**674 ms** |
| Worst measured benchmark after compaction | ~**523 ms** |

These timings are evidence from CI runners against the connected Neon database, not browser-device SLAs.

## 6. Neon storage gate

Pokémon Search V2 initially pushed Neon too close to its **512 MB** storage ceiling. The system was not considered ready for the next TCG until this was corrected.

Observed progression:

- ~476.3 MB before Search V2 storage cleanup
- ~431.4 MB after removing unused heavy indexes and retaining targeted useful indexes
- **292.23 MB / 512 MB** after compacting Search V2 projections

The compaction preserved canonical rich card/print attributes and only removed unnecessary duplication from derived search profiles. Functional and latency benchmarks remained green after compaction.

## 7. Frontend / BFF certification

Certified frontend gates:

- frontend contract tests pass
- `next build` passes
- `/games/pokemon` renders the Search V2 experience
- Search V2 BFF routes serialize/forward the Pokémon contract correctly
- Pokémon-specific copy and examples are shown instead of One Piece examples
- Pokémon physical badges expose relevant print dimensions
- Quick Filters are game-configured rather than hardcoded to One Piece

The isolated visual-QA environment may use `INTERNAL_API_ALLOW_PUBLIC=true` only for the local CI stack. Production behavior continues to require the normal internal API credential configuration.

## 8. Final browser / responsive certification

**Final visual workflow:** `Visual QA Pokemon Search V2`  
**GitHub Actions run:** `31264112187`  
**Conclusion:** **success**

The Playwright/Chromium gate verified:

1. desktop hero and certified counters
2. full Advanced Search panel opens
3. six Pokémon Quick Filters render
4. natural `Pikachu` search returns card results
5. Holo Quick Filter returns **Exact Prints**
6. Pokémon physical identity badges are visible
7. mobile viewport has no horizontal overflow
8. Search V2 BFF produced no HTTP errors
9. browser produced no page errors or fatal console errors

Evidence screenshots produced by the successful run:

- `pokemon-desktop-home.png`
- `pokemon-desktop-advanced-open.png`
- `pokemon-desktop-search.png`
- `pokemon-desktop-advanced-holo.png`
- `pokemon-mobile.png`

The run observed **30 remote resource-load errors** from external card-image URLs. These are recorded as non-fatal evidence because the UI has an explicit image fallback and Search/API functionality remained healthy. This certification therefore **does not claim 100% remote image-host availability**.

## 9. Certification boundaries

This certification means the Pokémon slice is trusted for the current V2 product scope:

- canonical Card identity
- source-backed exact physical variants represented in the certified snapshot
- rich attributes
- advanced filtering
- natural search
- responsive Search V2 UI
- reproducible CI evidence

It does **not** certify future FMV/pricing, portfolio functionality, grading, marketplace matching, or indefinite external image availability. Those are later gates.

It also does not assert that any third-party marketplace product identifier is an exact physical identity unless that mapping was proven 1:1.

## 10. Decision

**Pokémon V2 is FINAL CERTIFIED and is no longer the active catalog-certification blocker.**

Future Pokémon changes that alter canonical counts, physical-variant identity, the 23-facet contract, Search V2 semantics, or certification postconditions must re-run the relevant gates rather than silently changing this baseline.

## 11. Post-certification base-table hygiene

A later storage audit found that the physical `cards` / `prints` base tables still contained **one legacy Card and three legacy Prints** from the pre-V2 importer even though the certified V2 attributes and Search projection were already exact.

Read-only evidence proved that these rows were historical residues rather than missing canonical content:

- legacy Card `366`, `Pineco`, source ID `sv1-1`, had no V2 identity key, attributes or Search profile;
- legacy Print `512` mapped that stale `sv1-1` identity to the old Pineco row;
- legacy Print `513` incorrectly attached source identity `sv1-62` to Pikachu;
- legacy Print `1` attached Scarlet & Violet collector `001` to Pikachu with no source identity.

The legacy source IDs were reconciled before any deletion:

- `sv1-1` → canonical `sv01-001` → **Pineco**, with a complete V2 Card/Print identity;
- `sv1-62` → canonical `sv01-062` → **Tatsugiri**, proving the legacy Pikachu assignment was incorrect.

The cleanup was guarded by exact row signatures, exact before/after counts, canonical-replacement checks, a scan of every public relation exposing `card_id` / `print_id`, and transaction rollback on any unexpected dependency. The first attempt deliberately aborted before writes when it discovered `print_search_projection`; PostgreSQL metadata then proved that relation was an ordinary derived view (`relkind = v`), not stored canonical state.

The successful transactional cleanup removed exactly:

- **1** legacy Card;
- **3** legacy Prints;
- **3** legacy Print identifiers;
- **3** legacy Print images.

It touched:

- **0** pricing/product rows;
- **0** certified Card attributes;
- **0** certified Print attributes;
- **0** Card Search profiles;
- **0** Print Search profiles.

Final post-cleanup base-table and projection counts now match exactly:

| Layer | Cards | Prints |
|---|---:|---:|
| Canonical base tables | **21,065** | **33,757** |
| Attributes | **21,065** | **33,757** |
| Search V2 profiles | **21,065** | **33,757** |
| Rows outside certified V2 projection | **0** | **0** |

Cleanup workflow evidence: `Cleanup Pokemon Legacy Scope Anomalies V2`, run **31270286065**, successful rerun job **93135222273**.

This hygiene pass strengthens the original certification: Pokémon no longer merely has an exact certified projection; the underlying canonical base tables themselves now exactly equal that certified scope.
