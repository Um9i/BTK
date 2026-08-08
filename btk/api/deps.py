"""FastAPI dependencies."""

from collections.abc import AsyncIterator

import asyncpg
from fastapi import Depends, HTTPException, Request

from btk.auth import SESSION_COOKIE_NAME
from btk.db import get_pool


async def db_conn() -> AsyncIterator[asyncpg.Connection]:
    async with get_pool().acquire() as conn:
        yield conn


async def current_user(
    request: Request, conn: asyncpg.Connection = Depends(db_conn)
) -> asyncpg.Record | None:
    """The logged-in web_credential row for this request's session cookie, or None.

    Expired sessions are lazily deleted here rather than via a cron/timer --
    traffic on this site is low enough that "clean up on next use" is simpler
    than a scheduled job, and an expired-but-unvisited session costs nothing
    just sitting in the table.
    """
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if not token:
        return None
    row = await conn.fetchrow(
        """
        SELECT c.discord_user_id, c.username
        FROM web_session s
        JOIN web_credential c ON c.discord_user_id = s.discord_user_id
        WHERE s.token = $1 AND s.expires_at > now()
        """,
        token,
    )
    if row is None:
        await conn.execute("DELETE FROM web_session WHERE token = $1", token)
        return None
    return row


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
