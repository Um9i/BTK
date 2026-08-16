from asyncpg import Connection
from fastapi import APIRouter, Depends

from btk.api.deps import db_conn, require_user, resolve_round_id

router = APIRouter(prefix="/feed", tags=["feed"], dependencies=[Depends(require_user)])


@router.get("")
async def list_feed(
    round_number: int, tick_number: int | None = None, conn: Connection = Depends(db_conn)
) -> list[dict]:
    round_id = await resolve_round_id(conn, round_number)
    if tick_number is None:
        rows = await conn.fetch(
            "SELECT id, tick_number, category, text FROM feed WHERE round_id = $1 ORDER BY tick_number, id",
            round_id,
        )
    else:
        rows = await conn.fetch(
            """
            SELECT id, tick_number, category, text FROM feed
            WHERE round_id = $1 AND tick_number = $2
            ORDER BY id
            """,
            round_id,
            tick_number,
        )
    return [dict(row) for row in rows]
