<?php

namespace App\Http\Controllers;

use App\Http\Requests\CreateOrderRequest;
use App\Models\Order;
use App\Models\OrderItem;
use App\Models\Product;
use Illuminate\Database\QueryException;
use Illuminate\Http\JsonResponse;
use Illuminate\Http\Request;
use Illuminate\Support\Facades\DB;
use Illuminate\Validation\ValidationException;

class OrderController extends Controller
{
    // Test 4: 3 queries (orders, their items, the items' products).
    public function forCustomer(Request $request, string $id): JsonResponse
    {
        $customerId = self::intParam($id);
        $authenticated = $request->attributes->get('customer_id');
        if ($authenticated !== null && $authenticated !== $customerId) {
            abort(403);
        }

        $orders = Order::where('customer_id', $customerId)
            ->orderByDesc('created_at')
            ->orderByDesc('id')
            ->limit(10)
            ->with(['items' => fn ($q) => $q->orderBy('id'), 'items.product:id,name'])
            ->get();

        return response()->json([
            'customer_id' => $customerId,
            'orders' => $orders->map(fn (Order $o) => [
                'id' => $o->id,
                'status' => $o->status,
                'total_cents' => $o->total_cents,
                'created_at' => $o->created_at->toJSON(),
                'items' => $o->items->map(fn (OrderItem $i) => [
                    'product_id' => $i->product_id,
                    'name' => $i->product->name,
                    'unit_price_cents' => $i->unit_price_cents,
                    'quantity' => $i->quantity,
                ]),
            ]),
        ]);
    }

    // Test 5: validate, then one transaction.
    public function store(CreateOrderRequest $request): JsonResponse
    {
        $data = $request->validated();
        $customerId = $request->attributes->get('customer_id') ?? $data['customer_id'];
        // Ascending product_id: concurrent orders lock rows in the same order.
        $lines = collect($data['items'])->sortBy('product_id')->values();

        try {
            [$order, $products] = DB::transaction(function () use ($customerId, $lines) {
                $products = Product::whereIn('id', $lines->pluck('product_id'))
                    ->get(['id', 'name', 'price_cents'])
                    ->keyBy('id');
                if ($products->count() !== $lines->count()) {
                    throw ValidationException::withMessages(['items' => 'unknown product']);
                }

                $order = Order::create([
                    'customer_id' => $customerId,
                    'status' => 'pending',
                    'total_cents' => $lines->sum(fn ($l) => $products[$l['product_id']]->price_cents * $l['quantity']),
                ]);

                // One multi-row INSERT for the items.
                OrderItem::insert($lines->map(fn ($l) => [
                    'order_id' => $order->id,
                    'product_id' => $l['product_id'],
                    'quantity' => $l['quantity'],
                    'unit_price_cents' => $products[$l['product_id']]->price_cents,
                ])->all());

                foreach ($lines as $l) {
                    Product::whereKey($l['product_id'])->decrement('stock', $l['quantity']);
                }

                return [$order, $products];
            });
        } catch (QueryException $e) {
            if ($e->getCode() === '23503') { // foreign_key_violation: unknown customer
                throw ValidationException::withMessages(['customer_id' => 'unknown customer']);
            }
            throw $e;
        }

        return response()->json([
            'id' => $order->id,
            'status' => $order->status,
            'total_cents' => $order->total_cents,
            'created_at' => $order->created_at->toJSON(),
            'items' => $lines->map(fn ($l) => [
                'product_id' => $l['product_id'],
                'name' => $products[$l['product_id']]->name,
                'unit_price_cents' => $products[$l['product_id']]->price_cents,
                'quantity' => $l['quantity'],
            ]),
        ], 201);
    }
}
