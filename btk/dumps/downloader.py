"""Fetch Planetarion-style dumps from the live game.

The current round's dumps live at fixed URLs under
``BTK_DUMP_LIVE_BASE_URL`` (default ``https://game.planetarion.com/botfiles``,
matching merlin.cfg's ``[URL] dumps``) -- there's no tick number in the URL,
since it's always "whatever tick it is right now"; the actual tick number
is read from each file's own "Tick:" header after parsing (see
btk/dumps/parser.py). Ship stats come from ``BTK_SHIPS_LIVE_URL`` (default
``https://game.planetarion.com/api.pl?stats``, matching merlin.cfg's
``[URL] ships``) and are available even before the round starts ticking.

See btk/dumps/status.py for checking whether the round is actually ticking
before calling fetch_live() -- the live botfile URLs 404 between rounds.
"""

import httpx

from btk.config import get_settings

LISTING_FILES = (
    "planet_listing.txt",
    "galaxy_listing.txt",
    "alliance_listing.txt",
    "user_feed.txt",
)


async def fetch_live() -> dict[str, str]:
    """Fetch the four current dump files directly from the live game."""
    settings = get_settings()
    base = settings.dump_live_base_url.rstrip("/")

    async with httpx.AsyncClient(timeout=30.0) as client:
        texts = {}
        for filename in LISTING_FILES:
            resp = await client.get(f"{base}/{filename}")
            resp.raise_for_status()
            texts[filename] = resp.text
    return texts


async def fetch_live_shipstats() -> list[dict]:
    """Fetch the live round's ship stats (JSON) from the game's stats API."""
    settings = get_settings()
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(settings.ships_live_url)
        resp.raise_for_status()
        return resp.json()
