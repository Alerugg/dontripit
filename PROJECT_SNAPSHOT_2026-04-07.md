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

## Post-recovery catalog hardening (2026-04-07, branch `work`)
- Backend `/api/v1/sets` now returns `card_count` using `COUNT(DISTINCT prints.card_id)` per set, and falls back to set code when set name is blank.
- Frontend BFF `/api/catalog/sets` now normalizes display names safely:
  - preserves canonical names when valid,
  - avoids exposing pure numeric names as final display labels when a better set code exists,
  - preserves payload shape (`id`, `code`, `set_code`, `name`, `title`, `game`, `game_slug`, `card_count`).
- Card detail page (`/games/[slug]/cards/[cardId]`) now validates route game slug against fetched card game slug before rendering, preventing cross-game mismatches in card ↔ variants binding.
- Regression test added: `backend/tests/test_catalog_endpoints.py::test_v1_sets_includes_non_zero_card_count_when_prints_exist`.
- Local environment limitation in this session: Docker CLI unavailable (`docker: command not found`), so compose-based smoke checks/curls could not be completed against a running stack.

## Data-quality patch (2026-04-07, branch `fix/catalog-data-quality`)
- `/api/catalog/sets` fallback no longer depends only on a non-empty free-text query:
  - when `q` is empty and set rows look degraded (`card_count=0`, numeric-only labels/codes), the BFF now does targeted per-set `/api/v1/search` lookups using code/id and merges the best matched set metadata.
  - this keeps response compatibility (`id`, `code`, `set_code`, `name`, `title`, `game`, `game_slug`, `card_count`) while reducing raw numeric labels in One Piece when a better label exists upstream.
- `/api/catalog/news` now returns a safe default item for unknown/unsupported slugs instead of `items: []`, so hubs keep a non-dead news block while dynamic provider work is pending.
- `/api/catalog/cards/[id]` now falls back to the first filtered print image when the card-level image is missing, preserving detail-page binding after strict print filtering by `card_id`.
- Session limitation: Docker CLI is not installed in this environment, so compose build/test and live `curl` verifications against `localhost:3000` could not be executed here.

## Data-quality verification follow-up (2026-04-07, branch `work`)
- Frontend BFF `/api/catalog/sets` now applies a stronger fallback match strategy when upstream `/api/v1/sets` rows are degraded (numeric-only names/codes or `card_count=0`):
  - keeps exact id/code matches as first priority,
  - but if no exact match exists and source row looks degraded, it selects the best `/api/v1/search` set candidate that exposes non-numeric display metadata.
- This specifically hardens One Piece set-label recovery for legacy numeric pack ids (for example `569301`) so the BFF can promote canonical set labels/codes when search has better metadata.
- Backend test run in this session: `pytest -q` => `237 passed`.
- Session limitation unchanged: Docker CLI unavailable (`docker: command not found`), so required compose + localhost:3000 curl smoke checks could not be executed in this container.

## One Piece canonical set-label patch (2026-04-07, branch `work`)
- Root cause found: backend `/api/v1/sets` could still emit legacy One Piece numeric pack ids (for example `569010`) in both `code` and `name`; when that reached frontend `/api/catalog/sets`, UI fallback logic still degraded to labels like `Set #1188`.
- Minimal backend fix in `list_sets`:
  - include a per-set `sample_collector_number` from `prints`,
  - for `game=onepiece`, if set `code`/`name` is numeric-only, derive canonical commercial code from collector prefix (`OP01-*`, `EB01-*`, `ST10-*` → `op-01`, `eb-01`, `st-10`),
  - normalize response row to expose canonical `code` and non-numeric `name` (`ST-10` style) before returning payload.
- Targeted regression test added:
  - `backend/tests/test_catalog_endpoints.py::test_v1_sets_onepiece_derives_commercial_set_code_from_collectors_for_numeric_legacy_sets`
  - verifies `/api/v1/sets?game=onepiece&q=569010` returns canonicalized `code=st-10` and `name=ST-10` instead of numeric legacy metadata.
- Validation in this session:
  - targeted local pytest passed,
  - docker/compose and localhost curl checks remain blocked because Docker CLI is unavailable in this environment.

