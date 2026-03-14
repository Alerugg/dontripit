# Frontend · Next.js (JavaScript)

Frontend MVP de catálogo TCG multi-game con estética companion app / proto-marketplace.

## Stack

- Next.js App Router
- React
- JavaScript (sin TypeScript)

## Variables de entorno

Crear `frontend/.env.local`:

```bash
NEXT_PUBLIC_API_BASE_URL=http://localhost:5000
# opcional: clave fija para todas las requests
NEXT_PUBLIC_API_KEY=
```

Si no defines `NEXT_PUBLIC_API_KEY`, puedes guardarla en runtime desde `/api-console` (localStorage).

## Correr localmente

```bash
cd frontend
npm install
npm run dev
```

Abrir: `http://localhost:3000`

## Rutas principales

- `/` → Explorer catálogo (hero + sidebar + grid/list)
- `/cards/[id]` → Detalle de card + panel de prints/variantes
- `/prints/[id]` → Detalle de print + metadata + vuelta a card
- `/api-console` → Consola amigable para testing manual
- `/console` → alias legado que redirige a `/api-console`

## Endpoints usados

- `GET /api/v1/search?q=...&game=...`
- `GET /api/v1/cards/:id`
- `GET /api/v1/prints/:id`
- `GET /api/v1/games`
- `GET /api/v1/health` (solo API console)

## Arquitectura frontend (MVP)

- `components/AppShell.js`: topbar global y navegación preparada para crecer.
- `components/ExplorerSidebar.js`: filtros laterales y selector de vista grid/list.
- `components/ResultsGrid.js` + `components/ResultCard.js`: catálogo visual y cards reutilizables.
- `lib/apiClient.js`: cliente API centralizado con soporte para `NEXT_PUBLIC_API_BASE_URL` y fallback de API key local.
- `lib/apiKeyStorage.js`: persistencia local de API key para desarrollo.

Base lista para extender hacia colección, wishlist, binder y marketplace.
