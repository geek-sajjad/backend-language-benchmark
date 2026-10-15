"""Error bodies from docs/02-test-spec.md; conformance checks only the status code."""

from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

ERROR_CODES = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    422: "validation_failed",
    502: "downstream_failed",
}


def validation_failed(details: Any) -> HTTPException:
    return HTTPException(422, detail=details)


def error_response(status: int, details: Any = None) -> JSONResponse:
    body: dict[str, Any] = {"error": ERROR_CODES.get(status, "error")}
    if details is not None:
        body["details"] = details
    return JSONResponse(body, status_code=status)


async def http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
    return error_response(exc.status_code, exc.detail if exc.status_code == 422 else None)


async def request_validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
    # A malformed path or query parameter is a 400 (docs/02); a bad body is a 422.
    errors = exc.errors()
    if any(e["loc"] and e["loc"][0] in ("path", "query") for e in errors):
        return error_response(400, _plain(errors))
    return error_response(422, _plain(errors))


def _plain(errors) -> list[dict[str, Any]]:
    # Pydantic's error dicts can hold the original exception in "ctx", which isn't JSON.
    return [{"loc": e["loc"], "msg": e["msg"], "type": e["type"]} for e in errors]


def install(app: FastAPI) -> None:
    app.add_exception_handler(StarletteHTTPException, http_error)
    app.add_exception_handler(RequestValidationError, request_validation_error)
