"""Async engine and the per-request session dependency."""

import asyncio
import logging
import os
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy import URL
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

log = logging.getLogger("bench")


def pool_size_per_worker() -> int:
    # DB_POOL_SIZE is the stack's total (64), split evenly across the Uvicorn workers.
    # The entrypoint sets WEB_CONCURRENCY (Uvicorn's worker count) to the number of cores.
    workers = int(os.environ.get("WEB_CONCURRENCY", "1"))
    return max(1, int(os.environ.get("DB_POOL_SIZE", "64")) // workers)


async def connect() -> tuple[AsyncEngine, async_sessionmaker[AsyncSession]]:
    url = URL.create(
        "postgresql+asyncpg",
        username=os.environ.get("DB_USER", "bench"),
        password=os.environ.get("DB_PASSWORD", "bench"),
        host=os.environ.get("DB_HOST", "127.0.0.1"),
        port=int(os.environ.get("DB_PORT", "5432")),
        database=os.environ.get("DB_NAME", "bench"),
    )
    engine = create_async_engine(url, pool_size=pool_size_per_worker(), max_overflow=0)

    # The DB may still be starting (compose up, DB reset): retry for up to a minute.
    for attempt in range(1, 61):
        try:
            async with engine.connect():
                break
        except (OSError, ConnectionError) as e:
            if attempt == 60:
                raise
            log.warning("database not reachable yet (%s), retrying", e)
            await asyncio.sleep(1)

    # expire_on_commit=False: SQLAlchemy's asyncio docs recommend it, so attributes stay readable
    # after commit without another (implicit) query.
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    async with request.state.sessions() as session:
        yield session


Session = Annotated[AsyncSession, Depends(get_session)]
