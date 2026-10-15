<?php

use Illuminate\Foundation\Application;
use Illuminate\Foundation\Configuration\Exceptions;
use Illuminate\Foundation\Configuration\Middleware;
use Illuminate\Validation\ValidationException;
use Symfony\Component\HttpKernel\Exception\HttpException;
use Symfony\Component\HttpKernel\Exception\NotFoundHttpException;

return Application::configure(basePath: dirname(__DIR__))
    ->withRouting(
        // The API group has no session/CSRF middleware; the spec's paths have no /api prefix.
        api: __DIR__.'/../routes/api.php',
        apiPrefix: '',
    )
    ->withMiddleware(function (Middleware $middleware): void {
        //
    })
    ->withExceptions(function (Exceptions $exceptions): void {
        // JSON for every request, never a redirect or an HTML error page.
        $exceptions->shouldRenderJsonWhen(fn () => true);

        // The spec's error bodies (docs/02 "Endpoints"). ModelNotFoundException
        // arrives here already turned into a NotFoundHttpException.
        $exceptions->render(fn (ValidationException $e) => response()->json(
            ['error' => 'validation_failed', 'details' => $e->errors()], 422,
        ));
        $exceptions->render(fn (NotFoundHttpException $e) => response()->json(['error' => 'not_found'], 404));
        $exceptions->render(fn (HttpException $e) => response()->json(
            ['error' => match ($e->getStatusCode()) {
                400 => 'bad_request',
                401 => 'unauthorized',
                403 => 'forbidden',
                default => 'http_error',
            }], $e->getStatusCode(),
        ));
    })->create();
