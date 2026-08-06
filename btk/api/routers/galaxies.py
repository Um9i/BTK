from asyncpg import Connection
from fastapi import APIRouter, Depends, HTTPException

from btk.api.deps import db_conn

router = APIRouter(prefix="/galaxies", tags=["galaxies"])


@router.get("")
async def list_galaxies(round_id: int, tick_id: int | None = None, conn: Connection = Depends(db_conn)) -> list[dict]:
    if tick_id is None:
        tick_id = await conn.fetchval(
            "SELECT id FROM tick WHERE round_id = $1 ORDER BY number DESC LIMIT 1", round_id
        )
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
