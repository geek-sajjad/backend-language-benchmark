<?php

namespace App\Http\Controllers;

abstract class Controller
{
    /**
     * A path or query parameter as an int that fits Postgres' `integer`;
     * anything else is a malformed request (400, docs/02 "Endpoints").
     */
    protected static function intParam(?string $raw): int
    {
        if ($raw === null || ! ctype_digit($raw) || strlen($raw) > 10 || (int) $raw > 2147483647) {
            abort(400);
        }

        return (int) $raw;
    }
}
