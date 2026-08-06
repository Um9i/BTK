"""FastAPI dependencies."""

from collections.abc import AsyncIterator

import asyncpg

from btk.db import get_pool


async def db_conn() -> AsyncIterator[asyncpg.Connection]:
    async with get_pool().acquire() as conn:
        yield conn
