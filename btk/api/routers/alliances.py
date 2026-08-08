from asyncpg import Connection
from fastapi import APIRouter, Depends, HTTPException

from btk.api.deps import db_conn, resolve_round_id, resolve_tick_id

router = APIRouter(prefix="/alliances", tags=["alliances"])


@router.get("")
async def list_alliances(
    round_number: int, tick_number: int | None = None, conn: Connection = Depends(db_conn)
) -> list[dict]:
    round_id = await resolve_round_id(conn, round_number)
    tick_id = await resolve_tick_id(conn, round_id, tick_number)
    if tick_id is None:
        return []

    rows = await conn.fetch(
        """
        SELECT a.id, a.name, s.rank, s.size, s.members, s.score, s.points, s.total_score, s.total_value
        FROM alliance_stat s
        JOIN alliance a ON a.id = s.alliance_id
        WHERE s.tick_id = $1
        ORDER BY s.rank
        """,
        tick_id,
    )
    return [dict(row) for row in rows]


@router.get("/{alliance_id}/history")
async def alliance_history(alliance_id: int, conn: Connection = Depends(db_conn)) -> list[dict]:
    rows = await conn.fetch(
        """
        SELECT t.number AS tick, s.rank, s.size, s.members, s.score, s.points, s.total_score, s.total_value
        FROM alliance_stat s
        JOIN tick t ON t.id = s.tick_id
        WHERE s.alliance_id = $1
        ORDER BY t.number
        """,
        alliance_id,
    )
    if not rows:
        raise HTTPException(status_code=404, detail="No stats found for this alliance")
    return [dict(row) for row in rows]
