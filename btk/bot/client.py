"""Discord bot entry point."""

import asyncio
import logging

import discord
from discord.ext import commands

from btk.config import get_settings
from btk.db import close_pool, create_pool

log = logging.getLogger(__name__)

INITIAL_COGS = (
    "btk.bot.cogs.general",
    "btk.bot.cogs.game",
    "btk.bot.cogs.ships",
    "btk.bot.cogs.calcs",
)


class BTKBot(commands.Bot):
    async def setup_hook(self) -> None:
        await create_pool()
        for cog in INITIAL_COGS:
            await self.load_extension(cog)

    async def close(self) -> None:
        await close_pool()
        await super().close()


def build_bot() -> BTKBot:
    settings = get_settings()
    intents = discord.Intents.default()
    intents.message_content = True
    return BTKBot(command_prefix=settings.discord_command_prefix, intents=intents)


async def _main() -> None:
    logging.basicConfig(level=logging.INFO)
    settings = get_settings()
    bot = build_bot()
    async with bot:
        await bot.start(settings.discord_token.get_secret_value())


def run() -> None:
    asyncio.run(_main())


if __name__ == "__main__":
    run()
