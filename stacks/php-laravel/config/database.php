<?php

// Only the settings that differ from the framework's defaults
// (vendor/laravel/framework/config/database.php); the rest is merged in.
return [

    'default' => env('DB_CONNECTION', 'pgsql'),

    'connections' => [

        // The spec's variable names (DB_NAME, DB_USER) instead of Laravel's
        // DB_DATABASE / DB_USERNAME. No `search_path`: setting one makes the
        // connector run `set search_path` on every new connection, and `public`
        // is already Postgres' default. Laravel opens one connection per request
        // (no persistent PDO, docs/03).
        'pgsql' => [
            'driver' => 'pgsql',
            'host' => env('DB_HOST', '127.0.0.1'),
            'port' => env('DB_PORT', '5432'),
            'database' => env('DB_NAME', 'bench'),
            'username' => env('DB_USER', 'bench'),
            'password' => env('DB_PASSWORD', 'bench'),
            'charset' => 'utf8',
            'prefix' => '',
            'prefix_indexes' => true,
            'sslmode' => 'disable',
        ],

    ],

];
