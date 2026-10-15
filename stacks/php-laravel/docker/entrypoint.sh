#!/bin/bash
# Starts PHP-FPM and Nginx in one container (the memory metric needs a single `app`
# container, docs/04). Exits when either process dies.
set -euo pipefail
cd /var/www/html

# config:cache freezes env(), so it runs here with the runner's DB_HOST,
# DOWNSTREAM_URL and the mounted JWT key, not at build time.
php artisan config:cache --quiet

php-fpm --nodaemonize &
fpm=$!
nginx -g 'daemon off;' &
web=$!

trap 'kill -TERM "$fpm" "$web" 2>/dev/null; wait' TERM INT
wait -n "$fpm" "$web"
status=$?
kill -TERM "$fpm" "$web" 2>/dev/null || true
exit "$status"
