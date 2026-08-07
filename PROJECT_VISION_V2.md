# PROJECT VISION V2

Date: 2026-08-07
Status: Active product direction
Working branch: `catalog-v2`
Legacy snapshot branch: `legacy/dontripit-2026-08-07`

## 1. Product direction

This repository is evolving from the original Don’tRipIt software concept into a new, independent TCG data product. Don’tRipIt remains a separate ecommerce / Shopify business concept and must not define the scope of this application going forward.

The new application is a modern TCG catalog, market intelligence and portfolio platform.

Core promise:

> If a card printing exists, users should be able to find it, identify it precisely, understand its market value and track it in a portfolio.

The project should aim to become a trusted reference layer for TCG catalog identity and pricing, especially for European collectors.

## 2. Product principles

The project follows this sequence:

**CATALOG → IDENTITY → DATA → PRICE → TRUST → PORTFOLIO**

We do not add product complexity before the underlying data is trustworthy.

Primary principles:

1. Catalog accuracy before feature count.
2. Exact print identity before pricing.
3. Transparent pricing methodology instead of a black-box number.
4. Europe-first valuation while also supporting global valuation.
5. One canonical backend and database with game-specific experiences.
6. Source provenance must be preserved internally.
7. Market data sources must be modular and removable.
8. The pricing engine must survive loss of any individual source.
9. Avoid fake precision for illiquid cards.
10. Every important pricing result should include confidence and diagnostics.

## 3. Initial supported TCGs

The existing five-game foundation remains:

- One Piece Card Game
- Pokémon TCG
- Magic: The Gathering
- Yu-Gi-Oh!
- Riftbound

The long-term catalog ambition is every card and every meaningful printing from the beginning of each supported game.

Expansion to additional games should happen only after the current catalog quality is measurable and reliable.

## 4. Core canonical catalog

The existing canonical model is retained as the foundation:

- `Game`
- `Set`
- `Card`
- `Print`
- `PrintImage`
- `PrintIdentifier`

The critical distinction is:

- `Card` = conceptual card identity.
- `Print` = exact physical / released printing identity.

Pricing and portfolio holdings attach to `Print`, not merely to `Card`.

## 5. Catalog Model V2

`Print` identity should evolve beyond a generic foil flag and free-form variant string.

Target structured dimensions include where applicable:

- language
- region
- finish
- art variant
- frame variant
- promo type
- edition
- parallel
- stamp
- serialized status / serial number metadata
- rarity
- release attributes

Not all fields apply to every game.

Game-specific data should use a combination of canonical typed columns and extensible structured attributes such as JSONB rather than a huge sparse universal table.

Suggested domains:

- `card_attributes`
- `print_attributes`

## 6. Game-specific catalog attributes

Examples:

### One Piece

- color
- cost
- power
- counter
- life
- attribute
- traits
- card type
- leader properties
- promo / alt-art / manga / SP / anniversary classifications

### Pokémon

- HP
- type
- stage
- weakness
- resistance
- retreat
- regulation mark
- illustrator
- card category
- special treatment / rarity properties

### Magic: The Gathering

- colors
- color identity
- mana value
- type line
- oracle data
- legalities
- artist
- frame / treatment

### Yu-Gi-Oh!

- monster / spell / trap type
- attribute
- race / type
- archetype
- level / rank / link
- ATK / DEF
- pendulum properties
- edition
- rarity
- banlist properties

### Riftbound

Use official game-specific card dimensions as the connector and catalog mature.

## 7. Facet / filter engine

Filters should be configuration-driven per TCG rather than hardcoded into separate backend systems.

Target concept: `FacetDefinition`.

Possible fields:

- game
- key
- label
- data type
- filterable
- sortable
- display order
- supported operators

The API should eventually be able to describe the filter schema for a game to the frontend.

This enables rich game-specific hubs while retaining a single catalog platform.

## 8. Catalog completeness and health

Before major pricing work, build a permanent `Catalog Health` system.

For each game it should report at minimum:

- expected sets
- stored sets
- cards
- prints
- missing images
- missing collector numbers
- missing languages
- ambiguous variants
- duplicate identities
- external ID conflicts
- stale sources
- last successful ingest
- connector status
- search indexing status

Catalog completeness must be measurable rather than assumed.

