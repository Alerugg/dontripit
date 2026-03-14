# Frontend · Next.js (JavaScript)

MVP de explorador multi-game para TCG usando la API de `API-PROJECT`.

## Stack

- Next.js App Router
- React
- JavaScript (sin TypeScript)

## Variables de entorno

Crear `frontend/.env.local`:

```bash
NEXT_PUBLIC_API_BASE_URL=http://localhost:5000
# opcional si tu backend requiere auth por header
NEXT_PUBLIC_API_KEY=
```

## Correr localmente

```bash
cd frontend
npm install
npm run dev
```

Abrir: `http://localhost:3000`

## Rutas principales

- `/` → Explorer (buscador, filtros, resultados)
- `/cards/[id]` → Detalle de carta
- `/prints/[id]` → Detalle de print
- `/explorer` → alias que redirige a `/`

## Endpoints usados

- `GET /api/v1/games`
- `GET /api/v1/search?q=...&game=...&type=...`
- `GET /api/v1/cards/:id`
- `GET /api/v1/prints/:id`

## Notas de arquitectura

- `lib/apiClient.js`: capa centralizada de llamadas API y manejo de `NEXT_PUBLIC_API_BASE_URL`.
- `components/SearchControls.js`: input de búsqueda + filtros.
- `components/ResultsGrid.js` y `components/ResultCard.js`: render de resultados (`card`, `print`, `set`).
- `app/cards/[id]/page.js` y `app/prints/[id]/page.js`: vistas de detalle.

Base preparada para extender filtros y sumar autocomplete en próximas fases.
