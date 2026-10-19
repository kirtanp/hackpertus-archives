#!/usr/bin/env bash
# Deploy the web demo to a Docker host over SSH: sync the tree, build the uv-based image (Dockerfile:
# `uv sync --locked --no-dev`), replace the container, wait for /api/health and check the served site against the
# local files.
#
#   deploy/server.sh HOST [BIND]        e.g.  deploy/server.sh omen 100.120.107.76
#   just deploy HOST [BIND]
#
# HOST is an ssh destination. The container always listens on 127.0.0.1:$PORT on the host; BIND adds a second
# address (e.g. the host's tailnet IP). The Apertus key never leaves the host: the container reads $ENV_FILE there
# (default ~/.config/archai/apertus.env). Without that file the app still runs, with extractive cited answers.
set -euo pipefail

HOST=${1:?usage: deploy/server.sh HOST [BIND]}
BIND=${2:-}
DIR=${DEPLOY_DIR:-archai-demo}                       # relative to the remote home
PORT=${DEPLOY_PORT:-8791}
NAME=${DEPLOY_NAME:-archai-demo}
ENV_FILE=${DEPLOY_ENV_FILE:-'$HOME/.config/archai/apertus.env'}
cd "$(dirname "$0")/.."

echo "→ sync to $HOST:~/$DIR"
rsync -a --delete --exclude .venv --exclude .git --exclude '__pycache__' --exclude .pytest_cache \
  --exclude .playwright-mcp --exclude data/facsimiles --exclude data/facsimiles_png --exclude 'deploy/superset/.venv' \
  --exclude .env ./ "$HOST:$DIR/"

echo "→ build and replace the container"
ssh "$HOST" bash -s -- "$DIR" "$NAME" "$PORT" "$BIND" "$ENV_FILE" <<'REMOTE'
set -euo pipefail
dir=$1 name=$2 port=$3 bind=$4 env_file=$(eval echo "$5")
cd "$dir"
docker build -q -t "$name" . >/dev/null
env_arg=(); [ -f "$env_file" ] && env_arg=(--env-file "$env_file") || echo "  (no $env_file: extractive answers only)"
ports=(-p "127.0.0.1:$port:7860"); [ -n "$bind" ] && ports+=(-p "$bind:$port:7860")
docker rm -f "$name" >/dev/null 2>&1 || true
docker run -d --name "$name" --restart unless-stopped --memory 2500m "${env_arg[@]}" "${ports[@]}" "$name" >/dev/null
for _ in $(seq 1 60); do
  curl -sf "http://127.0.0.1:$port/api/health" | grep -q '"ready":true' && break
  sleep 3
done
curl -s "http://127.0.0.1:$port/api/health"; echo
REMOTE

echo "→ verify the served files"
for f in static/site/index.html static/site/support.js app.py; do
  here=$(shasum -a 256 < "$f" | cut -d' ' -f1)
  there=$(ssh "$HOST" "docker exec $NAME sha256sum $f" | cut -d' ' -f1)
  [ "$here" = "$there" ] && echo "  ok  $f" || { echo "  MISMATCH  $f"; exit 1; }
done
