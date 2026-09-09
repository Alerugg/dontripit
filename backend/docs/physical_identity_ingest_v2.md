# Physical Identity Ingest V2

## Why this exists

Don’tRipIt must not treat a card name, a marketplace metacard, or a marketplace product page as the canonical physical identity of a collectible.

A card concept can have many releases/reprints. A release can have multiple physical variants (rarity, foil treatment, first edition, reverse holo, parallel art, stamped promo, errata, language/region, etc.). A marketplace can then either split those physical variants into multiple product pages or group several physical variants behind one product page and expose some differences only as offer attributes.

The identity engine therefore has to model source facts first and market mappings second.

## Coverage target

Cardmarket is the coverage universe for the current European pricing product.

For each supported game, every Cardmarket single-card `idProduct` must end in exactly one of these explicit classes:

- `EXACT_ONE_PHYSICAL`: one Cardmarket product identifies one canonical physical Print.
- `GROUPED_PHYSICAL`: one Cardmarket product intentionally spans multiple canonical physical Prints because Cardmarket treats one or more dimensions as offer attributes (for example language and, in some games, regular foilness).
- `ALIAS_SAME_PHYSICAL`: multiple Cardmarket product IDs are proven aliases/recreated pages for the same physical Print identity.
- `AMBIGUOUS`: evidence is insufficient or contradictory; no automatic identity write.
- `OUT_OF_SCOPE`: not a real single-card product for the game or otherwise explicitly excluded with evidence.

The primary SLO is:

`accounted_cardmarket_products / current_cardmarket_single_products >= 99.0%`

where accounted means `EXACT_ONE_PHYSICAL + GROUPED_PHYSICAL + ALIAS_SAME_PHYSICAL + OUT_OF_SCOPE`.

`AMBIGUOUS` must remain below 1.0% of the current Cardmarket singles universe.

Additional gates:

- cross-game mappings = 0
- wrong EXACT mappings in gold/holdout = 0
- unexplained product IDs = 0
- source rows silently discarded because a normalized schema lacks a dimension = 0
- idempotent second pass = 0 identity writes
- current-price projection coverage >= 99% of identity-accounted products for which Cardmarket exposes a compatible current price

The 99% target is a coverage target, not permission to guess. Automatic `EXACT` still requires zero observed wrong mappings in certification.

## Identity hierarchy

### 1. Card concept

Examples: `Blue-Eyes White Dragon`, `Charizard`, `Lightning Bolt`, `Monkey.D.Luffy`.

This groups rules/gameplay identity and reprints. It is not a market product.

### 2. Release / printing family

The official set, starter deck, promo release, tournament release, reprint product, etc.

### 3. Physical Print

A real collectible identity. The normalized fingerprint can use a different subset of dimensions by game, but the engine must preserve all known dimensions.

Core normalized dimensions:

- `game`
- `card_concept`
- `release`
- `collector_number`
- `language`
- `region`
- `rarity`
- `finish`
- `edition`
- `version`
- `stamp`
- `treatment`
- `artwork`
- `reprint_family`
- `errata_revision`
- `promo_type`

Unknown is different from default. Missing evidence must be represented as unknown rather than silently collapsed into a generic value.

### 4. Market product

Cardmarket `idProduct`, CardTrader Blueprint, TCGplayer product ID, etc. Market products map to one or more physical Prints with a typed relationship and evidence.

## Source strategy by game

### Magic: The Gathering

Primary physical identity: Scryfall paper printing (`id`) and release/collector identity.

Important dimensions to preserve from Scryfall include `cardmarket_id`, `set`, `collector_number`, `lang`, `rarity`, `finishes`, variation/treatment fields, promo types, frame effects, border, security stamp and artwork-related identifiers when available.

`cardmarket_id` is trusted direct market evidence when present and structurally consistent.

Do not reduce Scryfall `finishes` to a single boolean. Regular foil, nonfoil and etched/special treatment semantics must remain explicit source facts. Cardmarket can model etched cards as separate versions while treating other finish differences as listing attributes or separate pages depending on the set.

### Pokémon

Primary canonical source: TCGdex plus official-set evidence where available.

Preserve at least:

- set and `localId`
- rarity
- normal/reverse/holo availability
- first edition
- promo/stamped/reprint information when exposed
- language/region family

TCGdex's current marketplace-to-variant mapping must not be assumed exact where the source itself marks that area as evolving.

CardTrader Blueprint data is a preferred cross-market identity bridge when available because `card_market_ids[]` can connect one blueprint to one or several Cardmarket IDs.

### Yu-Gi-Oh!

Primary canonical evidence:

- Konami official release membership
- YGOPRODeck card ID and set rows (`set_name`, `set_code`, `set_rarity`, `set_rarity_code`)
- artwork IDs/images where they distinguish artwork

