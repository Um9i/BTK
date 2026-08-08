"""General-purpose commands, mainly as a scaffold example."""

from discord.ext import commands

from btk.db import acquire


class General(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @commands.command(name="ping")
    async def ping(self, ctx: commands.Context) -> None:
        """Check the bot is alive and show gateway latency."""
        await ctx.send(f"pong ({round(self.bot.latency * 1000)}ms)")

    @commands.command(name="dbcheck")
    async def dbcheck(self, ctx: commands.Context) -> None:
        """Check the bot can reach the database."""
        async with acquire() as conn:
            value = await conn.fetchval("SELECT 1")
        await ctx.send("db ok" if value == 1 else "db error")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(General(bot))
