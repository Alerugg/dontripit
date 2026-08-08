# MTG Identity V2 — Preflight and Architecture Gate

**Date:** 2026-08-08  
**Branch:** `catalog-v2`  
**Status:** **REVIEW REQUIRED — FULL BOOTSTRAP QUARANTINED**  
**Primary source:** Scryfall `default_cards` bulk JSONL/GZIP

## Decision

Magic: The Gathering must **not** be fully bootstrapped into the current Neon schema yet.

The source is healthy and the identity problem is solvable, but the current generic storage model cannot hold a complete MTG catalog inside the existing 512 MiB Neon limit while preserving the project's operational safety margin.

The next gate is an ephemeral PostgreSQL shadow build that measures two candidate physical schemas using the complete current Scryfall paper corpus. No Neon writes are allowed until that measurement is complete.

---

## 1. Current live database headroom

At preflight time:

- current database: **440.43 MiB**
- Neon physical limit: **512 MiB**
- project safety ceiling: **480 MiB**
- physical headroom: **71.57 MiB**
- headroom to the project safety ceiling: **39.57 MiB**

The project will not intentionally materialize a design that exceeds the 480 MiB safety ceiling or eliminates operational reserve.

---

## 2. Current MTG state in Neon

The existing MTG data is a small legacy/incremental subset, not a complete certified catalog.

| Entity | Current rows |
|---|---:|
| Sets | 298 |
| Cards | 1,183 |
| Prints | 1,588 |
| Print images | 1,589 |
| Print identifiers | 1,588 |
| Card attributes | 0 |
| Print attributes | 0 |
| Card Search V2 profiles | 0 |
| Print Search V2 profiles | 0 |
| Facets | 0 |
| Legacy search documents | 3,069 |
| Prices | 0 |
| Products | 0 |

Legacy duplicate physical Print identity groups: **0**.

This subset has no durable pricing/product dependencies blocking a later canonical MTG replacement.

---

## 3. Current Scryfall paper corpus

Read-only preflight against the official Scryfall bulk source found:

| Source dimension | Count |
|---|---:|
| Bulk rows | 116,694 |
| Paper rows / source print objects | **107,337** |
| Unique Scryfall IDs | **107,337** |
| Unique Oracle IDs | **37,553** |
| Unique set codes | **986** |
| Exact finish variants | **161,275** |
| Source objects with multiple finishes | **53,693** |
| Missing `oracle_id` rows | **81** |
| Rows without normal image evidence | **162** |
| Natural `(set, collector, language, finish)` collisions | **0** |
| Unknown finish labels | **0** |

### Languages

Largest language groups:

- English: 104,703
- Spanish: 1,207
- Japanese: 651
- French: 430
- Italian: 194
- additional smaller language groups also exist

MTG V2 must therefore be multi-language from the identity layer rather than certifying English only.

### Finishes

Current source-backed finish counts:

- `nonfoil`: **94,121**
- `foil`: **65,936**
- `etched`: **1,218**

Common combinations:

- `foil + nonfoil`: 53,367 source objects
- `nonfoil`: 40,468
- `foil`: 12,284
- `etched`: 892
- `etched + foil + nonfoil`: 245
- `etched + nonfoil`: 41
- `etched + foil`: 40

This proves that the legacy `Print.is_foil + variant=default` model is insufficient for MTG.

---

## 4. Certified candidate identity rule

Current source evidence supports the following V2 identity model.

### Game

`mtg`

### Set

Scryfall set code.

### Logical Card

1. use Scryfall `oracle_id` when present;
2. when `oracle_id` is absent, use **normalized card name + layout**.

A dedicated full-source resolver audited all 81 missing-Oracle rows:

- 81 rows
- 71 `name + layout` identity groups
- 71 `name + layout + rules signature` groups
- **0 ambiguous `name + layout` groups**

Therefore `normalized name + layout` is sufficient for the current missing-Oracle source scope. A rules hash is not required today, but remains a future collision gate.

The missing-Oracle examples are primarily source-backed `reversible_card` objects.

### Source Print

Scryfall card object `id`.

This identifies one source printing object, not necessarily one sellable physical finish.

### Exact physical variant / market entity

`Scryfall source print ID + finish`

where certified current finishes are:

- `nonfoil`
- `foil`
- `etched`

Source dimensions retained alongside identity:

- set code
- collector number
- language
- rarity
- release date
- artist / illustration evidence where available
- frame/treatment-related source fields where later certified

The full source has **0 collisions** for `(set code, collector number, language, finish)` today, but Scryfall ID remains part of provenance and must not be discarded.

---

## 5. Why the current `Print` schema cannot represent MTG exactly

Current legacy behavior stores one `Print` per Scryfall object and derives:

- `is_foil = card.foil`
- `variant = default`
- globally unique `Print.scryfall_id`

That collapses the 53,693 source objects which support more than one physical finish.

Simply creating multiple `Print` rows per finish is possible only after changing the current global uniqueness semantics of `Print.scryfall_id` and the connector lookup logic. More importantly, it duplicates a large amount of source-print and Search V2 data.

The preferred candidate to test is therefore:

```text
Card
  ↓
SourcePrint (one row per Scryfall object)
  ↓
PrintFinishVariant (one very small row per nonfoil / foil / etched availability)
```

The exact market/portfolio entity can be the finish-variant row while identity/search fields that do not change by finish remain stored once on `SourcePrint`.

A duplicated-finish `Print` model will be built in parallel in ephemeral PostgreSQL as a control.

---

## 6. SourceRecord storage blocker

The ingest engine historically persisted the full source JSON for every new checksum in `SourceRecord.raw_json`.

Current Scryfall paper JSON volume:

