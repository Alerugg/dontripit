# Don'tRipIt — Pending roadmap

This file records future work without changing the active backend hardening scope.

## Active / do not reorder

1. Finish Search V2 hardening and production recertification.
   - Preserve exact identity/ranking.
   - Keep Cardmarket price hot path non-blocking.
   - Certify canonical probes and multi-PoP stability.
2. Clean the audited Yu-Gi-Oh primary-image duplicates.
   - Remove only the 6 known excess primary rows across the 5 audited prints.
   - Re-audit to exactly 0 duplicates.
   - Add a database protection/constraint so duplicate primaries cannot return.
3. Finish Cardmarket engines and remaining Cardmarket coverage/quality work.
   - Treat Cardmarket as the primary pricing engine until this block is certified.

## Last-priority product/data improvements — only after Cardmarket engines are green

### A. Marketing home separated from the app

- `dontripit.com` should become a polished explanatory/marketing home instead of opening directly into search.
- The home should explain clearly what Don'tRipIt does, supported games, pricing/collection value proposition, discovery/search capabilities and why the product is useful.
- Primary CTA: **Open App**.
- The CTA should enter the actual application/search experience, following the separation used by products such as Collectr.
- This is a frontend/product-design task and must not be mixed into backend hardening or the current production recertification.
- Before implementation, review the complete app flow and define the cleanest routing/domain structure (for example `/app`, `app.dontripit.com`, or another deliberate structure) without breaking existing deep links, SEO or auth.

### B. Frontend product pass

- Give the application frontend another complete UX/UI pass after the backend/data foundation is stable.
- Focus particularly on search-result cards, card detail, pricing presentation, portfolio/collection flows, mobile usability and visual hierarchy.
- Do not start this while current Search/Cardmarket work is still active.

### C. Multi-market price data: TCGplayer + StockX

- Research and design additional price-source connectors for **TCGplayer** and **StockX** after Cardmarket engines are complete.
- Do not blend prices blindly. Keep source-specific identity, currency, geography, condition, language, product type and timestamp provenance.
- Build canonical crosswalks from Don'tRipIt print/product identity to each marketplace before exposing prices.
- Store source snapshots independently and expose them as comparable market views.
- Define freshness, retries, rate limits, source availability and legal/API constraints before ingestion.
- Only create aggregate/benchmark prices after source-level coverage and identity confidence are measurable.
- Initial priority: feasibility + identity mapping + coverage audit; ingestion and UI come afterwards.

## Scope guard

The items in the last-priority section are deliberately parked. They must not delay or alter the current Search V2 / Cloud Run / Cardmarket / YGO-data hardening sequence.
