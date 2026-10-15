<?php

namespace App\Http\Controllers;

use Illuminate\Http\JsonResponse;
use Illuminate\Support\Facades\DB;

class HealthController extends Controller
{
    // 200 once the DB answers, the JWT key is loaded and the sales file exists.
    public function __invoke(): JsonResponse
    {
        $ready = config('bench.jwt_public_key') !== null && is_file(config('bench.sales_file'));
        try {
            DB::select('SELECT 1');
        } catch (\Throwable) {
            $ready = false;
        }

        return response()->json(['status' => $ready ? 'ok' : 'starting'], $ready ? 200 : 503);
    }
}
