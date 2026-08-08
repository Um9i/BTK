"""FastAPI dependencies."""

from collections.abc import AsyncIterator

import asyncpg
from fastapi import HTTPException

from btk.db import get_pool


async def db_conn() -> AsyncIterator[asyncpg.Connection]:
    async with get_pool().acquire() as conn:
        yield conn


async def resolve_round_id(conn: asyncpg.Connection, round_number: int) -> int:
    """Look up a round's internal id from its game round number.

    Routers take the game's round number (what a user/bot actually knows)
    rather than the DB's internal round.id, so every list endpoint resolves
    through here instead of trusting a client-supplied internal id.
    """
    round_id = await conn.fetchval("SELECT id FROM round WHERE number = $1", round_number)
    if round_id is None:
        raise HTTPException(status_code=404, detail=f"No round {round_number}")
    return round_id


async def resolve_tick_id(
    conn: asyncpg.Connection, round_id: int, tick_number: int | None
) -> int | None:
    """Look up a tick's internal id from its game tick number, defaulting to the latest."""
    if tick_number is None:
        return await conn.fetchval(
            "SELECT id FROM tick WHERE round_id = $1 ORDER BY number DESC LIMIT 1", round_id
        )
    return await conn.fetchval(
        "SELECT id FROM tick WHERE round_id = $1 AND number = $2", round_id, tick_number
    )
