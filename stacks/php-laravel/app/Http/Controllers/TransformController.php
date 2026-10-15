<?php

namespace App\Http\Controllers;

use Illuminate\Http\JsonResponse;
use Illuminate\Http\Request;

class TransformController extends Controller
{
    // Test 7: the cart is decoded into plain arrays (docs/02).
    public function __invoke(Request $request): JsonResponse
    {
        $cart = $request->json()->all();

        $groups = [];
        $total = 0;
        foreach ($cart['items'] as $item) {
            $unit = $item['pricing']['unit'];
            foreach ($item['pricing']['discounts'] as $discount) {
                $unit = $discount['kind'] === 'percent'
                    ? $unit - intdiv($unit * $discount['value'], 100)
                    : max(0, $unit - $discount['value']);
            }
            $lineTotal = $unit * $item['quantity'];
            $total += $lineTotal;

            $category = $item['product']['category'];
            $groups[$category] ??= ['category' => $category, 'count' => 0, 'subtotal_cents' => 0, 'top_items' => []];
            $groups[$category]['count']++;
            $groups[$category]['subtotal_cents'] += $lineTotal;
            $groups[$category]['top_items'][] = [
                'line_id' => $item['line_id'],
                'product_id' => $item['product']['id'],
                'name' => strtolower(preg_replace('/\s+/', ' ', trim($item['product']['name']))),
                'total_cents' => $lineTotal,
            ];
        }

        foreach ($groups as &$group) {
            usort($group['top_items'], fn ($a, $b) => [$b['total_cents'], $a['line_id']] <=> [$a['total_cents'], $b['line_id']]);
            $group['top_items'] = array_slice($group['top_items'], 0, 3);
        }
        unset($group);

        $categories = array_values($groups);
        usort($categories, fn ($a, $b) => [$b['subtotal_cents'], $a['category']] <=> [$a['subtotal_cents'], $b['category']]);

        return response()->json([
            'cart_id' => $cart['cart_id'],
            'currency' => $cart['currency'],
            'item_count' => count($cart['items']),
            'total_cents' => $total,
            'categories' => $categories,
        ]);
    }
}
