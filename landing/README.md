# Landing pública de DontRipIt

Este directorio contiene la **landing pública estática** de DontRipIt, separada del `frontend/` y `backend/`.

## Objetivo actual

La landing comunica la visión del producto y el alcance real de la **V1 en construcción**:

- catálogo multi‑TCG
- buscador avanzado
- base de datos robusta
- API estructurada
- base para colección, wishlist y futuro marketplace

## Deploy en Vercel

Configura el proyecto con:

- **Root Directory**: `landing`

Con la configuración actual, `index.html` se sirve como landing principal y los archivos estáticos del directorio se exponen directamente.

## Riot verification

`riot.txt` debe mantenerse en este directorio y servirse en `/riot.txt` para no romper la verificación de Riot.
