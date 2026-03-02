# Monorepo limpio: `frontend` (Next.js) + `backend` (Flask)

Este repositorio está organizado en dos carpetas principales:

- `frontend/` → aplicación Next.js (JavaScript)
- `backend/` → API Flask (Python)

## Estructura

- `frontend/app` interfaz web (App Router)
- `frontend/next.config.js` rewrite de `/api/*` hacia Flask en desarrollo
- `backend/app.py` endpoints Flask (`/api/health`, `/api/python`)
- `backend/requirements.txt` dependencias Python

## Ejecución local

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

## Verificaciones esperadas

- Frontend: `http://localhost:3000`
- Backend directo: `http://localhost:5000/api/health`
- Backend vía rewrite de Next: `http://localhost:3000/api/health`

Respuesta esperada en salud:

```json
{"ok": true}
```
