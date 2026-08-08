"""Check whether the live game is actually ticking before fetching dumps.

https://www.planetarion.com/games/status/game/ is a plain (server-rendered,
no public JSON API) status page for the main game, refreshed by PA staff's
own tooling. As of writing it shows things like:

    Round 118 - Lost SoulS
    Ticker Status
        Current Tick: 0
        Tick Speed: 1 hour
        Ticking: No
        Last Tick Happened At 15:00 GMT on Thursday 6th August 2026
    Game Status
        Game is Open
        Signups are Open

This lets btk-ingest ask "is there actually a new tick to fetch?" instead
of blindly hitting the botfile URLs every hour on a timer and treating a
404 (round between rounds/not yet ticking, see
https://game.planetarion.com/botfiles/planet_listing.txt right now) as an
error. It's regex-scraped HTML, not a stable API -- brittle if PA changes
this page's markup, but there's nothing more structured to parse it from.
"""

import re
from dataclasses import dataclass

import httpx

from btk.config import get_settings


@dataclass
class GameStatus:
    round_number: int
    round_name: str
    current_tick: int
    tick_speed: str
    ticking: bool
    last_tick_at: str
    game_open: bool
    signups_open: bool


def parse_game_status(html: str) -> GameStatus:
    # The page has more than one "<li>Round N - name</li>" (e.g. a "Who's
    # Online" sidebar box lists every currently-running round the same way)
    # and more than one "highlight" span outside the section we care about
    # -- scope everything below to the `id="game_status"` box specifically,
    # not the whole page.
    section_match = re.search(r'<div id="game_status".*?</div>\s*</div>', html, re.DOTALL)
    if not section_match:
        raise ValueError("Could not find the #game_status section on the game status page")
    section = section_match.group(0)

    def field(label: str) -> str:
        m = re.search(rf'{re.escape(label)}\s*<span class="highlight">\s*(.*?)\s*</span>', section)
        if not m:
            raise ValueError(
                f"Could not find {label!r} in the game status page's #game_status section"
            )
        return m.group(1).strip()

    round_match = re.search(r"<li>Round (\d+) - (.+?)</li>", section)
    if not round_match:
        raise ValueError(
            "Could not find the round header in the game status page's #game_status section"
        )

    return GameStatus(
        round_number=int(round_match.group(1)),
        round_name=round_match.group(2).strip(),
        current_tick=int(field("Current Tick:")),
        tick_speed=field("Tick Speed:"),
        ticking=field("Ticking:").lower() == "yes",
        last_tick_at=field("Last Tick Happened At"),
        game_open=field("Game is").lower() == "open",
        signups_open=field("Signups are").lower() == "open",
    )


async def fetch_game_status() -> GameStatus:
    settings = get_settings()
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(settings.game_status_url)
        resp.raise_for_status()
        return parse_game_status(resp.text)
