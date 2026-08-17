#!/usr/bin/env bash
set -euo pipefail

cd /app

# Production runtime must not mutate the database during boot.
# Schema migrations and catalog/bootstrap operations are executed explicitly
# through gated CI workflows against the canonical Neon database.
exec gunicorn -b 0.0.0.0:"${PORT:-5000}" app.main:app
