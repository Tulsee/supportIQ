#!/usr/bin/env bash
# Checks that the Caddyfile sends the security headers on the app's and the API's
# responses, over what the upstreams send, and hides the software behind the site.
# Runs Caddy in Docker in front of two stand-in upstreams (named api and web, as in
# docker-compose.yml) that send Server and X-Powered-By headers of their own.
#
#   infrastructure/docker/caddy/check-headers.sh     (needs Docker; used by CI)
set -euo pipefail

here=$(cd "$(dirname "$0")" && pwd)
image=caddy:2.10-alpine # the same image as docker-compose.yml
name=caddy-header-check-$$
port=${CHECK_PORT:-18080}

cleanup() {
  docker rm -f "$name-web" "$name-api" "$name-proxy" >/dev/null 2>&1 || true
  docker network rm "$name" >/dev/null 2>&1 || true
}
trap cleanup EXIT

docker network create "$name" >/dev/null
docker run -d --name "$name-web" --network "$name" --network-alias web "$image" \
  caddy respond --listen :3000 --header "X-Powered-By: Next.js" --body web >/dev/null
docker run -d --name "$name-api" --network "$name" --network-alias api "$image" \
  caddy respond --listen :8000 --header "Server: uvicorn" --body api >/dev/null
docker run -d --name "$name-proxy" --network "$name" -p "127.0.0.1:$port:80" \
  -v "$here/Caddyfile:/etc/caddy/Caddyfile:ro" "$image" >/dev/null

for _ in $(seq 1 30); do
  curl -fsS -o /dev/null "http://127.0.0.1:$port/" 2>/dev/null && break
  sleep 1
done

required=(
  "strict-transport-security: max-age=31536000"
  "x-content-type-options: nosniff"
  "referrer-policy: strict-origin-when-cross-origin"
  "x-frame-options: DENY"
  "content-security-policy: frame-ancestors 'none'; base-uri 'self'; object-src 'none'; form-action 'self'"
  "permissions-policy: camera=(), microphone=(), geolocation=(), payment=(), usb=()"
  "cross-origin-opener-policy: same-origin"
)
forbidden=("server" "x-powered-by")

failed=0
for path in / /api/v1/health; do
  # Header names lowercased, line endings stripped.
  headers=$(curl -fsS -D - -o /dev/null "http://127.0.0.1:$port$path" \
    | tr -d '\r' | awk -F': ' 'NF > 1 { printf "%s: %s\n", tolower($1), substr($0, length($1) + 3) }')
  for header in "${required[@]}"; do
    if ! grep -qxF "$header" <<<"$headers"; then
      echo "::error::$path is missing \"$header\""
      failed=1
    fi
  done
  for header in "${forbidden[@]}"; do
    if grep -qi "^$header:" <<<"$headers"; then
      echo "::error::$path sends \"$(grep -i "^$header:" <<<"$headers")\""
      failed=1
    fi
  done
done

if [ "$failed" -ne 0 ]; then
  echo "Response headers were:"
  curl -sS -D - -o /dev/null "http://127.0.0.1:$port/"
  exit 1
fi
echo "Security headers present on / and /api/v1/health; Server and X-Powered-By hidden."
