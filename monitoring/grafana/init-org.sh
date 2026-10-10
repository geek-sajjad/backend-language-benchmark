#!/bin/sh
# Rename Grafana's default org (id 1) to "Benchmark". Idempotent. Run by grafana-init.
set -eu
url=http://127.0.0.1:3000
for _ in $(seq 1 60); do
  curl -fsS "$url/api/health" >/dev/null 2>&1 && break
  sleep 1
done
curl -fsS -u "admin:${GRAFANA_ADMIN_PASSWORD}" -X PUT -H 'Content-Type: application/json' \
  -d '{"name":"Benchmark"}' "$url/api/orgs/1"
echo