Preserve:

- release/set code including regional code form
- collector/card number
- rarity
- edition when evidenced
- Cardmarket version (`V.1`, `V.2`, etc.) when a source exposes it
- artwork
- language/region
- special treatment/version markers

A Cardmarket metacard is a concept-level grouping signal only. It is not proof that two product IDs are the same physical print.

CardTrader Blueprint `card_market_ids[]` is a preferred bridge for Cardmarket version/alias resolution.

### One Piece Card Game

Primary canonical evidence: Bandai official card list plus the existing canonical One Piece source.

Preserve:

- card code (`OPxx`, `STxx`, `EBxx`, `P-xxx`, etc.)
- release/product family
- rarity
- normal vs Parallel
- alternate artwork/version
- reprint family (`PRB`, promo reprint, etc.)
- errata/revision
- region/language

Cardmarket explicitly supports versioned One Piece products such as `V.1` and `V.2`; version must therefore be first-class evidence rather than an opaque suffix.

## CardTrader bridge

CardTrader is an identity bridge, not the price authority.

The API Blueprint object exposes:

- blueprint ID
- name
- version
- expansion
- editable properties
- Scryfall ID where available
- `card_market_ids[]`
- TCGplayer ID where available

This makes it useful for proving either:

1. several Cardmarket IDs belong to one Blueprint (`ALIAS_SAME_PHYSICAL` / market grouping evidence), or
2. superficially similar Cardmarket IDs belong to different Blueprints and must remain separate physical variants.

The connector must require `CARDTRADER_API_TOKEN`, cache expansion and blueprint exports, respect API rate limits, and persist source provenance. Missing token must fail closed and must never downgrade to fuzzy auto-writes.

## Required schema direction

Do not add dozens of game-specific nullable columns directly to `prints` before the audit is complete.

Introduce a structured identity layer that can coexist with current `Print` rows:

### `physical_identity_facts`

Suggested fields:

- `id`
- `print_id`
- `dimension` (rarity, finish, edition, version, stamp, treatment, artwork, reprint_family, errata_revision, region, etc.)
- `normalized_value`
- `raw_value`
- `source`
- `source_external_id`
- `confidence`
- `evidence`
- `first_seen_at`
- `last_seen_at`

Unique source fact identity: `(print_id, dimension, source, source_external_id, normalized_value)`.

### Market relationship classification

Extend the external-product mapping semantics so a relationship can be typed as:

- exact_one_physical
- grouped_physical
- alias_same_physical
- candidate
- ambiguous
- out_of_scope

The existing `ExternalCatalogProduct` remains source-owned. Never make Cardmarket `idProduct` the canonical Print primary key.

## Ingest V2 pipeline

1. **Raw source snapshot**
   - fetch authoritative/current source data
   - checksum it
   - preserve source version and fetch timestamp
   - never discard identity-bearing fields before normalization

2. **Source-specific normalization**
   - normalize dimensions independently
   - preserve raw values and provenance
   - distinguish `unknown`, `not_applicable`, and explicit default values

3. **Canonical concept/release reconciliation**
   - card concept
   - official release
   - collector identity

4. **Physical identity materialization**
   - construct physical fingerprint from source-backed dimensions
   - no fuzzy write when a dimension changes physical identity

5. **Market crosswalk**
   Evidence ladder:
   - trusted direct source marketplace ID (e.g. Scryfall `cardmarket_id`)
   - trusted third-party explicit crosswalk (`card_market_ids[]`)
   - official release + collector + rarity/version/artwork exact match
   - deterministic alias/group rule validated on holdout
   - otherwise ambiguous

6. **Price projection**
   - ingest Cardmarket price guide at external product level first
   - project only through certified market relationships
   - preserve market price dimensions separately from physical dimensions

7. **Certification and observability**
   - per-game product coverage
   - ambiguous reasons
   - missing source dimensions
   - alias/group cardinality
   - gold precision
   - price projection coverage
   - idempotence

## Rollout order

1. Baseline current identity coverage against Cardmarket for all four games.
2. Fix source normalization loss (especially MTG finishes/treatments and Pokémon variants).
3. Add CardTrader read-only connector and explicit crosswalk audit.
4. Rebuild YGO identity using Konami + YGOPRODeck + CardTrader + Cardmarket.
5. Rebuild Pokémon variant identity.
6. Re-certify MTG using Scryfall direct IDs and finish/treatment facts.
7. Rebuild One Piece parallel/reprint/version identity.
8. Apply only certified exact/group/alias relationships.
9. Reproject prices.
10. Require >=99.0% Cardmarket product-accounted coverage per game before declaring the identity engine P0 green.
