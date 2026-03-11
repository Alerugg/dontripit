# Frontend (Next.js + React en JavaScript)

Frontend visual tipo **catálogo/marketplace informativo** para explorar el TCG, consumiendo **solo la API real** de API-PROJECT.

## Endpoints backend usados por el frontend

- `GET /api/v1/games`
- `GET /api/v1/search`
- `GET /api/v1/cards/:id`
- `GET /api/v1/prints/:id`
- `POST /api/admin/dev/api-keys` (botón de generar key para usarla inmediatamente)

## Variables de entorno

Crea `frontend/.env.local`:

```bash
# URL base de API. Si se deja vacía, usa same-origin /api/* con rewrite de Next.
NEXT_PUBLIC_API_BASE_URL=http://localhost:5000

# Opcional: clave por defecto. Si no existe, el usuario la puede pegar o generar desde UI.
NEXT_PUBLIC_API_KEY=
```

### Local

```bash
NEXT_PUBLIC_API_BASE_URL=http://localhost:5000
```

### Railway

```bash
NEXT_PUBLIC_API_BASE_URL=https://lively-consideration-production.up.railway.app
```

## Arranque local

```bash
cd frontend
npm install
npm run dev
```

Abrir: `http://localhost:3000/explorer`

## Flujo de key (importante)

1. En `/explorer`, escribe `Admin token`.
2. Click en **Generar API Key**.
3. El frontend llama `POST /api/admin/dev/api-keys`.
4. La `api_key` devuelta se guarda y se aplica automáticamente para todas las búsquedas.

## Validación manual de búsqueda real

1. Abre `/explorer`.
2. Genera API key o pega una key válida.
3. Prueba en buscador:
   - `Dark Magician`
   - `Forest`
   - `Charizard`
4. Verifica que:
   - aparecen sugerencias en autocompletado,
   - el grid carga resultados de `/api/v1/search`,
   - al hacer click abre detalle (`/api/v1/cards/:id` o `/api/v1/prints/:id`).

## Notas de arquitectura

- `lib/apiClient.js`: capa centralizada de API (base URL, headers, `X-API-Key`).
- `app/explorer/page.js`: buscador, filtros, autocompletado, grid y paginación.
- `app/explorer/[type]/[id]/page.js`: detalle de card/print.
- `components/*`: componentes reutilizables.
