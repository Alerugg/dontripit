# Landing pública de Dontripit

Este directorio contiene la **landing pública estática** de Dontripit, separada del `frontend/` y `backend/`.

## Objetivo actual

La landing comunica la visión de plataforma y el alcance real de la **V1 en construcción**:

- catálogo multi-TCG
- buscador estructurado
- API de datos
- cobertura progresiva por juegos
- base para colección, wishlist y marketplace futuro
- bloque de comunidad y contacto oficial

## Deploy en Vercel

Configura el proyecto con:

- **Root Directory**: `landing`

Con la configuración actual, `index.html` se sirve como landing principal y los archivos estáticos del directorio se exponen directamente.

## Riot verification

`riot.txt` debe mantenerse en este directorio y servirse en `/riot.txt` para no romper la verificación de Riot.
