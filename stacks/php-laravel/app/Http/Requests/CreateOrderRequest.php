<?php

namespace App\Http\Requests;

use Illuminate\Foundation\Http\FormRequest;

class CreateOrderRequest extends FormRequest
{
    public function rules(): array
    {
        // `integer:strict` rejects "42" and 1.5 (plain `integer` accepts "42").
        // The upper bounds keep ids inside Postgres' `integer`. Under /secure the
        // customer comes from the token and any body customer_id is ignored.
        $rules = [
            'items' => ['required', 'list', 'min:1', 'max:10'],
            'items.*' => ['required', 'array'],
            'items.*.product_id' => ['required', 'integer:strict', 'min:1', 'max:2147483647', 'distinct'],
            'items.*.quantity' => ['required', 'integer:strict', 'between:1,10'],
        ];

        if (! $this->attributes->has('customer_id')) {
            $rules['customer_id'] = ['required', 'integer:strict', 'min:1', 'max:2147483647'];
        }

        return $rules;
    }
}
