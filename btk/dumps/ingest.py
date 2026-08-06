"""Load parsed dump rows (btk.dumps.parser) into Postgres.

Mirrors the shape of excalibur.py's per-tick pipeline -- resolve identity
rows (alliance/galaxy/planet) then write this tick's stat snapshot -- but
without Merlin's precomputed rank/growth columns; those are derived at
query time instead (see db/schema.sql's header comment).
"""

import asyncpg


async def get_or_create_round(
    conn: asyncpg.Connection, round_number: int, round_name: str | None = None
) -> int:
    round_id = await conn.fetchval(
        """
        INSERT INTO round (number, name) VALUES ($1, $2)
        ON CONFLICT (number) DO UPDATE SET name = COALESCE(EXCLUDED.name, round.name)
        RETURNING id
        """,
        round_number,
        round_name,
    )
    return round_id


async def create_tick(
    conn: asyncpg.Connection,
    round_id: int,
    number: int,
    *,
    etag: str | None = None,
    last_modified: str | None = None,
) -> int:
    return await conn.fetchval(
        """
        INSERT INTO tick (round_id, number, etag, last_modified)
        VALUES ($1, $2, $3, $4)
        ON CONFLICT (round_id, number)
        DO UPDATE SET etag = EXCLUDED.etag, last_modified = EXCLUDED.last_modified
        RETURNING id
        """,
        round_id,
        number,
        etag,
        last_modified,
    )


async def ingest_alliances(conn: asyncpg.Connection, round_id: int, tick_id: int, rows: list[dict]) -> None:
    if not rows:
        return

    names = sorted({row["name"] for row in rows})
    await conn.executemany(
        "INSERT INTO alliance (round_id, name) VALUES ($1, $2) ON CONFLICT (round_id, name) DO NOTHING",
        [(round_id, name) for name in names],
    )
    id_by_name = {
        r["name"]: r["id"]
        for r in await conn.fetch(
            "SELECT id, name FROM alliance WHERE round_id = $1 AND name = ANY($2::text[])", round_id, names
        )
    }

    await conn.executemany(
        """
        INSERT INTO alliance_stat
            (tick_id, alliance_id, rank, size, members, score, points, total_score, total_value)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
        ON CONFLICT (tick_id, alliance_id) DO UPDATE SET
            rank = EXCLUDED.rank, size = EXCLUDED.size, members = EXCLUDED.members,
            score = EXCLUDED.score, points = EXCLUDED.points,
            total_score = EXCLUDED.total_score, total_value = EXCLUDED.total_value
        """,
        [
            (
                tick_id,
                id_by_name[row["name"]],
                row["rank"],
                row["size"],
                row["members"],
                row["score"],
                row["points"],
                row["total_score"],
                row["total_value"],
            )
            for row in rows
        ],
    )


async def ingest_galaxies(
    conn: asyncpg.Connection, round_id: int, tick_id: int, rows: list[dict]
) -> dict[tuple[int, int], int]:
    """Returns the {(x, y): galaxy_id} map, so planet ingestion can attach galaxy_id."""
    if not rows:
        return {}

    coords = sorted({(row["x"], row["y"]) for row in rows})
    await conn.executemany(
        "INSERT INTO galaxy (round_id, x, y) VALUES ($1, $2, $3) ON CONFLICT (round_id, x, y) DO NOTHING",
        [(round_id, x, y) for x, y in coords],
    )
    galaxy_id_by_xy = {
        (r["x"], r["y"]): r["id"]
        for r in await conn.fetch("SELECT id, x, y FROM galaxy WHERE round_id = $1", round_id)
    }

    await conn.executemany(
        """
        INSERT INTO galaxy_stat (tick_id, galaxy_id, name, size, score, value, xp)
        VALUES ($1, $2, $3, $4, $5, $6, $7)
        ON CONFLICT (tick_id, galaxy_id) DO UPDATE SET
            name = EXCLUDED.name, size = EXCLUDED.size, score = EXCLUDED.score,
            value = EXCLUDED.value, xp = EXCLUDED.xp
        """,
        [
            (
                tick_id,
                galaxy_id_by_xy[(row["x"], row["y"])],
                row["name"],
                row["size"],
                row["score"],
                row["value"],
                row["xp"],
            )
            for row in rows
        ],
    )
    return galaxy_id_by_xy


