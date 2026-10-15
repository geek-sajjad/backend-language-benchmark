<?php

namespace App\Http\Controllers;

use Illuminate\Http\JsonResponse;
use Illuminate\Support\Facades\Http;

class DownstreamController extends Controller
{
    // Test 6. PHP-FPM frees the cURL handle when the request ends, so every call
    // opens a new connection to the mock (deviation, see README).
    public function __invoke(string $id): JsonResponse
    {
        $productId = self::intParam($id);
        $inventory = Http::get(config('bench.downstream_url')."/inventory/{$productId}")->throw()->json();

        return response()->json([
            'product_id' => $productId,
            'available' => $inventory['available'],
            'source' => 'downstream',
        ]);
    }
}
