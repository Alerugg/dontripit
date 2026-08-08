# MTG Storage Architecture V2 — Decision Record

**Date:** 2026-08-08  
**Branch:** `catalog-v2`  
**Decision:** **Model D — Lean Hybrid Exact Print**  
**Full MTG bootstrap:** **BLOCKED on current 512 MiB Neon capacity**

## 1. Decision summary

MTG V2 will preserve the project's universal exact physical market entity as `Print.id`, while separating Scryfall source-print metadata from exact finish identity.

Chosen shape:

```text
Card
  ↓
MTG SourcePrint  ───── one row per Scryfall card object
  ↓
Print            ───── one lean exact physical row per SourcePrint + finish
```

Search metadata is stored once per SourcePrint, not once per finish.

Exact physical/market identity is:

```text
Scryfall source print ID + finish
```

Certified finishes currently are:

- `nonfoil`
- `foil`
- `etched`

`Print.id` remains the exact entity used by pricing, FMV, holdings, portfolio and future alerts.

## 2. Why Model D won

Four full-source architectures were built against the same current Scryfall paper corpus in disposable PostgreSQL 16.

| Model | Exact physical entity | Search rows | Measured total |
|---|---|---:|---:|
| A — duplicated exact Print | Print | 161,275 | **235.41 MiB** |
| B — SourcePrint + FinishVariant | FinishVariant | 107,337 | **197.68 MiB** |
| C — full hybrid exact Print | Print | 107,337 | **239.09 MiB** |
| **D — lean hybrid exact Print** | **Print** | **107,337** | **202.63 MiB** |

Model D is only **4.95 MiB larger than B**, while preserving `Print.id` as the universal exact market entity.

Savings:

- D vs A: **32.78 MiB**
- D vs C: **36.46 MiB**

This is a strong enough storage advantage to reject duplicated search/metadata, without forcing pricing and portfolio to adopt a game-specific exact-entity concept.

## 3. Model D measured performance

Full current source counts used in the shadow benchmark:

- Sets: **986**
- logical Cards: **37,624**
- SourcePrints: **107,337**
- exact Prints: **161,275**
- multi-finish SourcePrints: **53,693**
- nonfoil: **94,121**
- foil: **65,936**
- etched: **1,218**

Median query latency in ephemeral PostgreSQL:

| Query | Median |
|---|---:|
| Name search | **1.82 ms** |
| Exact collector | **0.26 ms** |
| Name + finish → exact Print | **3.40 ms** |
| Scryfall ID + finish → exact Print | **0.24 ms** |
| Exact `Print.id` | **0.23 ms** |

The finish join is therefore not a practical latency concern at this corpus size.

## 4. Canonical entities

### Card

Logical MTG rules identity:

1. `oracle_id` when present;
2. source-backed fallback identity for missing-Oracle objects.

The current helper `backend/app/mtg_identity_v2.py` uses a conservative fallback containing normalized name/layout plus a rules signature. This is stronger than the minimum full-source proof and is accepted for V2 because it prevents future silent collisions.

### SourcePrint

One row per Scryfall object ID.

Owns source-print metadata that does not change by finish, including:

- Scryfall ID
- Card
- Set
- collector number
- language
- rarity
- release date
- artist
- illustration evidence
- frame/treatment evidence when certified
- promo flags
- primary image evidence
- source-specific attributes

### Print

One lean exact physical row per `SourcePrint + finish`.

Owns only the dimensions required for universal exact identity and cross-TCG market references.

At minimum:

- `Print.id`
- `source_print_id`
- `card_id`
- `set_id`
- finish / exact variant

Shared-schema compatibility columns may temporarily retain a small amount of duplicated metadata where existing NOT NULL constraints require it. Any such duplication must be measured in the production-schema shadow gate before real bootstrap.

## 5. Search architecture

MTG must not create one heavy Search V2 document per finish.

Instead:

```text
Card Search Profile          → one per logical Card
MTG SourcePrint Search       → one per Scryfall SourcePrint
Exact finish filtering       → indexed join to lean Print rows
```

This is the key storage difference between Models A/C and Models B/D.

## 6. Market/FMV compatibility

The selected design deliberately keeps exact `Print.id`.

