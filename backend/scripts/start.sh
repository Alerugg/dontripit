#!/usr/bin/env bash
set -euo pipefail

cd /app

alembic upgrade head
python -m app.scripts.seed_catalog

exec gunicorn -b 0.0.0.0:"${PORT:-5000}" app.main:app
