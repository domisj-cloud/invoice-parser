#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

cd "${REPO_ROOT}"

echo "Starting invoice parser demo services..."
docker compose up -d --build

echo
echo "Waiting for parser service..."
for _ in {1..60}; do
  if curl -fsS "http://localhost:8000/health" >/dev/null 2>&1; then
    break
  fi
  sleep 2
done

if ! curl -fsS "http://localhost:8000/health" >/dev/null 2>&1; then
  echo "Parser service did not become healthy within 120 seconds." >&2
  docker compose ps
  exit 1
fi

echo
docker compose ps

cat <<'EOF'

Services are available at:
  Parser dashboard: http://localhost:8000
  Parser health:    http://localhost:8000/health
  MinIO console:    http://localhost:9001
  NiFi:             http://localhost:18080/nifi/

Credentials:
  MinIO: minioadmin / minioadmin
  NiFi:  admin / adminadminadmin
EOF
