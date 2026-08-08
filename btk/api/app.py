"""FastAPI application factory."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from starlette.staticfiles import StaticFiles

from btk.api.routers import alliances, feed, galaxies, health, planets, rounds, ships, status, web
from btk.config import get_settings
from btk.db import close_pool, create_pool

STATIC_DIR = Path(__file__).resolve().parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    await create_pool()
    try:
        yield
    finally:
        await close_pool()


def create_app() -> FastAPI:
    app = FastAPI(title="BTK API", version="0.1.0", lifespan=lifespan)
    app.include_router(health.router)
    app.include_router(status.router)
    app.include_router(rounds.router)
    app.include_router(alliances.router)
    app.include_router(planets.router)
    app.include_router(galaxies.router)
    app.include_router(ships.router)
    app.include_router(feed.router)
    app.include_router(web.router)
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app


app = create_app()


def run() -> None:
    settings = get_settings()
    uvicorn.run("btk.api.app:app", host=settings.api_host, port=settings.api_port, reload=True)


if __name__ == "__main__":
    run()
