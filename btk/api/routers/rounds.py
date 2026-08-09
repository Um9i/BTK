from asyncpg import Connection
from fastapi import APIRouter, Depends, HTTPException

from btk.api.deps import db_conn, resolve_round_id
from btk.models.schemas import Round

router = APIRouter(prefix="/rounds", tags=["rounds"])


@router.get("", response_model=list[Round])
async def list_rounds(conn: Connection = Depends(db_conn)) -> list[Round]:
    rows = await conn.fetch("SELECT id, number, name, started_at, ended_at FROM round ORDER BY id")
    return [Round.model_validate(dict(row)) for row in rows]


@router.get("/{round_number}/latest-tick")
async def latest_tick(round_number: int, conn: Connection = Depends(db_conn)) -> dict:
    round_id = await resolve_round_id(conn, round_number)
    row = await conn.fetchrow(
        "SELECT id, number FROM tick WHERE round_id = $1 ORDER BY number DESC LIMIT 1", round_id
    )
    if row is None:
        raise HTTPException(status_code=404, detail="No ticks ingested for this round yet")
    return {"tick_id": row["id"], "number": row["number"]}
