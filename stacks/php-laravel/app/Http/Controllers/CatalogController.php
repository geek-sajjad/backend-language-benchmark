<?php

namespace App\Http\Controllers;

use App\Models\Product;
use Illuminate\Http\JsonResponse;
use Illuminate\Http\Request;

class CatalogController extends Controller
{
    // Test 2: one SELECT by primary key, every column.
    public function show(string $id): JsonResponse
    {
        return response()->json(Product::findOrFail(self::intParam($id)));
    }

    // Test 3: one keyset page of 50 full models, `description` loaded but not returned.
    public function byCategory(Request $request, string $id): JsonResponse
    {
        $categoryId = self::intParam($id);
        $after = $request->has('after') ? self::intParam($request->query('after')) : 0;

        $products = Product::where('category_id', $categoryId)
            ->where('id', '>', $after)
            ->orderBy('id')
            ->limit(50)
            ->get();

        return response()->json([
            'category_id' => $categoryId,
            'next_after' => $products->last()?->id,
            'items' => $products->map(fn (Product $p) => [
                'id' => $p->id,
                'sku' => $p->sku,
                'name' => $p->name,
                'price_cents' => $p->price_cents,
                'stock' => $p->stock,
                'created_at' => $p->created_at->toJSON(),
            ]),
        ]);
    }
}