A game should not be called complete merely because an external API import returned successfully.

## 9. Certification strategy

Audit and certify games progressively.

Recommended initial order:

1. One Piece
2. Pokémon
3. Yu-Gi-Oh!
4. Magic: The Gathering
5. Riftbound

One Piece is the recommended first deep certification target because domain knowledge can be used to catch subtle promo, language, variant and release errors.

Target quality before calling a catalog certified: approximately 99.9%+ known coverage with unresolved exceptions explicitly tracked.

## 10. Search experience

Search should resolve human collector intent, including:

- card names
- partial names
- collector numbers
- set codes
- aliases
- common abbreviations
- spelling mistakes
- characters
- artists
- rarity / variant terms
- promo classifications

PostgreSQL remains the source of truth and should be pushed as far as reasonable with full-text, trigram and indexed structured attributes before introducing an external search engine.

If a dedicated engine is added later, it is a derived search index, never the canonical database.

## 11. Pricing philosophy

The product should not claim to know an absolute “real price”.

Primary output concepts:

- **Europe Fair Market Value (EU FMV)**
- **Global Fair Market Value (Global FMV)**
- probable price range
- confidence score
- timestamp
- historical movement
- market diagnostics

The Europe-first value is especially important because US-centric values can materially misrepresent what European collectors can actually buy or sell cards for.

## 12. Market observations

External pricing information should be normalized into a canonical observation layer before any index calculation.

Target entity: `MarketObservation`.

Suggested fields:

- `print_id`
- `source`
- `observation_type`
- `external_id`
- native price
- native currency
- shipping
- landed price
- normalized EUR price
- condition
- language
- finish
- graded status
- grader
- grade
- quantity
- seller identifier or privacy-safe hash
- region
- occurrence timestamp
- capture timestamp
- match confidence
- raw source payload
- observation fingerprint

Observation types may include:

- `SALE`
- `LISTING`
- `MARKET_REFERENCE`
- `BUYLIST`

## 13. Market source adapters

All sources must be isolated behind adapters.

Conceptual structure:

```text
market_sources/
  cardmarket/
  cardtrader/
  ebay/
  tcgplayer/
  wallapop/
  ...
```

Adapters translate external source data into canonical `MarketObservation` records.

No pricing algorithm should directly depend on the external source response schema.

Sources can then be enabled, disabled or replaced without rebuilding the product.

## 14. Source tiers

Internally classify sources by operational and legal reliability.

### Tier A — Core

Sources suitable for production dependency with stable authorized/public access.

Expected early examples:

- Cardmarket public catalog / price guide
- CardTrader official API

### Tier B — Restricted / licensed

Sources that may be valuable but require explicit access, approval, commercial agreement or additional legal review.

### Tier C — Experimental

Sources useful for research or market comparison but not safe to make foundational until access and usage rights are resolved.

The FMV system must not collapse when a Tier B or Tier C source disappears.

## 15. Entity resolution

This is a critical platform component.

Every external marketplace product, listing or aggregate must map to the correct canonical `Print`.

Matching may use:

- game
- set
- collector number
- name
- language
- finish
- rarity
- variant
- image
- external IDs

Every match should have a `match_confidence`.

Low-confidence matches should be excluded from automated valuation and sent to a manual review queue.

Incorrect print identity is considered more damaging than missing pricing data.

## 16. Price normalization

Before index calculation, observations should be normalized across:

- currency
- shipping
- region
- language
- condition
- grading
- timestamp
- relevant tax / landed-cost considerations where applicable

The system should distinguish raw marketplace price from the economically comparable normalized price.

## 17. Deduplication

The same listing, sale or relisting must not receive repeated weight.

Every observation should support a stable fingerprint based on source identity and relevant external attributes.

Relisting and repeated capture detection should be part of ingestion quality controls.

## 18. Fair Market Value engine

The current simple mean / median price index is considered V0 and should not define the future product.

The new pricing engine should combine robust statistical methods and source-quality weighting.

Conceptual observation weight:

```text
w = type_weight
  × source_weight
  × recency_weight
  × region_weight
  × condition_weight
  × matching_weight
  × concentration_weight
```

Exact numerical weights must be calibrated through evidence and backtesting rather than chosen permanently by intuition.

Preferred initial statistical tools include:

