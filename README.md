# Run from scratch (Codespaces)

## A) Run with Docker (recomendado)

1) `cp .env.example .env`
2) `docker compose up --build`
3) `docker compose exec backend alembic upgrade head`
4) `docker compose exec backend python -m app.scripts.seed`
5) `docker compose exec backend python -m app.scripts.seed_catalog`
6) Pruebas:
   - `curl -i http://localhost:3000/api/health`
   - `curl -i http://localhost:3000/api/db-check`
   - `curl -i http://localhost:3000/api/games`
   - `curl -i "http://localhost:3000/api/sets?game=pokemon"`
   - `curl -i "http://localhost:3000/api/prints?set_code=SV1&language=EN"`
   - `curl -i "http://localhost:3000/api/prints/1"`

## B) Run without Docker (2 terminales)

Backend:

- `cd backend`
- `python -m venv .venv`
- `source .venv/bin/activate`
- `pip install -r requirements.txt`
- `export DATABASE_URL=postgresql+psycopg2://USER:PASS@localhost:5432/DB`
- `alembic upgrade head`
- `python -m app.scripts.seed`
- `python -m app.scripts.seed_catalog`
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
- `curl -i "http://localhost:3000/api/prints/1"`

---

## Estructura

- `frontend/` → aplicación Next.js (JavaScript)
- `backend/app/` → API Flask + SQLAlchemy
- `backend/alembic/` → migraciones Alembic
- `backend/tests/` → tests pytest
- `docker-compose.yml` → stack db + backend + frontend
