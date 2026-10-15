<?php

namespace App\Http\Controllers;

use Illuminate\Http\JsonResponse;
use Illuminate\Http\Request;

class AggregateController extends Controller
{
    // Test 8. The 50,000 rows are an immutable array in OPcache shared memory
    // (preloaded); `require` returns it without copying (docs/02 Appendix A).
    // Row layout: [id, region, category, customer_tier, amount_cents, quantity, day].
    public function __invoke(Request $request): JsonResponse
    {
        $region = self::intParam($request->query('region'));
        $minAmount = self::intParam($request->query('min_amount'));
        $sales = require config('bench.sales_file');

        $groups = [];
        foreach ($sales as [, $saleRegion, $category, , $amount, $quantity]) {
            if ($saleRegion !== $region || $amount < $minAmount) {
                continue;
            }
            if (! isset($groups[$category])) {
                $groups[$category] = [
                    'category' => $category, 'count' => 0, 'sum_amount' => 0,
                    'min_amount' => $amount, 'max_amount' => $amount, 'avg_amount' => 0, 'total_quantity' => 0,
                ];
            }
            $g = &$groups[$category];
            $g['count']++;
            $g['sum_amount'] += $amount;
            if ($amount < $g['min_amount']) {
                $g['min_amount'] = $amount;
            }
            if ($amount > $g['max_amount']) {
                $g['max_amount'] = $amount;
            }
            $g['total_quantity'] += $quantity;
            unset($g);
        }

        foreach ($groups as &$g) {
            $g['avg_amount'] = intdiv($g['sum_amount'], $g['count']);
        }
        unset($g);

        $rows = array_values($groups);
        usort($rows, fn ($a, $b) => [$b['sum_amount'], $a['category']] <=> [$a['sum_amount'], $b['category']]);

        return response()->json([
            'region' => $region,
            'min_amount' => $minAmount,
            'groups' => array_slice($rows, 0, 10),
        ]);
    }
}
