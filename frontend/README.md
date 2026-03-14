# Frontend · TCG Catalog (Next.js + JavaScript)

Base de producto para catálogo TCG multi-game con separación entre experiencia pública y tooling admin privado.

## Superficies

- **Público (catálogo)**: `/, /cards/[id], /prints/[id]`
- **Privado/admin**: `/admin/api-console` (protegido por credenciales)
Base de producto para catálogo TCG multi-game con separación clara entre experiencia pública y tooling admin.

## Arquitectura

- **Público (catálogo)**: `/, /cards/[id], /prints/[id]`
- **Privado/admin**: `/admin/api-console`
- **BFF interno (server-side)**:
  - `/api/catalog/search`
  - `/api/catalog/cards/[id]`
  - `/api/catalog/prints/[id]`

La UI pública nunca llama directo al backend protegido; consume solamente el BFF interno.

## Variables de entorno

Crear `frontend/.env.local` (entorno local fuera de Docker):

```bash
# Server only (NO exponer al cliente)
INTERNAL_API_BASE_URL=http://localhost:5000
INTERNAL_API_KEY=tu_api_key_interna

# Público (opcionales)
NEXT_PUBLIC_SITE_NAME=TCG Nexus
NEXT_PUBLIC_DEFAULT_GAME=

# Protección admin
ADMIN_CONSOLE_USERNAME=admin
ADMIN_CONSOLE_PASSWORD=admin
```

### Si corres con Docker Compose

En el servicio `frontend`, `INTERNAL_API_BASE_URL` debe apuntar a `http://backend:5000` (red interna de Docker).

## Correr localmente (npm)

```bash
cd frontend
npm install
npm run dev
```

Abrir `http://localhost:3000`.

### Puertos de desarrollo

- Frontend Next.js: `3000`
- Backend API: `5000`

## Flujos

### Catálogo público

1. El navegador consulta `/api/catalog/*`.
2. Las rutas BFF del frontend llaman a `INTERNAL_API_BASE_URL` con `INTERNAL_API_KEY` desde servidor.
3. La UI recibe payload sanitizado y mensajes amigables.

### Admin API Console

- Ruta: `/admin/api-console`
- Requiere Basic Auth (`ADMIN_CONSOLE_USERNAME` + `ADMIN_CONSOLE_PASSWORD`).
- Permite probar presets de `search/cards/prints` contra el BFF interno.
- No aparece en la navegación pública del catálogo.


### Puertos de desarrollo

- Frontend Next.js: `3000`
- Backend API: `5000`

## Flujos

### Catálogo público

1. El navegador consulta `/api/catalog/*`.
2. Las rutas BFF del frontend llaman a `INTERNAL_API_BASE_URL` con `INTERNAL_API_KEY` desde servidor.
3. La UI recibe payload sanitizado y mensajes amigables.

### Admin API Console

- Ruta: `/admin/api-console`
- Permite probar presets de `search/cards/prints` contra el BFF interno.
- Mantiene la experiencia técnica fuera de la home pública.

## Comandos útiles

```bash
npm run test
npm run build
```
