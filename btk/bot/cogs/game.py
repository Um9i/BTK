"""Game-status commands."""

from discord.ext import commands

from btk.db import acquire
from btk.dumps.status import fetch_game_status


class Game(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @commands.command(name="round")
    async def round_(self, ctx: commands.Context) -> None:
        """Show the live round status (ticking or not, current tick)."""
        status = await fetch_game_status()
        ticking = "ticking" if status.ticking else "not ticking"
        await ctx.send(
            f"Round {status.round_number} ({status.round_name}) -- {ticking}, "
            f"current tick {status.current_tick} ({status.tick_speed}/tick)"
        )

    @commands.command(name="tick")
    async def tick(self, ctx: commands.Context) -> None:
        """Show the most recently ingested tick."""
        async with acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT r.number AS round_number, r.name AS round_name,
                       t.number AS tick_number, t.inserted_at
                FROM tick t
                JOIN round r ON r.id = t.round_id
                ORDER BY t.id DESC
                LIMIT 1
                """
            )
        if row is None:
            await ctx.send("No ticks ingested yet.")
            return
        await ctx.send(
            f"Latest ingested: round {row['round_number']} ({row['round_name']}) "
            f"tick {row['tick_number']}, at {row['inserted_at']:%Y-%m-%d %H:%M UTC}"
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Game(bot))
