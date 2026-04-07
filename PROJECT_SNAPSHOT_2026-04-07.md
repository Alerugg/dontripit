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
- Next slice: set detail page

## Rules
- Do not merge old broken WIP frontend branches blindly
- Work in small vertical slices
- Run backend tests after each meaningful change
