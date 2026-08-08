from asyncpg import Connection
from fastapi import APIRouter, Depends, HTTPException

from btk.api.deps import db_conn, resolve_round_id, resolve_tick_id

router = APIRouter(prefix="/planets", tags=["planets"])


@router.get("")
async def list_planets(
    round_number: int, tick_number: int | None = None, conn: Connection = Depends(db_conn)
) -> list[dict]:
    round_id = await resolve_round_id(conn, round_number)
    tick_id = await resolve_tick_id(conn, round_id, tick_number)
    if tick_id is None:
        return []

    rows = await conn.fetch(
        """
        SELECT p.id, p.external_id, s.x, s.y, s.z, s.planet_name, s.ruler_name,
               s.race, s.size, s.score, s.value, s.xp, s.special
        FROM planet_stat s
        JOIN planet p ON p.id = s.planet_id
        WHERE s.tick_id = $1
        ORDER BY s.score DESC
        """,
        tick_id,
    )
    return [dict(row) for row in rows]


@router.get("/{planet_id}/history")
async def planet_history(planet_id: int, conn: Connection = Depends(db_conn)) -> list[dict]:
    rows = await conn.fetch(
        """
        SELECT t.number AS tick, s.x, s.y, s.z, s.planet_name, s.ruler_name,
               s.race, s.size, s.score, s.value, s.xp, s.special
        FROM planet_stat s
        JOIN tick t ON t.id = s.tick_id
        WHERE s.planet_id = $1
        ORDER BY t.number
        """,
        planet_id,
    )
    if not rows:
        raise HTTPException(status_code=404, detail="No stats found for this planet")
    return [dict(row) for row in rows]
