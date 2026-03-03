# API PROJECT

## Run with Docker

1. `cp .env.example .env`
2. `docker compose up --build`
3. `docker compose exec backend alembic upgrade head`
4. `docker compose exec backend python -m app.scripts.seed`
5. Optional ingest: `docker compose exec backend python -m app.ingest.run fixture_local --path backend/data/fixtures`

## API access model (API as Product)

### Environment variables

- `PUBLIC_API_ENABLED=true|false`
  - `true`: `/api/*` works without API key (except still rate-limited by IP).
  - `false`: API key required for all `/api/*` except `/api/health`.
- `PUBLIC_IP_RATE_LIMIT_RPM=30` (default)

### Create API key

```bash
docker compose exec backend python -m app.auth.create_key --plan free --label "dev"
```

The key is printed only once in stdout. The database stores only `key_hash` + `prefix`.

### Plans and limits

- `free`: monthly quota `5000`, burst `60 rpm`
- `pro`: monthly quota `100000`, burst `600 rpm`
- `enterprise`: monthly quota unlimited, burst `3000 rpm`

### Request headers

Use either option:

- `X-API-Key: <key>`
- `Authorization: Bearer <key>`

### Curl examples

```bash
curl -H "X-API-Key: <key>" http://localhost:3000/api/games
curl http://localhost:3000/api/games
```

Second example only works when `PUBLIC_API_ENABLED=true`.

### Response headers

- `X-Plan`
- `X-RateLimit-Limit`
- `X-RateLimit-Remaining`
- `X-Quota-Monthly`
- `X-Quota-Used`

### Error responses (JSON)

- `401 {"error":"missing_api_key"}`
- `401 {"error":"invalid_api_key"}`
- `429 {"error":"rate_limited"}`
- `429 {"error":"quota_exceeded"}`

## Endpoints

- `GET /api/health`
- `GET /api/db-check`
- `GET /api/games`
- `GET /api/cards`
- `GET /api/prints`
- `GET /api/prints/<id>`
- `GET /api/sets`
- `GET /api/search`
- `GET /api/docs`
- `GET /api/openapi.json`

## SDKs

- JavaScript: `sdk/javascript`
- Python: `sdk/python`

## Tests

```bash
cd backend
pytest -q
```