Therefore existing/future contracts remain coherent:

- `Price.print_id`
- market observations by exact Print
- FMV snapshot by exact Print
- Holding/portfolio by exact Print
- alerts/watchlist by exact Print

No MTG-specific `FinishVariant` foreign key is needed in the market layer.

## 7. Scryfall provenance policy

The Scryfall bulk export is a versioned, authoritative, re-fetchable source.

Production MTG V2 should therefore use:

```text
Source / SourceSyncState
        ↓
Bulk snapshot manifest/version
        ↓
SourcePrint.scryfall_id
```

It should **not** create a permanent operational `SourceRecord` row for every card on every bulk snapshot.

The already-implemented raw-payload guardrail remains useful, but the final MTG bulk path should go further and use snapshot-level provenance instead of 107k per-card checksum rows.

The generic ingest workflow remains quarantined for MTG until this dedicated path is certified.

## 8. Live revision 25

Revision `20260808_25` was safely applied to the current Neon database.

It changes only Scryfall uniqueness semantics on the existing shared `prints` table:

- removed legacy global uniqueness on `scryfall_id`;
- added partial unique `(scryfall_id, variant)` for non-null Scryfall IDs.

Live workflow:

- run: **31271811652**
- result: **PASS**
- catalog row writes: **0**
- Card counts unchanged for every game
- Print counts unchanged for every game
- database size: **461,824,000 → 461,021,184 bytes**
- stable size after migration: approximately **439.66 MiB**

Revision 25 is a compatibility preparation, not the final Model D storage layer.

## 9. Capacity decision

Current stable Neon after revision 25 is approximately **439.66 MiB**.

Model D shadow size is **202.63 MiB**.

Naive combined magnitude:

```text
439.66 + 202.63 = 642.29 MiB
```

The final replacement will reclaim the small legacy MTG subset and production-schema packing will differ from the isolated shadow model, but neither effect can make a 512 MiB database viable.

### 512 MiB

**Rejected.** Full certified MTG cannot fit with operational reserve.

### 768 MiB

At ~642 MiB, only about 126 MiB remains before future history, market observations, rebuild peaks and additional games. That is roughly 16% headroom before reclaiming legacy data and is too tight for the project's trust-first operating model.

**Not selected as the target.**

### 1 GiB

At ~642 MiB, roughly 382 MiB remains, about 37% of capacity.

This provides substantially healthier room for:

- transactional rebuild peaks;
- search/index maintenance;
- price history;
- market observations;
- portfolio growth;
- future source metadata;
- later game expansion.

**Decision: 1 GiB is the minimum operational database target for a single-PostgreSQL architecture before full MTG bootstrap.**

This is a minimum, not a permanent capacity ceiling.

## 10. Next gate

Before creating the final migration/data loader, build Model D against the **actual shared production Alembic schema** in disposable PostgreSQL.

The gate must measure:

- `mtg_source_prints` table design;
- optional FK from shared `prints` to SourcePrint;
- existing shared `Print` NOT NULL/unique constraints;
- source-specific metadata storage;
- Card attributes;
- SourcePrint Search V2;
- exact finish join;
- realistic shared indexes;
- snapshot-level provenance instead of per-card raw SourceRecords.

This gate decides the exact revision 26 DDL.

## 11. No-go rules

Do not:

- bootstrap full MTG into the current 512 MiB Neon;
- re-enable MTG in generic `ingest.yml`;
- duplicate Scryfall raw JSON into Neon;
- create heavy print-search rows per finish;
- treat `is_foil` as sufficient MTG finish identity;
- discard exact `Print.id` for market/portfolio solely to save ~5 MiB;
- move to another database before the production-schema shadow measurement is complete.

## 12. Advancement rule

MTG can advance from architecture to production data only after:

1. revision 26 schema passes migration + downgrade tests in ephemeral PostgreSQL;
2. full source fits the selected >=1 GiB production capacity with reserve;
3. full-source canonical counts are deterministic;
4. exact `SourcePrint + finish` identity has zero collisions;
5. source snapshot provenance is certified;
6. MTG Search V2/facets pass functional and latency gates;
7. Catalog Health proves completeness against the same Scryfall bulk snapshot.
