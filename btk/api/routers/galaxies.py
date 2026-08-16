from asyncpg import Connection
from fastapi import APIRouter, Depends, HTTPException

from btk.api.deps import db_conn, require_user, resolve_round_id, resolve_tick_id

router = APIRouter(prefix="/galaxies", tags=["galaxies"], dependencies=[Depends(require_user)])


@router.get("")
async def list_galaxies(
    round_number: int, tick_number: int | None = None, conn: Connection = Depends(db_conn)
) -> list[dict]:
    round_id = await resolve_round_id(conn, round_number)
    tick_id = await resolve_tick_id(conn, round_id, tick_number)
    if tick_id is None:
        return []

    rows = await conn.fetch(
        """
        SELECT g.id, g.x, g.y, s.name, s.size, s.score, s.value, s.xp
        FROM galaxy_stat s
        JOIN galaxy g ON g.id = s.galaxy_id
        WHERE s.tick_id = $1
        ORDER BY s.score DESC
        """,
        tick_id,
    )
    return [dict(row) for row in rows]


@router.get("/{galaxy_id}/history")
async def galaxy_history(galaxy_id: int, conn: Connection = Depends(db_conn)) -> list[dict]:
    rows = await conn.fetch(
        """
        SELECT t.number AS tick, s.name, s.size, s.score, s.value, s.xp
        FROM galaxy_stat s
        JOIN tick t ON t.id = s.tick_id
        WHERE s.galaxy_id = $1
        ORDER BY t.number
        """,
        galaxy_id,
    )
    if not rows:
        raise HTTPException(status_code=404, detail="No stats found for this galaxy")
    return [dict(row) for row in rows]
