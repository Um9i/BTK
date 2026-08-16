from asyncpg import Connection
from fastapi import APIRouter, Depends

from btk.api.deps import db_conn, require_user

router = APIRouter(prefix="/ships", tags=["ships"], dependencies=[Depends(require_user)])


@router.get("")
async def list_ships(round_id: int, conn: Connection = Depends(db_conn)) -> list[dict]:
    rows = await conn.fetch(
        """
        SELECT id, name, class, race, type, metal, crystal, eonium, total_cost,
               damage, armor, guns, initiative, empres, baseeta, target1, target2, target3
        FROM ship
        WHERE round_id = $1
        ORDER BY name
        """,
        round_id,
    )
    return [dict(row) for row in rows]