## One Piece canonical disambiguation hotfix (2026-04-07, branch `work`)
- Root cause identified in frontend BFF `/api/catalog/sets` fallback heuristics:
  - when `/api/v1/sets` returned degraded numeric-only One Piece labels/codes, the fallback selector accepted the first non-numeric `/api/v1/search` candidate whenever exact id/code match was missing;
  - this could over-canonicalize multiple distinct set ids into the same label/code (for example repeated `EB-01`).
- Minimal/localized fix:
  - extracted set-normalization helpers to `frontend/lib/catalog/normalizers/sets.js` (logic-only module);
  - tightened `selectBestSearchFallback` to apply canonical fallback only when it is **unequivocal**:
    - exact id/code match still wins;
    - degraded numeric source rows now accept heuristic fallback only if there is exactly one qualified non-numeric candidate;
    - ambiguous candidates return `null` so route falls back to neutral label (`Set #<id>`) instead of collapsing to one canonical code.
- Added focused tests in `frontend/tests/catalogSetsRoute.test.mjs`:
  - unique canonical fallback is applied when unambiguous;
  - ambiguous fallback does not force one canonical code;
  - multiple degraded ids remain distinct instead of collapsing to a shared code.
- Verification in this session:
  - `backend pytest -q` => `238 passed`;
  - `node --test frontend/tests/catalogSetsRoute.test.mjs` => `3 passed`;
  - docker-compose and live localhost curl checks could not be executed because Docker CLI is unavailable in this container (`docker: command not found`).

## One Piece set identity anti-collapse patch (2026-04-07, branch `work`)
- Root cause confirmed in two layers:
  - Backend `/api/v1/sets` One Piece normalization was deriving canonical commercial codes from a single sample collector number (`ST10-001` => `st-10` style), which can over-assign canon for legacy numeric set ids that are not proven equivalent.
  - Frontend `/api/catalog/sets` fallback matcher could accept heuristic search candidates for degraded numeric rows, which risks collapsing distinct set ids to repeated canonical codes.
- Fix implemented with minimal/localized scope:
  - Backend now **preserves original set `code` identity** for One Piece and only applies a neutral display fallback (`Set #<id>`) when both code/name are numeric-like.
  - Frontend set fallback now only applies canonical mapping on **unequivocal** matches (exact id, or exact non-numeric code); degraded numeric sets no longer get heuristic canonical assignment.
  - Frontend normalization guarantees neutral non-numeric title/name fallback (`Set #<id>`) when display label is numeric-like.
- Added/updated regression coverage:
  - backend: `test_v1_sets_onepiece_keeps_unique_numeric_codes_and_uses_neutral_name_fallback`
  - frontend: strengthened `catalogSetsRoute` tests for unequivocal-only canonical mapping, ambiguity handling, distinct-id preservation, and payload shape.
- Validation executed in this session:
  - targeted backend test passed,
  - frontend normalizer tests passed (`4` tests),
  - full local `pytest -q` in this container reports `235 passed, 3 failed` due pre-existing missing Riftbound fixture files under `data/fixtures`.
  - required docker compose + localhost curl validation remains blocked here because Docker CLI is unavailable (`docker: command not found`) and no local server is running on `localhost:3000`.

## One Piece source set-name mapping follow-up (2026-04-07, branch `work`)
- Root cause confirmed in backend `/api/v1/sets`:
  - legacy One Piece set rows with numeric code/name were only receiving neutral fallback (`Set #<id>`),
  - endpoint had no safe bridge from those legacy ids to canonical set labels even when print collector numbers clearly identified a commercial set family/code.
- Minimal backend fix in `backend/app/routes/catalog.py`:
  - added a safe collector-prefix extractor (`OPxx`, `STxx`, `EBxx`) and set-level inference,
  - applies canonical One Piece **name** mapping only when all available collectors in that set imply exactly one commercial code and an existing canonical set row exists for that code,
  - preserves original set `code` to avoid collapsing distinct legacy set identities.
- Regression tests added in `backend/tests/test_catalog_endpoints.py`:
  - maps neutral numeric One Piece set name to canonical label when mapping is unequivocal,
  - preserves distinct legacy numeric codes (no collapse into one canonical code),
  - keeps neutral label when no safe mapping exists,
  - existing Pokemon set endpoint coverage remains green.

