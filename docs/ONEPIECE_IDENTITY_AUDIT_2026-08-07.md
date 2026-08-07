# One Piece Identity & Release Audit — 2026-08-07

Status: **Catalog reconstruction required before Catalog Certified**
Branch: `catalog-v2`

## Executive conclusion

The first One Piece bootstrap proved that the ingestion, canonical database, images, identifiers and search indexing can work reliably at production scale. However, two independent audits show that the current One Piece canonical identity is not yet correct enough for pricing.

The problem is not database integrity. The problem is semantic identity.

A price engine must never aggregate market observations onto the wrong logical card or physical printing, so One Piece must be corrected before pricing work continues.

## 1. Internal health after first bootstrap

The optimized One Piece bootstrap inserted:

- 56 canonical collector-family sets
- 1,121 `Card` rows
- 3,810 `Print` rows
- 3,810 primary images
- 3,810 structured external identifiers

Internal Catalog Health reported:

- zero prints without images
- zero prints without primary images
- zero prints without external identifiers
- zero missing `card_key`
- zero missing `print_key`
- zero potential duplicate print identity groups
- 56 sets missing release date

This proves the stored rows are internally consistent. It does **not** prove external completeness or correct semantic identity.

## 2. Official source coverage audit

The official English ONE PIECE Card Game card list exposed:

- 84 source series
- 4,673 raw card/print appearances
- 4,402 appearances accepted by the existing parser
- 271 appearances discarded
- parser coverage: **94.201%**

Discarded collector families:

- `P-xxx`: **231** appearances
- `PRBxx-xxx`: **40** appearances

The current parser only derives commercial codes matching `OP`, `ST` or `EB`, which explains these exclusions.

Fourteen official series contain currently discarded cards, including promotional, premium, tournament, regional and special-product series.

## 3. Commercial release context is being collapsed

The official source contains 84 series while the current canonical bootstrap produced only 56 collector-family `Set` rows.

At least 39 collector-family set codes occur in more than one official source series. This proves that the collector-number prefix and the commercial release/product are not the same concept.

Examples of concepts that need to coexist:

- collector family / original numbering system
- commercial product or promotional program
- exact physical print / treatment

A print can appear in more than one official commercial release without becoming a different physical print. Therefore commercial release provenance must be many-to-many.

## 4. Logical Card identity is wrong for One Piece

A second audit tested the current logical `Card` grouping in Neon.

Current database:

- canonical `Card` rows: **1,121**
- canonical `Print` rows: **3,810**
- unique base collector numbers represented: **1,290**
- `Card` rows containing more than one collector number: **304**
- prints attached to those collapsed rows: **1,307**
- additional collector-number card definitions collapsed by name: **169**
- maximum collector numbers collapsed into one `Card`: **10**
- collector numbers attached to multiple `Card` rows: **0**

The last number is important: collector number behaves like a stable logical identity key, while visible name does not.

Example: one `Monkey.D.Luffy` Card row currently contains ten mechanically different card numbers:

- OP01-003
- OP02-041
- OP03-070
- OP04-090
- OP05-060
- OP07-033
- OP09-061
- OP11-040
- OP13-078
- ST01-001

Those must not share one logical `Card` identity.

## 5. Revised One Piece identity rules

### Logical Card

For One Piece, the logical gameplay definition is keyed by the normalized base collector number.

Examples:

- `onepiece:op01-003`
- `onepiece:p-001`
- `onepiece:prb01-001`

The visible card name is descriptive data, not identity.

### Print

`Print` represents the exact collectible printing/treatment of the logical card.

Print identity remains based on dimensions such as:

- logical card
- collector family / canonical set
- language
- variant / treatment
- later: structured art / finish / edition dimensions

Examples such as parallel, R1 and R2 stay at Print level.

### Commercial release

Commercial release/product provenance is a separate concept.

One physical `Print` may appear in multiple releases.

The schema therefore needs a release layer rather than encoding commercial release into the `Set` foreign key.

## 6. Schema V2: CatalogRelease

Proposed table: `catalog_releases`

Fields:

- `id`
- `game_id`
- `source`
- `external_id`
- `name`
- `code` nullable
- `release_type` nullable
- `release_date` nullable
- `language` nullable
- `region` nullable
- `metadata_json` nullable
- `created_at`

Identity:

- unique `(game_id, source, external_id)`

For the current official One Piece source, `external_id` is the official `series` value and `name` is the official series label.

## 7. Schema V2: PrintRelease

Proposed table: `print_releases`

Fields:

- `id`
- `print_id`
- `release_id`
- `source_print_id` nullable
- `appearance_type` nullable
- `metadata_json` nullable
- `created_at`

Identity:

- unique `(print_id, release_id)`

This allows one exact print to be associated with multiple commercial releases without duplicating the print or its market value.

## 8. What remains unchanged

The shared canonical architecture remains valid:

`Game → Set → Card → Print`

`Set` continues to represent the canonical numbering/collector family for compatibility with the other TCGs and existing APIs.

`CatalogRelease ↔ PrintRelease ↔ Print` adds commercial-release provenance beside that hierarchy.

This avoids a destructive reinterpretation of `Set` across all TCGs.

## 9. One Piece parser changes required

The official parser must:

1. accept `OPxx-xxx`;
2. accept `STxx-xxx`;
3. accept `EBxx-xxx`;
4. accept `P-xxx`;
5. accept `PRBxx-xxx`;
6. key logical Cards by normalized base collector number, never by name;
7. retain official series ID and series label;
8. deduplicate exact print identity across repeated series appearances;
9. preserve every series appearance in `PrintRelease`;
10. preserve raw official modal ID as source-print provenance.

## 10. Rebuild strategy

Because One Piece was only just bootstrapped and pricing has not yet been built on top of it, a controlled reconstruction is preferable to a complicated in-place split.

Before any destructive reconstruction:

- verify there are no One Piece `Price`, `PriceSnapshot` or other valuable dependent records;
- download and normalize the complete replacement payload before opening the DB transaction;
- apply the new schema first;
- rebuild only One Piece canonical rows and release links;
- never touch Pokémon, MTG, Yu-Gi-Oh! or Riftbound rows;
- rebuild search in a separate transaction;
- run Catalog Health and both One Piece audits afterwards.

## 11. Catalog Certified gate

One Piece must not be called Catalog Certified until all of the following are true:

- official parser coverage is effectively 100% for supported card families;
- no logical `Card` row contains multiple distinct base collector numbers;
- every canonical print has image and external identifier;
- official series provenance is retained through release links;
- no unexplained canonical print collisions remain;
- promos and PRB families are represented;
- release-date enrichment strategy is defined;
- coverage is compared against the official source and exceptions are explicit.

Only after this gate should pricing ingestion begin for One Piece.
