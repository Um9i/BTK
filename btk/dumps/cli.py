"""Fetch + parse + ingest the live game's current tick / ship stats.

Usage:
    uv run btk-ingest live               # fetch + ingest the current live tick
    uv run btk-ingest live-shipstats     # fetch + ingest the live round's ship stats
"""

import asyncio
import sys

import asyncpg

from btk.config import get_settings
from btk.dumps import downloader
from btk.dumps.ingest import ingest_shipstats, ingest_tick
from btk.dumps.parser import (
    parse_alliance_listing,
    parse_galaxy_listing,
    parse_planet_listing,
    parse_shipstats,
    parse_user_feed,
)
from btk.dumps.status import fetch_game_status


async def ingest_live_once(conn: asyncpg.Connection) -> None:
    """Fetch the current live tick from the game and ingest it (intended to
    run once a minute after every hourly PA tick).

    Checks https://www.planetarion.com/games/status/game/ first and skips
    cleanly if the round isn't actually ticking right now (between rounds,
    signups-only, paused, ...) instead of blindly hitting the botfile URLs
    every time the timer fires and treating the resulting 404 as a failure.
    The round name/number are also read from that status check, rather than
    configured statically.
    """
    status = await fetch_game_status()
    if not status.ticking:
        print(
            f"live: round {status.round_number} ({status.round_name}) is not ticking "
            f"(current tick {status.current_tick}, last tick at {status.last_tick_at}) -- skipping"
        )
        return

    texts = await downloader.fetch_live()
    planet_text = texts.get("planet_listing.txt")
    galaxy_text = texts.get("galaxy_listing.txt")
    alliance_text = texts.get("alliance_listing.txt")
    feed_text = texts.get("user_feed.txt")
    if not (planet_text and galaxy_text and alliance_text):
        print("live: missing dump file(s), skipping")
        return

    dump_tick, planet_rows = parse_planet_listing(planet_text)
    _, galaxy_rows = parse_galaxy_listing(galaxy_text)
    _, alliance_rows = parse_alliance_listing(alliance_text)
    feed_rows = parse_user_feed(feed_text)[1] if feed_text else []

    await ingest_tick(
        conn,
        status.round_number,
        status.round_name,
        planet_rows=planet_rows,
        galaxy_rows=galaxy_rows,
        alliance_rows=alliance_rows,
        feed_rows=feed_rows,
        tick_number=dump_tick,
    )
    print(
        f"tick {dump_tick}: {len(planet_rows)} planets, {len(galaxy_rows)} galaxies, "
        f"{len(alliance_rows)} alliances, {len(feed_rows)} feed items"
    )


async def ingest_live_shipstats_once(conn: asyncpg.Connection) -> None:
    status = await fetch_game_status()
    ships = parse_shipstats(await downloader.fetch_live_shipstats())
    await ingest_shipstats(conn, status.round_number, status.round_name, ships)
    print(f"round {status.round_number} ({status.round_name}): {len(ships)} ships")


async def main() -> None:
    if len(sys.argv) < 2 or sys.argv[1] not in ("live", "live-shipstats"):
        print(__doc__)
        sys.exit(1)

    settings = get_settings()
    conn = await asyncpg.connect(dsn=settings.db_dsn)
    try:
        if sys.argv[1] == "live":
            await ingest_live_once(conn)
        else:
            await ingest_live_shipstats_once(conn)
    finally:
        await conn.close()


def run() -> None:
    asyncio.run(main())


if __name__ == "__main__":
    run()
