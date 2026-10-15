<?php

use App\Http\Controllers\AggregateController;
use App\Http\Controllers\CatalogController;
use App\Http\Controllers\DownstreamController;
use App\Http\Controllers\HealthController;
use App\Http\Controllers\OrderController;
use App\Http\Controllers\TransformController;
use App\Http\Middleware\JwtAuth;
use Illuminate\Support\Facades\Route;

$endpoints = function () {
    Route::get('/products/{id}', [CatalogController::class, 'show']);
    Route::get('/categories/{id}/products', [CatalogController::class, 'byCategory']);
    Route::get('/customers/{id}/orders', [OrderController::class, 'forCustomer']);
    Route::post('/orders', [OrderController::class, 'store']);
    Route::get('/downstream/{id}', DownstreamController::class);
    Route::post('/transform', TransformController::class);
    Route::get('/aggregate', AggregateController::class);
};

// Tests 2-8 call the plain routes, test 9 the same endpoints under /secure.
Route::group([], $endpoints);
Route::prefix('secure')->middleware(JwtAuth::class)->group($endpoints);

Route::get('/health', HealthController::class);
