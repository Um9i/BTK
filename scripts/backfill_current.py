"""Backfill missing ticks of the *current* live round from dumps.dfwtk.com's
recent-ticks mirror.

Usage:
    uv run btk-backfill-current                 # every missing tick, 1..current_tick
    uv run btk-backfill-current --from 1 --to 600
    uv run btk-backfill-current --keep-going     # don't stop on a fetch error

This mirror serves recent ticks of whichever round is currently live
directly by tick number -- https://dumps.dfwtk.com/<tick>/<file>.txt --
unlike btk/dumps/archive.py's r<round>/<tick>/ layout, which is for
backfilling a *past*, finished round from a different archive host
(BTK_ARCHIVE_BASE_URL). This script is for catching up the live round when
the hourly btk-ingest live timer wasn't running for a stretch of ticks.

Ticks already present in the `tick` table for the current round are skipped,
so re-running after an interruption is cheap and doesn't re-fetch anything.
"""

import argparse
import asyncio

import asyncpg
import httpx

from btk.config import get_settings
from btk.dumps.downloader import LISTING_FILES
from btk.dumps.ingest import ingest_tick
from btk.dumps.parser import (
    parse_alliance_listing,
    parse_galaxy_listing,
    parse_planet_listing,
    parse_user_feed,
)
from btk.dumps.status import fetch_game_status

MIRROR_BASE_URL = "https://dumps.dfwtk.com"

_RETRY_ATTEMPTS = 4
_RETRY_BACKOFF_SECONDS = 2.0


async def _get_with_retry(client: httpx.AsyncClient, url: str) -> httpx.Response:
    for attempt in range(1, _RETRY_ATTEMPTS + 1):
        try:
            resp = await client.get(url)
            resp.raise_for_status()
            return resp
        except (httpx.TransportError, httpx.HTTPStatusError) as exc:
            if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code < 500:
                raise  # 404 etc. -- retrying won't help
            if attempt == _RETRY_ATTEMPTS:
                raise
            await asyncio.sleep(_RETRY_BACKOFF_SECONDS * attempt)
    raise AssertionError("unreachable")


async def fetch_mirror_tick(client: httpx.AsyncClient, tick_number: int) -> dict[str, str]:
    """Fetch the four dump files for one tick from the recent-ticks mirror."""
    texts = {}
    for filename in LISTING_FILES:
        resp = await _get_with_retry(client, f"{MIRROR_BASE_URL}/{tick_number}/{filename}")
        texts[filename] = resp.text
    return texts


async def backfill(
    conn: asyncpg.Connection,
    round_number: int,
    round_name: str | None,
    tick_range: range,
    *,
    keep_going: bool,
) -> None:
    already_done = {
        r["number"]
        for r in await conn.fetch(
            "SELECT t.number FROM tick t JOIN round r ON t.round_id = r.id WHERE r.number = $1",
            round_number,
        )
    }
    ticks = [t for t in tick_range if t not in already_done]
    print(
        f"round {round_number}: {len(tick_range)} ticks requested, "
        f"{len(already_done)} already ingested, {len(ticks)} remaining"
    )

    async with httpx.AsyncClient(timeout=30.0) as client:
        for i, tick_number in enumerate(ticks, start=1):
            try:
                texts = await fetch_mirror_tick(client, tick_number)
            except httpx.HTTPStatusError as exc:
                print(f"[{i}/{len(ticks)}] tick {tick_number}: {exc.response.status_code}, skipping")
                if keep_going:
                    continue
                raise

            dump_tick, planet_rows = parse_planet_listing(texts["planet_listing.txt"])
            _, galaxy_rows = parse_galaxy_listing(texts["galaxy_listing.txt"])
            _, alliance_rows = parse_alliance_listing(texts["alliance_listing.txt"])
            feed_rows = parse_user_feed(texts["user_feed.txt"])[1]

            await ingest_tick(
                conn,
                round_number,
                round_name,
                planet_rows=planet_rows,
                galaxy_rows=galaxy_rows,
                alliance_rows=alliance_rows,
                feed_rows=feed_rows,
                tick_number=dump_tick,
            )
            print(
                f"[{i}/{len(ticks)}] tick {dump_tick}: {len(planet_rows)} planets, "
                f"{len(galaxy_rows)} galaxies, {len(alliance_rows)} alliances, "
                f"{len(feed_rows)} feed items"
            )


async def run(args: argparse.Namespace) -> None:
    status = await fetch_game_status()
    lo = args.start if args.start is not None else 1
    hi = args.end if args.end is not None else status.current_tick
    print(f"round {status.round_number} ({status.round_name}), current tick {status.current_tick}")

    settings = get_settings()
    conn = await asyncpg.connect(dsn=settings.db_dsn)
    try:
        await backfill(
            conn,
            status.round_number,
            status.round_name,
            range(lo, hi + 1),
            keep_going=args.keep_going,
        )
    finally:
        await conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from", dest="start", type=int, default=None, help="first tick (default 1)")
    parser.add_argument(
        "--to", dest="end", type=int, default=None, help="last tick (default: current live tick)"
    )
    parser.add_argument(
        "--keep-going",
        action="store_true",
        help="don't stop the whole run on one tick's fetch error",
    )
    asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    main()