async def ingest_planets(
    conn: asyncpg.Connection,
    round_id: int,
    tick_id: int,
    rows: list[dict],
    galaxy_id_by_xy: dict[tuple[int, int], int],
) -> None:
    if not rows:
        return

    external_ids = sorted({row["external_id"] for row in rows})
    await conn.executemany(
        "INSERT INTO planet (round_id, external_id) VALUES ($1, $2) ON CONFLICT (round_id, external_id) DO NOTHING",
        [(round_id, eid) for eid in external_ids],
    )
    planet_id_by_external_id = {
        r["external_id"]: r["id"]
        for r in await conn.fetch(
            "SELECT id, external_id FROM planet WHERE round_id = $1 AND external_id = ANY($2::text[])",
            round_id,
            external_ids,
        )
    }

    await conn.executemany(
        """
        INSERT INTO planet_stat
            (tick_id, planet_id, galaxy_id, x, y, z, planet_name, ruler_name, race, size, score, value, xp, special)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14)
        ON CONFLICT (tick_id, planet_id) DO UPDATE SET
            galaxy_id = EXCLUDED.galaxy_id, x = EXCLUDED.x, y = EXCLUDED.y, z = EXCLUDED.z,
            planet_name = EXCLUDED.planet_name, ruler_name = EXCLUDED.ruler_name, race = EXCLUDED.race,
            size = EXCLUDED.size, score = EXCLUDED.score, value = EXCLUDED.value, xp = EXCLUDED.xp,
            special = EXCLUDED.special
        """,
        [
            (
                tick_id,
                planet_id_by_external_id[row["external_id"]],
                galaxy_id_by_xy.get((row["x"], row["y"])),
                row["x"],
                row["y"],
                row["z"],
                row["planet_name"],
                row["ruler_name"],
                row["race"],
                row["size"],
                row["score"],
                row["value"],
                row["xp"],
                row["special"],
            )
            for row in rows
        ],
    )


async def ingest_feed(conn: asyncpg.Connection, round_id: int, rows: list[dict]) -> None:
    if not rows:
        return
    await conn.executemany(
        "INSERT INTO feed (round_id, tick_number, category, text) VALUES ($1, $2, $3, $4)",
        [(round_id, row["tick_number"], row["category"], row["text"]) for row in rows],
    )


async def ingest_tick(
    conn: asyncpg.Connection,
    round_number: int,
    round_name: str | None,
    *,
    planet_rows: list[dict],
    galaxy_rows: list[dict],
    alliance_rows: list[dict],
    feed_rows: list[dict],
    tick_number: int,
    etag: str | None = None,
    last_modified: str | None = None,
) -> int:
    """Ingest one tick's worth of already-parsed dump rows in a single transaction."""
    async with conn.transaction():
        round_id = await get_or_create_round(conn, round_number, round_name)
        tick_id = await create_tick(conn, round_id, tick_number, etag=etag, last_modified=last_modified)
        await ingest_alliances(conn, round_id, tick_id, alliance_rows)
        galaxy_id_by_xy = await ingest_galaxies(conn, round_id, tick_id, galaxy_rows)
        await ingest_planets(conn, round_id, tick_id, planet_rows, galaxy_id_by_xy)
        await ingest_feed(conn, round_id, feed_rows)
    return tick_id


async def ingest_shipstats(
    conn: asyncpg.Connection, round_number: int, round_name: str | None, rows: list[dict]
) -> None:
    """Store round-scoped ship stats once (see btk.dumps.parser.parse_shipstats).

    Unlike per-tick data, this replaces the round's whole ship table content
    each time it's run, matching shipstats.py's delete-then-reload behavior.
    """
    async with conn.transaction():
        round_id = await get_or_create_round(conn, round_number, round_name)
        await conn.execute("DELETE FROM ship WHERE round_id = $1", round_id)
        await conn.executemany(
            """
            INSERT INTO ship
                (round_id, name, class, race, type, metal, crystal, eonium, total_cost,
                 damage, armor, guns, initiative, empres, baseeta, target1, target2, target3)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, $18)
            """,
            [
                (
                    round_id,
                    row["name"],
                    row["class"],
                    row["race"],
                    row["type"],
                    row["metal"],
                    row["crystal"],
                    row["eonium"],
                    row["total_cost"],
                    row["damage"],
                    row["armor"],
                    row["guns"],
                    row["initiative"],
                    row["empres"],
                    row["baseeta"],
                    row["target1"],
                    row["target2"],
                    row["target3"],
                )
                for row in rows
            ],
        )
