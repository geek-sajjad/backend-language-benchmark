<?php

namespace App\Models;

use Illuminate\Database\Eloquent\Model;
use Illuminate\Database\Eloquent\Relations\HasMany;

class Order extends Model
{
    // Eloquent fills created_at from the app clock on insert; the table has no updated_at.
    const UPDATED_AT = null;

    protected $fillable = ['customer_id', 'status', 'total_cents'];

    public function items(): HasMany
    {
        return $this->hasMany(OrderItem::class);
    }
}
