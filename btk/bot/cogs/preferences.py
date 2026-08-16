"""!pref -- lets a bot member set their website login password.
!link/!unlink -- lets a bot member point their account at their own planet,
so the website's homepage can show a personalized "you" panel.

There's no signup form on the website itself; a Discord-verified bot member
(see btk/bot/access.py) running this command is the only way to get a login,
and it doubles as proof the account belongs to them.
"""

import asyncio
import re

import discord
from discord.ext import commands

from btk.auth import hash_password
from btk.db import acquire

MIN_PASSWORD_LENGTH = 8
PLANET_COORD_RE = re.compile(r"(\d+)[:.\s]+(\d+)[:.\s]+(\d+)")


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

    @commands.command(name="link")
    async def link(self, ctx: commands.Context, *, coords: str) -> None:
        """Point your account at your own planet. Usage: !link <x:y:z>

        Trusted as self-reported, same as every other member-only command
        (see btk/bot/access.py) -- there's no in-game ownership proof to
        check against. planet_id points at a specific round's planet row
        (see db/schema.sql), so this needs re-running each new round.
        """
        match = PLANET_COORD_RE.fullmatch(coords.strip())
        if not match:
            await ctx.send("Usage: !link <x:y:z>")
            return
        x, y, z = (int(v) for v in match.groups())

        async with acquire() as conn:
            round_id = await conn.fetchval("SELECT id FROM round ORDER BY id DESC LIMIT 1")
            if round_id is None:
                await ctx.send("No round data yet.")
                return
            tick_id = await conn.fetchval(
                "SELECT id FROM tick WHERE round_id = $1 ORDER BY number DESC LIMIT 1", round_id
            )
            if tick_id is None:
                await ctx.send("No ticks ingested yet.")
                return
            planet_id = await conn.fetchval(
                "SELECT planet_id FROM planet_stat WHERE tick_id = $1 AND x = $2 AND y = $3 AND z = $4",
                tick_id,
                x,
                y,
                z,
            )
            if planet_id is None:
                await ctx.send(f"No planet with coords {x}:{y}:{z} found")
                return
            await conn.execute(
                """
                INSERT INTO discord_link (discord_user_id, planet_id)
                VALUES ($1, $2)
                ON CONFLICT (discord_user_id) DO UPDATE SET planet_id = $2, linked_at = now()
                """,
                ctx.author.id,
                planet_id,
            )
        await ctx.send(f"Linked to {x}:{y}:{z}. Your homepage will show this planet's stats.")

    @commands.command(name="unlink")
    async def unlink(self, ctx: commands.Context) -> None:
        """Remove your planet link. Usage: !unlink"""
        async with acquire() as conn:
            await conn.execute(
                "UPDATE discord_link SET planet_id = NULL WHERE discord_user_id = $1", ctx.author.id
            )
        await ctx.send("Unlinked.")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Preferences(bot))
