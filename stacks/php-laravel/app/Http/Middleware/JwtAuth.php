<?php

namespace App\Http\Middleware;

use Closure;
use Firebase\JWT\JWT;
use Firebase\JWT\Key;
use Illuminate\Http\Request;
use Symfony\Component\HttpFoundation\Response;

/**
 * Test 9: RS256 bearer token. php-jwt checks the signature, that the token's
 * `alg` matches the key's, and `exp`/`nbf`/`iat` when present; issuer, audience
 * and the presence of `exp` and `sub` are checked here. The customer id from
 * `sub` is stored in the request attributes for the controllers.
 */
class JwtAuth
{
    public function handle(Request $request, Closure $next): Response
    {
        $token = $request->bearerToken();
        if ($token === null) {
            abort(401);
        }

        try {
            $claims = JWT::decode($token, new Key(config('bench.jwt_public_key'), 'RS256'));
        } catch (\Throwable) {
            abort(401);
        }

        $sub = $claims->sub ?? null;
        $aud = (array) ($claims->aud ?? []);
        if (! isset($claims->exp)
            || ($claims->iss ?? null) !== 'bench-idp'
            || ! in_array('bench-api', $aud, true)
            || ! is_string($sub) || ! ctype_digit($sub) || strlen($sub) > 10) {
            abort(401);
        }

        $request->attributes->set('customer_id', (int) $sub);

        return $next($request);
    }
}
