# Run from scratch (Codespaces)

## A) Run with Docker (recomendado)

1) `cp .env.example .env`
2) `docker compose up --build`
3) `docker compose exec backend alembic upgrade head`
4) `docker compose exec backend python -m app.scripts.seed`
5) (opcional) `docker compose exec backend python -m app.scripts.seed_catalog`
6) Ingest por conector `fixture_local` (recomendado para carga incremental):
   - `docker compose exec backend python -m app.ingest.run fixture_local --path backend/data/fixtures`
7) Pruebas rápidas:
   - `curl -i http://localhost:3000/api/health`
   - `curl -i http://localhost:3000/api/db-check`
   - `curl -i http://localhost:3000/api/games`
   - `curl -i "http://localhost:3000/api/sets?game=pokemon"`
   - `curl -i "http://localhost:3000/api/prints?set_code=SV1&language=EN"`
   - `curl -i "http://localhost:3000/api/search?q=pika&game=pokemon"`

## B) Run without Docker (2 terminales)

Backend:

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

- `curl -i http://localhost:3000/api/db-check`
- `curl -i http://localhost:3000/api/games`
- `curl -i "http://localhost:3000/api/sets?game=pokemon"`
- `curl -i "http://localhost:3000/api/prints?set_code=SV1&language=EN"`
- `curl -i "http://localhost:3000/api/search?q=pika&game=pokemon"`

## Ingest: fixture_local

Comando:

- Docker: `docker compose exec backend python -m app.ingest.run fixture_local --path backend/data/fixtures`
- No docker: `python -m app.ingest.run fixture_local --path backend/data/fixtures`

Formato JSON (`backend/data/fixtures/pokemon_demo.json`):

- `game`: `{ slug, name }`
- `sets`: `[{ code, name, release_date }]`
- `cards`: `[{ name }]`
- `prints`: `[{ set_code, card_name, collector_number, language, rarity, is_foil, images, identifiers }]`
- `images`: `[{ url, is_primary, source }]`
- `identifiers`: `[{ source, external_id }]`

Notas:

- El ingest guarda payload crudo en `source_records.raw_json` con checksum SHA256.
- Si el checksum ya existe para el source, se salta el archivo (idempotente).
- Además, los upserts usan uniques para evitar duplicados en sets/prints/identifiers.

---

## Estructura

- `frontend/` → aplicación Next.js (JavaScript)
- `backend/app/` → API Flask + SQLAlchemy
- `backend/app/ingest/` → framework de conectores e ingest CLI
- `backend/data/fixtures/` → fixtures JSON locales
- `backend/alembic/` → migraciones Alembic
- `backend/tests/` → tests pytest
- `docker-compose.yml` → stack db + backend + frontend