- weighted median
- median absolute deviation (MAD)
- interquartile range (IQR)
- recency decay
- seller concentration adjustments
- market depth
- cross-source confirmation

Actual realized sales should generally carry more information than active asks.

Active listing floors must not be treated as market value by themselves.

## 19. Manipulation and anomaly resistance

The system should explicitly detect cases such as:

- temporary disappearance of cheap listings
- sudden ask-price jumps unsupported by sales
- seller concentration
- stale markets
- large bid / ask or listing dispersion
- low liquidity
- abrupt supply shocks
- cross-market divergence

A temporary change in visible listing floor should not automatically move FMV if historical sales and other market evidence remain stable.

Possible diagnostic labels:

- `ASK_ANOMALY`
- `LOW_LIQUIDITY`
- `SELLER_CONCENTRATION`
- `SUPPLY_SHOCK`
- `STALE_MARKET`
- `SOURCE_DIVERGENCE`

## 20. Confidence score

Every FMV should have an associated confidence level.

Potential inputs:

- number of sources
- number of recent sales
- number of listings
- seller diversity
- observation freshness
- price dispersion
- entity-match confidence
- source quality
- liquidity
- agreement across regions / markets

The UI should avoid presenting illiquid values with false precision.

Example output:

```text
EU Fair Market Value: €104
Probable range: €99–€109
Confidence: 92/100
```

## 21. Historical index

Target entity: `MarketIndexSnapshot`.

Suggested fields:

- `print_id`
- geographic scope
- fair value
- low estimate
- high estimate
- confidence score
- sales count
- listing count
- source count
- effective sample size
- calculated timestamp
- algorithm version
- diagnostics

Historical snapshots enable:

- 24H
- 7D
- 30D
- 90D
- 1Y
- ALL

Algorithm versioning is mandatory so historic outputs remain explainable when pricing logic changes.

## 22. Backtesting

Do not treat the pricing model as trustworthy until it is backtested.

Test across:

- high-liquidity cards
- low-liquidity cards
- cheap cards
- expensive cards
- promos
- vintage
- newly released cards
- hype-driven cards

For a historical date, calculate the FMV using only information available at that date, then compare the estimate with later realized market evidence.

Use those results to calibrate source and observation weights.

## 23. Public card page

Target card / print pages should eventually show:

- exact printing identity
- current EU FMV
- current Global FMV
- probable range
- confidence score
- price history
- market movement
- market depth
- source / evidence diagnostics
- catalog attributes
- images
- related prints

The experience should prioritize clarity over information overload.

## 24. Consumer users and portfolios

Consumer account functionality comes after catalog and pricing reliability.

Target entities:

- `User`
- `Portfolio`
- `Holding`

A holding may contain:

- print identity
- quantity
- condition
- language
- graded status
- grader
- grade
- purchase price
- purchase currency
- purchase date
- notes

Portfolio outputs include:

- current market value
- cost basis
- unrealized gain / loss
- ROI
- 7D / 30D / 1Y movement
- allocation by TCG
- largest holdings
- top gainers / losers

## 25. Watchlists and alerts

After portfolio:

- watchlists
- price threshold alerts
- material FMV-change alerts
- anomaly alerts

These should be built on the same canonical pricing and identity system rather than separate logic.

## 26. Scanner

Card scanning is a later feature.

It should not launch until exact print resolution is sufficiently reliable.

The scanner should identify a likely card and then resolve or ask the user to confirm the exact printing when multiple variants exist.

A scanner that finds the card name but assigns the wrong printing creates unacceptable pricing errors.

## 27. Web / mobile strategy

Initial product should remain a fast responsive web application and may become a PWA.

Do not simultaneously build independent native iOS, Android and web clients before product-market fit.

## 28. API strategy

The existing API-key, plan, quota and versioning infrastructure can become a future commercial advantage.

Potential public / paid endpoints:

- cards
- sets
- prints
- market value
- price history
- market depth
- catalog health / metadata where appropriate

Potential future customers:

- TCG shops
- portfolio apps
- inventory tools
- bots
- analytics services
- marketplaces

## 29. Monetization principles

The public catalog should remain broadly accessible.

Potential premium areas later include:

- advanced portfolio analytics
- long historical windows
- sophisticated alerts
- unlimited watchlists
- bulk scanner workflows
- exports
- professional market tooling
- API access

