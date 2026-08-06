from asyncpg import Connection
from fastapi import APIRouter, Depends

from btk.api.deps import db_conn

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@router.get("/health/db")
async def health_db(conn: Connection = Depends(db_conn)) -> dict:
    value = await conn.fetchval("SELECT 1")
    return {"status": "ok", "db": value == 1}
