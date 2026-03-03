# API PROJECT

## Run with Docker

1. `cp .env.example .env`
2. `docker compose up --build`
3. `docker compose exec backend alembic upgrade head`
4. `docker compose exec backend python -m app.scripts.seed`
5. Optional ingest: `docker compose exec backend python -m app.ingest.run fixture_local --path backend/data/fixtures`

## API access model (API as Product)

### API versioning

- Canonical versioned API is under `/api/v1/*`.
- Legacy `/api/*` routes are temporary aliases to v1 behavior (same handlers), so existing clients keep working.

### Environment variables

- `PUBLIC_API_ENABLED=true|false` (default: `false`)
  - `false`: API key required for all `/api/*` and `/api/v1/*` except health.
  - `true`: API can be accessed without API key, but no-key traffic is still rate-limited by IP.
- `PUBLIC_IP_RATE_LIMIT_RPM=30` (default)

### Force API key on all protected routes

Set this in `.env` (and keep the same value in Compose):

```bash
PUBLIC_API_ENABLED=false
```

### Create API key

```bash
docker compose exec backend python -m app.auth.create_key --plan free --label "dev"
```

Optional explicit scopes:

```bash
docker compose exec backend python -m app.auth.create_key --plan pro --label "admin" --scopes "read:catalog,read:admin"
```

Default scope for new keys is `read:catalog`.

### Scopes

- `read:catalog`: required for catalog/read endpoints.
- `read:admin`: required for admin endpoints such as `/api/v1/admin/metrics`.

If a key is valid but lacks required permission:

- `403 {"error":"insufficient_scope"}`

### Rotate / disable keys

Disable a key by prefix:

```bash
docker compose exec backend python -m app.auth.disable_key --prefix ak_abcd1
```

Rotate a key by prefix (deactivates old key, prints new key once):

```bash
docker compose exec backend python -m app.auth.rotate_key --prefix ak_abcd1
```

Requests made with disabled keys return:

- `401 {"error":"invalid_api_key"}`

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
# default mode (PUBLIC_API_ENABLED=false): missing key => 401
curl http://localhost:3000/api/v1/games

# with key => 200
curl -H "X-API-Key: <key>" http://localhost:3000/api/v1/games

# legacy alias still works => 200
curl -H "X-API-Key: <key>" http://localhost:3000/api/games

# admin metrics with admin scope => 200
curl -H "X-API-Key: <admin_key>" http://localhost:3000/api/v1/admin/metrics
```

### Response headers

- `X-Plan`
- `X-RateLimit-Limit`
- `X-RateLimit-Remaining`
- `X-Quota-Monthly`
- `X-Quota-Used`

### Error responses (JSON)

- `401 {"error":"missing_api_key"}`
- `401 {"error":"invalid_api_key"}`
- `403 {"error":"insufficient_scope"}`
- `429 {"error":"rate_limited"}`
- `429 {"error":"quota_exceeded"}`

## Endpoints

- `GET /api/v1/health`
- `GET /api/v1/db-check`
- `GET /api/v1/games`
- `GET /api/v1/cards`
- `GET /api/v1/prints`
- `GET /api/v1/prints/<id>`
- `GET /api/v1/sets`
- `GET /api/v1/search`
- `GET /api/v1/admin/metrics` (requires `read:admin`)
- `GET /api/v1/docs`
- `GET /api/v1/openapi.json`

Legacy aliases without `/v1` are currently still available.

## Postman Collection

Collection file: `/postman/API-PROJECT.postman_collection.json`

Import steps:

1. Open Postman → **Import**.
2. Select `postman/API-PROJECT.postman_collection.json`.
3. Set collection variables:
   - `baseUrl` (default `http://localhost:3000`)
   - `apiKey` (your key)
4. Run requests (header `X-API-Key` is already configured at collection level).

## SDKs

- JavaScript: `sdk/javascript`
- Python: `sdk/python`

## Tests

```bash
cd backend
pytest -q
```
