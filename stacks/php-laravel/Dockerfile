# syntax=docker/dockerfile:1
# Laravel on Nginx + PHP-FPM, one container (docs/03).
FROM php:8.5.11-fpm-trixie@sha256:adde18b67ea7a8cddc580f657e0d3f16e800758c6fa690b969c2925a303e8afc AS base
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends nginx libpq-dev; \
    docker-php-ext-install -j"$(nproc)" pdo_pgsql; \
    apt-mark manual libpq5; \
    apt-get purge -y --auto-remove libpq-dev; \
    rm -rf /var/lib/apt/lists/*

# Test 8 dataset, generated once at build time (docs/02 Appendix A). GMP only lives in this stage.
FROM base AS sales
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends libgmp-dev; \
    docker-php-ext-install gmp
COPY tools/gen-sales.php /tools/
RUN php /tools/gen-sales.php /sales_data.php

FROM composer:2.10.3@sha256:af98f42dfff7c68ba8d53c2164fd9fde1087b7d449514baa38c418b1f6bc4bac AS vendor
WORKDIR /app
COPY composer.json composer.lock ./
RUN --mount=type=cache,target=/tmp/cache \
    COMPOSER_CACHE_DIR=/tmp/cache composer install --no-dev --no-scripts --no-autoloader --no-interaction

FROM base
WORKDIR /var/www/html
RUN set -eux; \
    cp "$PHP_INI_DIR/php.ini-production" "$PHP_INI_DIR/php.ini"; \
    sed -i '/^access.log/d' /usr/local/etc/php-fpm.d/docker.conf; \
    mkdir -p /run/php
COPY docker/php.ini "$PHP_INI_DIR/conf.d/zz-bench.ini"
COPY docker/php-fpm.conf /usr/local/etc/php-fpm.d/zzz-bench.conf
COPY docker/nginx.conf /etc/nginx/nginx.conf
COPY docker/entrypoint.sh /usr/local/bin/bench-entrypoint

COPY --from=vendor /app/vendor ./vendor
COPY . .
COPY --from=sales /sales_data.php storage/bench/sales_data.php
# composer install -o (docs/03): the optimized autoloader and package discovery,
# then the route and event caches. config:cache runs at container start.
RUN --mount=type=bind,from=vendor,source=/usr/bin/composer,target=/usr/bin/composer \
    set -eux; \
    composer dump-autoload --optimize --no-dev --no-interaction; \
    php artisan route:cache; \
    php artisan event:cache; \
    chown -R www-data:www-data storage bootstrap/cache

ENV APP_ENV=production \
    APP_DEBUG=false \
    LOG_CHANNEL=stderr \
    LOG_LEVEL=warning \
    CACHE_STORE=array
EXPOSE 8080
ENTRYPOINT ["/usr/local/bin/bench-entrypoint"]
