#!/usr/bin/env bash
set -euo pipefail

LANGFUSE_DIR="$HOME/.langfuse"
ENV_FILE="$(cd "$(dirname "$0")/.." && pwd)/.env"

echo "==> Setting up Langfuse in $LANGFUSE_DIR"

if [ ! -d "$LANGFUSE_DIR" ]; then
  git clone https://github.com/langfuse/langfuse.git "$LANGFUSE_DIR"
else
  echo "    Repo already cloned, pulling latest..."
  git -C "$LANGFUSE_DIR" pull --ff-only
fi

echo "==> Starting Langfuse (official docker-compose)..."
docker compose -f "$LANGFUSE_DIR/docker-compose.yml" up -d

echo "==> Waiting for Langfuse to be ready..."
for i in $(seq 1 90); do
  if curl -sf http://localhost:3000/api/public/health > /dev/null 2>&1; then
    echo "    Langfuse is up!"
    break
  fi
  if [ "$i" -eq 90 ]; then
    echo "    Langfuse didn't come up in time — check 'docker compose -f $LANGFUSE_DIR/docker-compose.yml logs'"
    exit 1
  fi
  sleep 2
done

# Append Langfuse env vars to .env if not already present
if ! grep -q "LANGFUSE_PUBLIC_KEY" "$ENV_FILE" 2>/dev/null; then
  echo "" >> "$ENV_FILE"
  echo "# Langfuse tracing (local)" >> "$ENV_FILE"
  echo "LANGFUSE_PUBLIC_KEY=pk-lf-local" >> "$ENV_FILE"
  echo "LANGFUSE_SECRET_KEY=sk-lf-local" >> "$ENV_FILE"
  echo "LANGFUSE_HOST=http://localhost:3000" >> "$ENV_FILE"
  echo "==> Added Langfuse keys to $ENV_FILE"
else
  echo "==> Langfuse keys already in $ENV_FILE, skipping"
fi

echo ""
echo "Done! Langfuse dashboard: http://localhost:3000"
echo "  Create an account on first visit, then go to Settings > API Keys"
echo "  to get your actual keys (update .env if different from defaults)."
echo ""
echo "To stop:  docker compose -f $LANGFUSE_DIR/docker-compose.yml down"
echo "To nuke:  docker compose -f $LANGFUSE_DIR/docker-compose.yml down -v"
