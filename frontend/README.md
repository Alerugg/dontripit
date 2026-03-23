# Frontend · Don’tRipIt Catalog

Frontend público y tooling admin construidos con **Next.js App Router**.

## Estado actual del frontend

La home pública vive en `frontend/app/page.js` y usa `frontend/components/home/HomePageShell.js` como composición principal ya consolidada, sin depender de una landing legacy.

La navegación principal del catálogo ya está organizada alrededor de rutas por juego bajo `frontend/app/games/[slug]`.

### Rutas principales

- `/` → home pública.
- `/explorer` → explorador global.
- `/games/[slug]` → hub dedicado por juego.
- `/games/[slug]/explorer` → explorer scoped por juego.
- `/games/[slug]/cards/[cardId]` → detalle de carta por juego.
- `/games/[slug]/sets/[setCode]` → detalle/listado de set por juego.
- `/cards/[id]` y `/prints/[id]` → rutas de detalle compartidas.
- `/admin/api-console` → consola interna protegida.

### Rutas legacy soportadas

- `/tcg/[slug]` → redirige a `/games/[slug]/explorer`.
- `/play/[slug]` → redirige a `/games/[slug]/explorer`.

## Arquitectura

### Público

- `app/page.js` compone la home con `components/home/*`.
- `components/catalog/CatalogExplorer.js` concentra la UX de búsqueda, sugerencias y resultados.
- `lib/catalog/routes.js` centraliza la construcción de URLs públicas para evitar strings hardcodeadas.
- `lib/catalog/games.js` define el catálogo de juegos soportados y sus metadatos.

### BFF interno

La UI pública no llama directamente al backend protegido. En su lugar utiliza:

- `/api/catalog/search`
- `/api/catalog/suggest`
- `/api/catalog/cards/[id]`
- `/api/catalog/prints/[id]`

## Variables de entorno

Crear `frontend/.env.local` para desarrollo local:

```bash
INTERNAL_API_BASE_URL=http://localhost:5000
INTERNAL_API_KEY=tu_api_key_interna

NEXT_PUBLIC_SITE_NAME=Don’tRipIt
NEXT_PUBLIC_DEFAULT_GAME=

ADMIN_CONSOLE_USERNAME=admin
ADMIN_CONSOLE_PASSWORD=admin
```

Si corres con Docker Compose, `INTERNAL_API_BASE_URL` normalmente debe apuntar a `http://backend:5000`.

## Desarrollo local

```bash
cd frontend
npm install
npm run dev
```

Abrir `http://localhost:3000`.

## Comandos útiles

```bash
npm test
npm run build
npm run start
```

## Troubleshooting rápido

### “Se sigue viendo la misma página”

1. Verifica que el root servido sea realmente Next.js:

```bash
curl -s http://127.0.0.1:3000/ | rg "__NEXT_DATA__|id=\"__next\"|/_next/"
```

2. Limpia artefactos locales si sospechas caché stale:

```bash
cd frontend
rm -rf .next
rm -rf node_modules/.cache
npm run dev
```

3. Si usas Docker Compose, reconstruye el servicio:

```bash
docker compose down
docker compose up -d --build
```

4. Si la incidencia persiste, revisa que no haya un servidor estático o proxy sirviendo una landing legacy por delante del contenedor de Next.js.
