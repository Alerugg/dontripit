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

# sealed products list (paginated + filters)
curl -H "X-API-Key: <key>" "http://localhost:3000/api/v1/products?game=pokemon&set_code=SV1&type=booster_box&limit=20&offset=0"

# sealed product detail
curl -H "X-API-Key: <key>" http://localhost:3000/api/v1/products/1
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
- `GET /api/v1/products`
- `GET /api/v1/products/<id>`
- `GET /api/v1/product-variants?product_id=<id>`
- `GET /api/v1/prices?entity_type=print|product_variant&entity_id=<id>&source=<name>&currency=<code>`
- `GET /api/v1/index?game=<slug>&set_code=<code>&source=<name>&currency=<code>&metric=median|mean`
- `GET /api/v1/admin/prices/last?source=<name>` (requires `read:admin`)
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

## Data ingestion pipeline

### Fixture local

```bash
cd backend
python -m app.ingest.run fixture_local --path data/fixtures/pokemon_demo.json
```

### Reindex search documents

```bash
cd backend
python -m app.scripts.reindex_search
```

### Scryfall MTG

Fixture/offline mode:

```bash
cd backend
python -m app.ingest.run scryfall_mtg --path data/fixtures/scryfall --fixture true --incremental false
```

Real API mode:

```bash
cd backend
python -m app.ingest.run scryfall_mtg --set woe --limit 50 --fixture false --incremental true
```


### Price snapshots ingestion (fixtures)

Load demo prices (audit-friendly: source + currency + timestamp):

```bash
cd backend
python -m app.ingest.run fixture_local --path data/fixtures/prices_demo.json
```

Then query:

```bash
curl -H "X-API-Key: <key>" "http://localhost:3000/api/v1/prices?entity_type=print&entity_id=1"
curl -H "X-API-Key: <key>" "http://localhost:3000/api/v1/index?set_code=SV1&source=demo&currency=EUR"
```

Daily aggregation for charting:

```bash
cd backend
python -m app.scripts.aggregate_prices_daily
```

To add official sources later, implement a new connector that writes to `price_sources` and `price_snapshots` using the same idempotent identity `(entity_type, entity_id, source_id, currency, as_of)`.

### Inspect ingest state

Requires API key with `read:admin`.

- `GET /api/v1/admin/ingest/runs?source=fixture_local`
- `GET /api/v1/admin/ingest/state?source=scryfall_mtg`
- `GET /api/v1/admin/quality/summary`

### Troubleshooting

- Scryfall 429/5xx: connector retries with exponential backoff + request throttling.
- Fixture path errors: verify path is relative to `backend/` or use absolute path.
- Empty search results: run `python -m app.scripts.reindex_search` and retry `/api/search?q=pika`.
