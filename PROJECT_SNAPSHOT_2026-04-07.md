# PROJECT SNAPSHOT — 2026-04-07

## Baseline status
- Branch validated from: main
- Tag: baseline-green-2026-04-07
- Backend test status: 236 passed
- Docker status: backend/frontend build OK

## Important fixes confirmed
- One Piece remote fallback issue was caused by legacy env contamination:
  - `.env` had `ONEPIECE_PUNKRECORDS_BASE_URL=https://raw.githubusercontent.com/buhbbl/punk-records/main`
  - fixed by clearing it:
    - `ONEPIECE_PUNKRECORDS_BASE_URL=`
- Current remote fallback behavior is healthy again.
- Main branch is the trusted backend baseline.

## Current working branch
- feat/catalog-frontend-recovery

## Frontend recovery progress
- Slice 1 recovered: game explorer
- Slice 2 recovered: sets directory
- Slice 3 recovered: set detail page

## Current recovery status
- Core catalog frontend recovery is back in place from the pre-recovery WIP
- Main backend remains green after each frontend slice recovery
- Routes verified with HTTP 200:
  - /games/pokemon
  - /games/yugioh
  - /games/onepiece
  - /games/mtg
  - /games/riftbound
  - /games/pokemon/sets
  - /games/yugioh/sets
  - /games/onepiece/sets
  - /games/mtg/sets
  - /games/riftbound/sets
  - /games/pokemon/sets/scarlet-violet
  - /games/yugioh/sets/lob
  - /games/onepiece/sets/op-01
  - /games/mtg/sets/lea
  - /games/riftbound/sets/ogn

## Rules
- Do not merge old broken WIP frontend branches blindly
- Work in small vertical slices
- Run backend tests after each meaningful change

## Next step
- Do focused frontend smoke validation and then push/open PR from feat/catalog-frontend-recovery into main

## Post-recovery integration fix (2026-04-07)
- Added missing frontend catalog adapter routes for `news`, `sets`, and `set-detail` under `frontend/app/api/catalog/*` so recovered game hub and set views no longer depend on missing internal endpoints.
- Hardened catalog card linking to avoid routing print-only entities to `/cards/[cardId]` when `card_id` is absent.
