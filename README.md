# Run from scratch (Codespaces)

## A) Run with Docker (recomendado)

1) `cp .env.example .env`
2) `docker compose up --build`
3) `docker compose exec backend alembic upgrade head`
4) `docker compose exec backend python -m app.scripts.seed`
5) (Opcional / recomendado) ingest local:
   - `docker compose exec backend python -m app.ingest.run fixture_local --path backend/data/fixtures`
6) Pruebas rápidas:
   - `curl -i "http://localhost:3000/api/health"`
   - `curl -i "http://localhost:3000/api/db-check"`
   - `curl -i "http://localhost:3000/api/games"`
   - `curl -i "http://localhost:3000/api/search?q=pika&game=pokemon"`


## API endpoints

- `GET /api/sets?game=pokemon&limit=50&offset=0&order=release_date_desc&q=sv`
- `GET /api/prints/<id>`
- `GET /api/search?q=pika&game=pokemon&type=card|set|print&limit=20&offset=0`

Ejemplos curl:

- `curl -i "http://localhost:3000/api/sets?game=pokemon"`
- `curl -i "http://localhost:3000/api/prints/1"`
- `curl -i "http://localhost:3000/api/search?q=pika&game=pokemon"`

## C) Rate limit + cache

- Rate limit in-memory para `/api/v1/search` y `/api/v1/prints`: `60 req/min` por IP (configurable).
- Cache in-memory (TTL default 30s) para `/api/v1/search` y `/api/v1/prints`.
- Header de depuración: `X-Cache: MISS|HIT`.
- En producción se recomienda reemplazar ambos mecanismos por Redis.

## D) Ingest: fixture_local

## E) Migraciones y verificación de /api/search

- `docker compose exec backend alembic upgrade head`
- `curl -i "http://localhost:3000/api/search?q=pika&game=pokemon&type="`


Comando:

- `cd backend`
- `python -m venv .venv`
- `source .venv/bin/activate`
- `pip install -r requirements.txt`
- `export DATABASE_URL=postgresql+psycopg2://USER:PASS@localhost:5432/DB`
- `alembic upgrade head`
- `python -m app.scripts.seed`
- `python -m app.ingest.run fixture_local --path backend/data/fixtures`
- `flask --app app.main:app run --host=0.0.0.0 --port=5000`

Frontend:

- `cd frontend`
- `npm install`
- `npm run dev`

Pruebas:

- `curl -i "http://localhost:3000/api/db-check"`
- `curl -i "http://localhost:3000/api/games"`
- `curl -i "http://localhost:3000/api/search?q=pika&game=pokemon"`

---

## Search API (Postgres FTS)

- Endpoint recomendado: `GET /api/search?q=...&game=...&type=card|set|print&limit=20&offset=0`
- Validación:
  - `q` es obligatorio (mínimo 2 chars)
  - `limit` default 20, máximo 100
- Resultado:
  - `type`, `id`, `title`, `subtitle`, `score`
  - para `print`: `set_code`, `collector_number`, `primary_image_url`

## Ingest framework

CLI base:

- `python -m app.ingest.run fixture_local --path backend/data/fixtures`

Qué hace:

1. Lee archivos JSON.
2. Calcula checksum SHA-256 por archivo.
3. Guarda payload crudo en `source_records.raw_json`.
4. Si `(source_id, checksum)` ya existe, hace skip (idempotente).
5. Normaliza y upsertea `games`, `sets`, `cards`, `prints`, `print_images`, `print_identifiers`.

### fixture_local JSON format

Ejemplo disponible en `backend/data/fixtures/pokemon_demo.json`:

```json
{
  "game": {"slug": "pokemon", "name": "Pokémon"},
  "sets": [{"code": "SV1", "name": "Scarlet & Violet", "release_date": "2023-03-31"}],
  "cards": [{"name": "Pikachu"}],
  "prints": [
    {
      "set_code": "SV1",
      "card_name": "Pikachu",
      "collector_number": "001",
      "language": "EN",
      "rarity": "common",
      "is_foil": false,
      "images": [{"url": "https://...", "is_primary": true, "source": "fixture_local"}],
      "identifiers": [{"source": "demo", "external_id": "..."}]
    }
  ]
}
```

Si faltan arrays (`sets`, `cards`, `prints`), no rompe el proceso.

---

## Estructura

- `frontend/` → aplicación Next.js (JavaScript)
- `backend/app/` → API Flask + SQLAlchemy
- `backend/app/ingest/` → framework de ingest por conectores
- `backend/alembic/` → migraciones Alembic
- `backend/data/fixtures/` → fixtures locales JSON
- `backend/tests/` → tests pytest
- `docker-compose.yml` → stack db + backend + frontend