- uncompressed paper JSON lines: **578,435,764 bytes**
- approximately **551.64 MiB** before PostgreSQL/index overhead

The preflight projected roughly **673.8 MB** of new operational database usage for those raw SourceRecords alone.

That is larger than the complete Neon database limit and cannot be part of the MTG production design.

### Guardrail now implemented

`SourceConnector` supports a connector-level raw-payload persistence policy.

Default behavior remains unchanged for other sources.

`ScryfallMtgV2Connector` now sets:

```python
persist_raw_source_payload = False
```

New Scryfall `SourceRecord` rows retain:

- source association
- checksum
- ingestion timestamp
- a small explicit `_payload_omitted` marker

but do not duplicate the upstream card JSON.

The official bulk source is authoritative and re-fetchable. The operational database therefore keeps provenance/idempotency without becoming a raw-data archive.

Focused regression CI confirms that:

- Scryfall checksum/idempotency still works;
- the raw card JSON is omitted;
- other connectors retain raw payloads by default;
- existing Scryfall ingest tests still pass.

---

## 7. General ingest quarantine

MTG is now forced to `mtg_limit=0` inside the general `ingest.yml` pipeline on `catalog-v2`.

The former manual `mtg_limit` input is explicitly marked ignored while V2 is uncertified.

This prevents a scheduled or manual general refresh from accidentally starting a large legacy Scryfall materialization.

Dedicated read-only/shadow MTG workflows remain allowed.

This quarantine stays in place until Identity V2 + storage + Search V2 are certified.

---

## 8. Why the current Neon schema does not fit

The read-only preflight estimated the following scenarios using measured current relation costs.

| Scenario | Estimated addition | Projected DB |
|---|---:|---:|
| Legacy one-row-per-Scryfall-object catalog, no raw history | +168.19 MiB | 608.62 MiB |
| Legacy object catalog + SourceRecords | +810.75 MiB | 1,251.18 MiB |
| Exact finish catalog, no raw history | +239.97 MiB | 680.40 MiB |
| Exact finish + attributes | +514.02 MiB | 954.45 MiB |
| Exact finish + attributes + Search V2 | +723.39 MiB | 1,163.82 MiB |
| Full current contract incl. raw SourceRecords | +1,365.95 MiB | 1,806.38 MiB |

**Safe scenarios under the current 480 MiB project ceiling: 0.**

These are conservative estimates, not the final architecture decision. The next shadow build will replace them with actual PostgreSQL relation sizes.

---

## 9. Generic index/storage audit

The current shared schema contains material overhead from source-specific indexes that apply to rows from unrelated games.

Measured source-specific/external-ID index footprint: approximately **33.72 MiB**.

Examples of sparse source columns:

### Sets — 1,208 rows

- `tcgdex_id`: 16.887% populated
- `yugioh_id`: 53.477%
- `riftbound_id`: 0.083%

### Cards — 39,395 rows

- `oracle_id`: 3.003%
- `tcgdex_id`: 53.474%
- `yugoprodeck_id`: 36.753%
- `riftbound_id`: 0.005%
- `card_key`: 96.989%

### Prints — 84,248 rows

- `scryfall_id`: 1.885%
- `tcgdex_id`: 25.006%
- `yugioh_id`: 52.495%
- `riftbound_id`: 0.002%
- `print_key`: 98.109%

This strongly suggests that partial/source-scoped indexes or a normalized external-identifier layer can reduce future growth. No semantic index removal has been authorized solely from scan counts; query usage is being audited before any migration.

---

## 10. Image limitation

The full source contains **162 paper objects without normal image evidence**.

The audited samples are predominantly Scryfall `art_series` / memorabilia objects.

MTG V2 must preserve these identities without fabricating images. The UI may use an explicit placeholder/source limitation state.

---

## 11. Search V2 implication

The current shared `facets.py` has no MTG facet contract yet.

MTG Search V2 must be built only after physical identity is locked.

Desired filters include at minimum:

- Set
- Collector number
- Language
- Rarity
- Finish
- Color identity
- Mana cost / mana value
- Card type / subtype
- Legalities
- Artist
- Frame / treatment when source-backed
- Release year

Search must avoid repeating large searchable text once per finish when the finish is merely a child availability dimension.

---

## 12. Next gate — ephemeral PostgreSQL shadow build

Before any full Neon write, GitHub Actions will start a disposable PostgreSQL 16 service and materialize the entire current paper corpus into two competing designs.

### Model A — duplicated exact Print

- one row per `Scryfall ID + finish`
- approximately 161,275 exact print rows
- finish-specific print search profile for every row

### Model B — SourcePrint + FinishVariant

- approximately 107,337 SourcePrint rows
- approximately 161,275 lightweight FinishVariant rows
- one source-print search profile per Scryfall object
- exact finish resolved by indexed child relation

Both models will receive representative production indexes and `pg_trgm` search indexes.

The shadow workflow will measure:

- exact row counts
- heap size
- index size
- total relation size
- search-index size
- representative natural-search latency
- exact collector lookup latency
- finish-filter latency
- source-ID lookup latency

No Neon credentials or Neon writes are needed for this gate.

---

## 13. Advancement rule

MTG may advance to a real canonical bootstrap only when all of these are true:

1. exact Card and physical-finish identity is collision-free;
2. no full Scryfall raw payload is duplicated into the operational DB;
3. a measured storage architecture fits the selected production database with meaningful operational reserve;
4. Search V2/facet design fits without pathological duplication;
5. legacy MTG dependencies are audited before replacement;
6. full-source Catalog Health can prove completeness against the current Scryfall bulk snapshot.

Until then, **MTG remains quarantined from general ingest**.
