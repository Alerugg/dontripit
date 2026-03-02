# Monorepo limpio: `frontend` (Next.js) + `backend` (Flask)

Este repositorio está organizado en dos carpetas principales:

- `frontend/` → aplicación Next.js (JavaScript)
- `backend/` → API Flask (Python)

## Estructura

- `frontend/app` interfaz web (App Router)
- `frontend/next.config.js` rewrite de `/api/*` hacia Flask (configurable por env vars)
- `backend/app.py` endpoints Flask (`/api/health`, `/api/python`)
- `backend/requirements.txt` dependencias Python
- `docker-compose.yml` stack de desarrollo con `db`, `backend`, `frontend`

## Ejecución local (sin Docker)

### 1) Backend (puerto 5000)

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
flask --app app.py run --host 0.0.0.0 --port 5000
```

### 2) Frontend (puerto 3000)

```bash
cd frontend
npm install
npm run dev
```

## Docker Dev

```bash
cp .env.example .env
docker compose up --build
```

Verificación vía frontend (proxy/rewrite):

```bash
curl -i http://localhost:3000/api/health
```

Respuesta esperada:

```json
{"ok": true}
```

## Switch de backend URL (local vs Docker)

- `frontend/next.config.js` usa `BACKEND_INTERNAL_URL` primero, luego `BACKEND_URL`, y si no existen usa `http://localhost:5000`.
- En Docker Compose se define `BACKEND_INTERNAL_URL=http://backend:5000`, por eso el frontend habla al servicio `backend`.
- Fuera de Docker, `npm run dev` sigue funcionando con `localhost:5000` (sin configuración extra).

## Troubleshooting

- Si `docker compose up --build` falla por puertos, libera `3000`/`5000` o cambia los mapeos en `docker-compose.yml`.
- Si frontend no conecta al backend dentro de Docker, revisa que `BACKEND_INTERNAL_URL` sea `http://backend:5000`.
- Si cambias dependencias de Node/Python, reconstruye con `docker compose up --build`.
- Si Postgres no inicia, elimina el volumen con `docker compose down -v` y vuelve a levantar.
- Si el healthcheck tarda, espera unos segundos y revisa logs con `docker compose logs -f`.
