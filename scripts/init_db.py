"""Create the BTK database schema (idempotent - safe to re-run).

Usage:
    uv run btk-initdb
"""

import asyncio
from pathlib import Path

import asyncpg

from btk.config import get_settings

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "db" / "schema.sql"


async def init_db() -> None:
    settings = get_settings()
    conn = await asyncpg.connect(dsn=settings.db_dsn)
    try:
        await conn.execute(SCHEMA_PATH.read_text())
        print(f"Schema applied from {SCHEMA_PATH}")
    finally:
        await conn.close()


def main() -> None:
    asyncio.run(init_db())


if __name__ == "__main__":
    main()
