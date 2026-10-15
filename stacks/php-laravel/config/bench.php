<?php

// Benchmark settings. `php artisan config:cache` runs when the container starts,
// so the PEM ends up in bootstrap/cache/config.php, which OPcache keeps in shared
// memory: the key file is read once at startup, as in the other stacks.
$keyFile = env('JWT_PUBLIC_KEY_FILE');

return [

    'downstream_url' => rtrim(env('DOWNSTREAM_URL', 'http://127.0.0.1:9000'), '/'),

    'jwt_public_key' => $keyFile && is_readable($keyFile) ? file_get_contents($keyFile) : null,

    // Test 8 dataset, written at image build time (docs/02 Appendix A).
    'sales_file' => storage_path('bench/sales_data.php'),

];