## One Piece legacy source-mapping hardening (2026-04-07, branch `work`)
- Root cause in backend `/api/v1/sets` One Piece mapper:
  - canonical label promotion could happen when only *some* collectors in a legacy set exposed a commercial prefix, because non-parseable collectors were ignored as missing evidence.
  - canonical correspondence lookup also assumed any returned `sets.code` match was safe, without forcing a unique canonical row per commercial code.
- Minimal backend safety hardening in `backend/app/routes/catalog.py`:
  - canonical promotion now requires **full collector evidence**: every non-empty collector in that legacy set must parse to a commercial prefix (`OP/ST/EB`) and all must agree on one code.
  - canonical correspondence is accepted only when there is exactly one One Piece canonical row for that commercial `sets.code`.
  - fallback remains neutral and stable (`Set #<id>`) when evidence is ambiguous or incomplete.
  - legacy `set.code` identity remains unchanged to prevent cross-id collapse.
- Added focused tests in `backend/tests/test_catalog_endpoints.py`:
  - ambiguous collectors (`OP` + `EB`) stay neutral,
  - mixed evidence (one parseable + one non-parseable collector) stays neutral.
- Validation in this container:
  - `pytest -q backend/tests/test_catalog_endpoints.py` => `14 passed`.
  - `pytest -q` => `239 passed, 3 failed` (pre-existing Riftbound fixture files missing under `data/fixtures`).
  - Docker/compose and live curl validations remain blocked in this environment (`docker: command not found`).

## One Piece legacy set evidence sweep (IDs 1188-1191) — 2026-04-07

Scope reviewed:
- `backend/app/routes/catalog.py` (`/api/v1/sets` One Piece legacy name promotion path).
- `backend/app/ingest/connectors/onepiece.py` (remote normalization, pack-id/commercial parsing).
- `backend/app/models.py` (`sets`, `prints`, `print_identifiers` available source fields).

Findings from backend logic (safe-mapping constraints):
- Canonical promotion for numeric-like One Piece set labels is intentionally gated behind **full collector evidence**:
  - all non-empty collectors in a legacy set must parse as `OP/ST/EB` commercial prefixes,
  - all parsed collectors must agree on one single commercial code,
  - and that commercial code must exist exactly once as a canonical One Piece `sets.code` row.
- If any collector is non-parseable, mixed across multiple families, or canonical target is not unique, response stays neutral (`Set #<id>`).
- `sets.code` identity is preserved; no cross-id collapse is performed.

Set IDs requested (`1188`, `1189`, `1190`, `1191`):
- In this workspace there is no running local API/database snapshot exposing those concrete rows, so per-id collector lists cannot be extracted here.
- Given current production-safe policy, these IDs should remain neutral unless their own `prints.collector_number` evidence is unanimous + canonical-target-unique as above.

Action taken in backend code:
- No mapping changes implemented (no inequívoca per-id evidence available in this environment).
- Existing safe neutral fallback behavior is preserved.

## One Piece canonical vs legacy-mixed classification patch (2026-04-07, branch `work`)
- Root cause:
  - `/api/v1/sets` correctly avoided aggressive canonical remapping for numeric One Piece legacy rows, but still exposed mixed/ambiguous legacy containers in the main listing with neutral labels, which polluted the default set directory surface.
- Minimal backend change:
  - Added explicit One Piece set classification in `/api/v1/sets`:
    - `canonical` for safe canonical identities (native canonical code or unequivocal numeric->canonical inference),
    - `legacy_mixed_ambiguous` for legacy numeric/mixed containers that are not safe to canonize.
  - Default `/api/v1/sets?game=onepiece` now excludes `legacy_mixed_ambiguous` rows unless explicitly requested with `include_legacy_ambiguous=true` (or when directly searched via `q`), preventing ids like `1188-1191` from contaminating principal listing behavior.
  - Kept existing safety guarantees:
    - no invented mappings,
    - no forced canonical code collapse,
    - `card_count` query untouched.
- Tests added:
  - onepiece default listing excludes mixed ambiguous legacy rows,
  - onepiece explicit include path returns legacy mixed row marked as `legacy_mixed_ambiguous`,
  - canonical onepiece mapping and ambiguity-neutral behavior remain validated by existing tests.
- Validation in this container:
  - targeted backend tests pass;
  - required Docker compose + backend-in-container pytest + localhost curl checks cannot run here because Docker CLI is unavailable (`docker: command not found`).

