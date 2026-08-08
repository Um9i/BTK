"""!pref -- lets a bot member set their website login password.

There's no signup form on the website itself; a Discord-verified bot member
(see btk/bot/access.py) running this command is the only way to get a login,
and it doubles as proof the account belongs to them.
"""

import asyncio

import discord
from discord.ext import commands

from btk.auth import hash_password
from btk.db import acquire

MIN_PASSWORD_LENGTH = 8


class Preferences(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def _scrub_invoking_message(self, ctx: commands.Context) -> None:
        """Best-effort delete of the message that just posted a plaintext password in a channel."""
        if isinstance(ctx.channel, discord.DMChannel):
            return
        try:
            await ctx.message.delete()
        except discord.HTTPException:
            pass

    async def _notify(self, ctx: commands.Context, message: str) -> None:
        """DM the reply, falling back to the channel if DMs are closed -- a failed DM must never
        leave the command silently unanswered."""
        if isinstance(ctx.channel, discord.DMChannel):
            await ctx.send(message)
            return
        try:
            await ctx.author.send(message)
        except discord.Forbidden:
            await ctx.send(f"{ctx.author.mention} {message}", delete_after=15)
        else:
            try:
                await ctx.send(f"{ctx.author.mention} check your DMs.", delete_after=10)
            except discord.Forbidden:
                pass

    @commands.command(name="pref")
    async def pref(self, ctx: commands.Context, *, password: str) -> None:
        """Set your website login password. Usage: !pref <password> -- send this as a DM to the bot so the password never sits in a channel."""
        password = password.strip()
        await self._scrub_invoking_message(ctx)

        if len(password) < MIN_PASSWORD_LENGTH:
            await self._notify(
                ctx, f"Password must be at least {MIN_PASSWORD_LENGTH} characters. Try again."
            )
            return

        password_hash = await asyncio.to_thread(hash_password, password)
        async with acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO web_credential (discord_user_id, username, password_hash)
                VALUES ($1, $2, $3)
                ON CONFLICT (discord_user_id) DO UPDATE SET password_hash = $3, updated_at = now()
                RETURNING username
                """,
                ctx.author.id,
                ctx.author.name,
                password_hash,
            )

        await self._notify(
            ctx, f"Password set. Log in at the website with username `{row['username']}`."
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Preferences(bot))
