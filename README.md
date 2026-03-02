# Next.js + Flask en GitHub Codespaces (sin Docker)

Proyecto híbrido con frontend **Next.js (JavaScript)** y backend **Flask (Python)**.

## Estructura

- `app/` → frontend Next.js (App Router)
- `api/index.py` → backend Flask
- `next.config.js` → rewrite `/api/*` hacia Flask en desarrollo
- `requirements.txt` → dependencias Python

## Arranque en Codespaces (2 terminales)

### Terminal 1 — Backend (Flask, puerto 5000)

```bash
cd /workspace/API-PROJECT
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python3 -m flask --app api/index.py run --host 0.0.0.0 --port 5000
```

### Terminal 2 — Frontend (Next.js, puerto 3000)

```bash
cd /workspace/API-PROJECT
npm install
npm run dev
```

## URLs en Codespaces

- Frontend: `http://localhost:3000`
- Backend directo: `http://localhost:5000/api/health`
- Backend vía Next rewrite (sin CORS): `http://localhost:3000/api/health`

## Verificación rápida

1. Abre el puerto **3000** desde la pestaña **Ports** de Codespaces.
2. Visita `http://localhost:3000/api/health`.
3. Debe responder:

```json
{"ok": true}
```

## Scripts disponibles (frontend)

- `npm run dev` → `next dev -H 0.0.0.0 -p 3000`
- `npm run build`
- `npm run start` → `next start -H 0.0.0.0 -p 3000`
- `npm run lint`