## One Piece card detail variant-binding hardening (2026-04-08, branch `fix/onepiece-card-variant-binding`)
- Root cause observed in frontend binding layer:
  - card detail BFF (`/api/catalog/cards/[id]`) trusted upstream card identity and only filtered variants by string-equal `card_id`, which allowed edge mismatches when id formats differ (string/number), and did not explicitly fail when upstream card id diverged from requested route id.
  - catalog card link resolution could prioritize `item.card_id` even for `type='card'`, making master-card navigation vulnerable when payloads include residual `card_id` metadata.
- Minimal/localized fixes:
  - `frontend/app/api/catalog/cards/[id]/route.js`
    - enforce requested card identity (`payload.id` must match route `id`, with numeric-safe comparison) and return 404 on mismatch;
    - keep payload shape compatible while filtering `prints` with numeric-safe `card_id` matching;
    - keep `sets` constrained to filtered prints only.
  - `frontend/components/catalog/CatalogCard.js`
    - card links now use `item.id` for `type='card'` (master identity), using `item.card_id` only for non-card entities.
  - Added targeted regression tests (`frontend/tests/cardDetailBinding.test.mjs`) to lock both safeguards.
- Validation in this container:
  - `node --test frontend/tests/cardDetailBinding.test.mjs frontend/tests/bffRoutesSyntax.test.mjs` passed.
  - required docker compose / backend pytest-in-container / localhost curl checks are blocked in this environment (`docker: command not found`, no local server on `localhost:3000`).

## One Piece manual smoke hardening follow-up (2026-04-08, branch `work`)
- Root cause found in real navigation/detail glue:
  - `/games/[slug]/cards/[cardId]` rendered card detail labels/links from payload `card.game` directly, so UI labels could show inconsistent slug-like text and links were vulnerable when payload game fields were incomplete.
  - card detail variants were filtered correctly but not deterministically ordered, leading to unstable/mixed variant presentation in detail.
- Minimal fixes applied:
  - `frontend/components/cards/CardDetailLayout.js`
    - now resolves game context from route slug fallback first (`routeGameSlug`), then payload (`game_slug`/`game`), normalizes it, and renders canonical game label from `getGameConfig`.
  - `frontend/app/games/[slug]/cards/[cardId]/page.js`
    - now passes `routeGameSlug={params.slug}` into `CardDetailLayout`.
  - `frontend/app/api/catalog/cards/[id]/route.js`
    - keeps existing strict `card_id` filtering and adds stable variant ordering by collector number/set/finish/variant/id.
  - `frontend/tests/cardDetailBinding.test.mjs`
    - adds targeted assertions for the new detail hardening (`routeGameSlug` fallback + canonical label) and print sort hook.
- Validation in this container:
  - `cd backend && pytest -q` => `244 passed`.
  - `cd frontend && node --test tests/cardDetailBinding.test.mjs` => `3 passed`.
  - lightweight route smoke via `next dev` + curl (HTTP 200):
    - `/games/onepiece`
    - `/games/onepiece/sets`
    - `/games/onepiece/sets/op01`
    - `/games/onepiece/cards/1`
  - required docker compose commands could not run here because Docker CLI is unavailable (`docker: command not found`).

## One Piece QA functional pass (2026-04-08, branch `work`)
- Objetivo ejecutado: QA funcional focalizado en One Piece (`/games/onepiece`, sets, set detail, card detail y navegación hub→set→card) sin refactor masivo.
- Resultado: **no se reprodujo bug funcional nuevo** en las rutas objetivo dentro de este entorno; no se aplicaron cambios de lógica/producto para evitar fixes inventados o duplicados de `main`.
- Validación ejecutada:
  - `docker compose up -d --build` → bloqueado por entorno (`docker: command not found`).
  - `cd backend && pytest -q` → `244 passed`.
  - Smoke runtime en frontend local (`next dev`) + `curl`:
    - `/games/onepiece` → 200
    - `/games/onepiece/sets` → 200
    - `/games/onepiece/sets/op-01` → 200
    - `/games/onepiece/cards/40860?q=luffy&type=singles` → 200
    - `/games/onepiece/cards/1` → 200
    - `/games/onepiece/cards/2` → 200
    - `/games/onepiece/cards/3` → 200
- Nota de alcance: por falta de Docker en este contenedor no fue posible levantar stack completo compose ni validar la navegación visual contra backend containerizado; se dejó evidencia explícita del límite para no reportar falsos positivos.
