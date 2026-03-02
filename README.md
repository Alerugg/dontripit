# Run from scratch (Codespaces)

## A) Run with Docker (recomendado)

1) `cp .env.example .env`
2) `docker compose up --build`
3) `docker compose exec backend alembic upgrade head`
4) `docker compose exec backend python -m app.scripts.seed`
5) (opcional) `docker compose exec backend python -m app.scripts.seed_catalog`
6) Ingest inicial con fixtures locales:
   - `docker compose exec backend python -m app.ingest.run fixture_local --path backend/data/fixtures`
7) Ingest real Scryfall (MTG):
   - `docker compose exec backend python -m app.ingest.run scryfall_mtg --set woe --limit 50`
8) Ingest Scryfall en modo offline fixture (CI/dev sin internet):
   - `docker compose exec backend python -m app.ingest.run scryfall_mtg --fixture --path backend/data/fixtures/scryfall --set woe --limit 50`

## B) API versionada

- Endpoints existentes `/api/*` se mantienen por compatibilidad.
- Endpoints estables nuevos: `/api/v1/*` (read-only), por ejemplo:
  - `GET /api/v1/health`
  - `GET /api/v1/search?q=pika&game=pokemon`
  - `GET /api/v1/games`
  - `GET /api/v1/sets?game=mtg`
  - `GET /api/v1/prints?game=mtg&set_code=woe`
  - `GET /api/v1/prints/{id}`

### Ejemplos curl

- `curl -i http://localhost:3000/api/v1/health`
- `curl -i "http://localhost:3000/api/v1/search?q=hope&game=mtg"`
- `curl -i "http://localhost:3000/api/v1/prints?game=mtg&set_code=woe&limit=5"`

## C) Rate limit + cache

- Rate limit in-memory para `/api/v1/search` y `/api/v1/prints`: `60 req/min` por IP (configurable).
- Cache in-memory (TTL default 30s) para `/api/v1/search` y `/api/v1/prints`.
- Header de depuración: `X-Cache: MISS|HIT`.
- En producción se recomienda reemplazar ambos mecanismos por Redis.

## D) Ingest: fixture_local

Comando:

- Docker: `docker compose exec backend python -m app.ingest.run fixture_local --path backend/data/fixtures`
- No docker: `python -m app.ingest.run fixture_local --path backend/data/fixtures`

## E) Ingest: scryfall_mtg

- Conector real público (sin scraping): consume Scryfall API.
- Soporta filtros:
  - `--set <code>`: ingesta solo un set.
  - `--limit <n>`: limita cartas por set para pruebas.
  - `--fixture`: usa JSON locales en `backend/data/fixtures/scryfall` para tests/CI.
- Dedupe canónico:
  - Primario por `print_identifiers(source='scryfall', external_id='<card_id>:foil|nonfoil')`.
  - Secundario por `(game_id, card.name)` para concepto de carta.
- Trazabilidad:
  - Guarda `raw_json` y checksum en `source_records`; si existe checksum, se salta.

## F) Comandos útiles (docker)

- Migraciones: `docker compose exec backend alembic upgrade head`
- Seed base: `docker compose exec backend python -m app.scripts.seed`
- Seed catálogo demo: `docker compose exec backend python -m app.scripts.seed_catalog`
- Ingest fixture_local: `docker compose exec backend python -m app.ingest.run fixture_local --path backend/data/fixtures`
- Ingest scryfall real: `docker compose exec backend python -m app.ingest.run scryfall_mtg --set woe --limit 50`
- Ingest scryfall fixture: `docker compose exec backend python -m app.ingest.run scryfall_mtg --fixture --path backend/data/fixtures/scryfall --set woe --limit 50`
- Search v1: `curl -i "http://localhost:3000/api/v1/search?q=hope&game=mtg"`
- Prints v1: `curl -i "http://localhost:3000/api/v1/prints?game=mtg&set_code=woe"`
