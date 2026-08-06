from dataclasses import asdict

from fastapi import APIRouter

from btk.dumps.status import fetch_game_status

router = APIRouter(tags=["status"])


@router.get("/status/game")
async def game_status() -> dict:
    """Live status of the main Planetarion game (round, tick, ticking or not).

    Screen-scraped from https://www.planetarion.com/games/status/game/ --
    see btk/dumps/status.py.
    """
    return asdict(await fetch_game_status())
