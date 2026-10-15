"""Test 6: slow downstream call through the shared httpx client."""

import logging

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from .schemas import IdPath

log = logging.getLogger("bench")

router = APIRouter()


def new_client() -> httpx.AsyncClient:
    # httpx allows 100 connections (20 kept alive) per client by default. Test 6 needs ~1,000
    # requests in flight across the workers, so both limits are raised (docs/03).
    limits = httpx.Limits(max_connections=1024, max_keepalive_connections=1024)
    return httpx.AsyncClient(limits=limits)


class Stock(BaseModel):
    product_id: int
    available: int
    source: str


@router.get("/downstream/{id}")
async def inventory(id: IdPath, request: Request) -> Stock:
    try:
        resp = await request.state.http.get(f"{request.state.downstream_url}/inventory/{id}")
        resp.raise_for_status()
        available = resp.json()["available"]
    except (httpx.HTTPError, ValueError, KeyError) as e:
        log.error("downstream call failed: %r", e)
        raise HTTPException(502) from None
    return Stock(product_id=id, available=available, source="downstream")
