#!/usr/bin/env bash
# Write the Prometheus file_sd target files for the three benchmark hosts.
#   monitoring/prometheus/gen-targets.sh APP_IP DB_IP LOADGEN_IP
# Run on the loadgen host (private IPs). Prometheus picks the change up within 10 s.
# Locally: gen-targets.sh 127.0.0.1 127.0.0.1 127.0.0.1 reproduces the committed files.
set -euo pipefail

if [ $# -ne 3 ]; then
  echo "usage: $0 APP_IP DB_IP LOADGEN_IP" >&2
  exit 2
fi
app=$1 db=$2 loadgen=$3
dir="$(cd "$(dirname "$0")" && pwd)/targets"
mkdir -p "$dir"

# Write atomically so Prometheus never reads a half-written file.
write() {
  local file="$dir/$1"
  cat > "$file.tmp"
  mv "$file.tmp" "$file"
}

write node.json <<JSON
[
  { "targets": ["$app:9100"], "labels": { "host": "app" } },
  { "targets": ["$db:9100"], "labels": { "host": "db" } },
  { "targets": ["$loadgen:9100"], "labels": { "host": "loadgen" } }
]
JSON

write cadvisor.json <<JSON
[
  { "targets": ["$app:8081"], "labels": { "host": "app" } }
]
JSON

write postgres.json <<JSON
[
  { "targets": ["$db:9187"], "labels": { "host": "db" } }
]
JSON

echo "wrote $dir/{node,cadvisor,postgres}.json (app=$app db=$db loadgen=$loadgen)"
