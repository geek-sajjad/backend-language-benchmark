"""FastAPI app. Uvicorn runs one copy per worker process; each builds its own state at startup."""

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI
from fastapi.responses import PlainTextResponse

from . import aggregate, catalog, db, downstream, errors, orders, transform
from .auth import load_public_key, require_auth

logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[dict]:
    # Uvicorn starts serving only after this startup completes, so /health is 200 once
    # the JWT key, the sales dataset and the DB pool are ready.
    jwt_key = load_public_key(os.environ.get("JWT_PUBLIC_KEY_FILE", "/run/secrets/jwt_public.pem"))
    sales = aggregate.build_sales()
    engine, sessions = await db.connect()
    async with downstream.new_client() as http:
        yield {
            "jwt_key": jwt_key,
            "sales": sales,
            "sessions": sessions,
            "http": http,
            "downstream_url": os.environ.get("DOWNSTREAM_URL", "http://127.0.0.1:9000").rstrip("/"),
        }
    await engine.dispose()


# No interactive docs: a production API doesn't serve them.
app = FastAPI(lifespan=lifespan, openapi_url=None, docs_url=None, redoc_url=None)
errors.install(app)


@app.get("/health", response_class=PlainTextResponse)
async def health() -> str:
    return "ok"


# Served both unprotected (tests 2–8) and under /secure (test 9).
api = APIRouter()
for module in (catalog, downstream, transform, aggregate):
    api.include_router(module.router)

app.include_router(api)
app.include_router(orders.public)
app.include_router(api, prefix="/secure", dependencies=[require_auth])
app.include_router(orders.secure)