Monetization must not be allowed to distort catalog or price methodology.

## 30. Scope exclusions for V2

Do not reintroduce unrelated marketplace complexity during the core build.

Not initial priorities:

- peer-to-peer marketplace
- escrow
- payments
- buyer/seller disputes
- shipping logistics
- social feed
- tournaments
- event management
- chat
- general TCG news

Don’tRipIt ecommerce activity is separate from this product direction.

## 31. Existing infrastructure to retain

Keep and evolve where practical:

- Flask backend
- PostgreSQL / Neon
- SQLAlchemy
- Alembic
- Next.js frontend
- Docker
- API v1 foundations
- API key / plan / quota infrastructure
- OpenAPI / Swagger
- SDK foundations
- canonical Game / Set / Card / Print architecture
- image and external-ID models
- provenance models
- ingest framework
- existing TCG connectors
- search foundation
- pricing storage / snapshot concepts
- test suite

## 32. Existing infrastructure to refactor

Refactor progressively:

- print variant / finish representation
- game-specific metadata
- facets / filtering
- ingest strictness
- pipeline observability
- incremental indexing
- source health reporting
- price normalization

## 33. Components considered new or replaceable

New or substantially redesigned:

- Catalog Health system
- structured facet engine
- market observation model
- source adapter framework
- source quality tiers
- entity resolution engine
- confidence scoring
- anomaly detection
- FMV methodology
- pricing backtesting
- consumer authentication
- portfolios
- watchlists
- alerts

The current simple price-index calculation should be treated as replaceable V0 logic.

## 34. Immediate execution roadmap

### Phase 0 — Repository safety

- Preserve legacy state.
- Develop only on `catalog-v2` until reviewed.
- Do not destructively rewrite `main`.

### Phase 1 — Restore infrastructure

- Run backend and frontend tests.
- Validate builds, dependencies and migrations.
- Restore scheduled ingestion reliability.
- Fix partial-success reporting.
- Fix MTG / Scryfall incremental failure.
- Fix Riftbound fallback configuration / behavior.
- Add One Piece to the daily refresh path.
- Replace scheduled Pokémon `base1` behavior with a real all-set / incremental strategy.
- Ensure mutations trigger correct search indexing.

### Phase 2 — Catalog Health

- Build read-only DB audit tooling.
- Produce exact counts and completeness indicators per TCG.
- Surface connector and freshness health.

### Phase 3 — One Piece certification

- Audit every known release category.
- Resolve variants, promos, languages, identifiers and image gaps.
- Reach measurable high-confidence catalog coverage.

### Phase 4 — Catalog Model V2 + facets

- Add structured variants and attributes.
- Introduce game-specific facet definitions.
- Build the first “monster filter” explorer for One Piece.

### Phase 5 — Pricing ingestion

- Integrate Cardmarket public price data.
- Integrate CardTrader official marketplace data.
- Add normalized `MarketObservation` storage.
- Add source health and dedupe.

### Phase 6 — Entity resolution

- Map every external market entity to exact canonical print identity.
- Add confidence and manual-review queue.

### Phase 7 — FMV V1

- Build EU and Global Fair Market Value.
- Add range and confidence.
- Add anomaly detection and market-depth signals.

### Phase 8 — Backtesting and calibration

- Measure model quality against subsequent market evidence.
- Tune weights and rules.
- Version the pricing algorithm.

### Phase 9 — Public pricing UI

- Build trusted print detail pages with transparent price history and evidence.

### Phase 10 — Remaining TCG certification

- Pokémon
- Yu-Gi-Oh!
- Magic: The Gathering
- Riftbound

### Phase 11 — Portfolio product

- Consumer users
- holdings
- portfolio valuation
- P&L
- watchlists
- alerts

### Phase 12 — Later leverage

- scanner
- PWA enhancements
- commercial data API
- professional analytics

## 35. Definition of success

The project should not be judged primarily by feature count.

Its strongest long-term moat should become:

**CANONICAL IDENTITY + HISTORICAL MARKET DATA + PRICING METHODOLOGY + TRUST**

The product is successful when collectors can use it to answer three questions with unusually high confidence:

1. Exactly which printing is this?
2. What is it realistically worth in my market?
3. What is my collection actually worth today?
