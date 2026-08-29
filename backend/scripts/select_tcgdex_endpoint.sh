#!/usr/bin/env bash
set -euo pipefail

probe_language="${POKEMON_LANGUAGE:-en}"
remote_template="https://api.tcgdex.net/v2/{lang}"
remote_url="https://api.tcgdex.net/v2/${probe_language}/series"
probe_body="${RUNNER_TEMP:-/tmp}/tcgdex-remote-probe.json"

printf 'Probing remote TCGdex transport: %s\n' "$remote_url"
if http_code="$(curl --silent --show-error --output "$probe_body" --write-out '%{http_code}' \
  --connect-timeout 5 --max-time 15 "$remote_url")"; then
  printf 'Remote TCGdex transport reachable (HTTP %s); keeping remote endpoint.\n' "$http_code"
  {
    echo "TCGDEX_BASE_URL_TEMPLATE=${remote_template}"
    echo "TCGDEX_SOURCE_MODE=remote"
  } >> "$GITHUB_ENV"
  exit 0
fi

printf 'Remote TCGdex transport is unreachable; activating frozen official local fallback.\n'
: "${TCGDEX_FALLBACK_IMAGE:?TCGDEX_FALLBACK_IMAGE must contain the frozen official image digest}"

docker rm -f tcgdex-fallback >/dev/null 2>&1 || true
docker pull "$TCGDEX_FALLBACK_IMAGE"
docker run -d \
  --name tcgdex-fallback \
  -p 127.0.0.1:3000:3000 \
  -e CI=1 \
  -e MAX_WORKERS=2 \
  "$TCGDEX_FALLBACK_IMAGE" >/dev/null

ready=0
for _attempt in $(seq 1 30); do
  if curl --silent --show-error --fail --connect-timeout 2 --max-time 5 \
    http://127.0.0.1:3000/ping >/dev/null; then
    ready=1
    break
  fi
  sleep 2
done

if [[ "$ready" != "1" ]]; then
  docker logs tcgdex-fallback || true
  echo "Official local TCGdex fallback failed to become ready" >&2
  exit 1
fi

local_url="http://127.0.0.1:3000/v2/${probe_language}/series"
curl --silent --show-error --fail --connect-timeout 2 --max-time 15 \
  "$local_url" >/dev/null

{
  echo "TCGDEX_BASE_URL_TEMPLATE=http://127.0.0.1:3000/v2/{lang}"
  echo "TCGDEX_SOURCE_MODE=official_local_fallback"
} >> "$GITHUB_ENV"

printf 'Official local TCGdex fallback ready: %s (%s)\n' "$TCGDEX_FALLBACK_IMAGE" "$local_url"
