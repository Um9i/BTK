"""Member management commands (admin-only), loosely modeled on Merlin's Hooks/user/{adduser,remuser}.py."""

from discord.ext import commands

from btk.bot.access import admin_only, parse_user_id
from btk.config import get_settings
from btk.db import acquire


class Members(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @commands.command(name="adduser")
    @admin_only()
    async def adduser(self, ctx: commands.Context, *, arg: str) -> None:
        """Add a Discord user as a bot member. Usage: !adduser <user id | @mention>"""
        user_id = parse_user_id(arg)
        if user_id is None:
            await ctx.send("Usage: !adduser <discord user id or @mention>")
            return
        if user_id in get_settings().admin_ids:
            await ctx.send(f"<@{user_id}> is already an admin.")
            return

        async with acquire() as conn:
            inserted = await conn.fetchval(
                """
                INSERT INTO bot_member (discord_user_id, added_by) VALUES ($1, $2)
                ON CONFLICT DO NOTHING
                RETURNING discord_user_id
                """,
                user_id,
                ctx.author.id,
            )
        if inserted is None:
            await ctx.send(f"<@{user_id}> is already a member.")
        else:
            await ctx.send(f"Added <@{user_id}> as a bot member.")

    @commands.command(name="remuser")
    @admin_only()
    async def remuser(self, ctx: commands.Context, *, arg: str) -> None:
        """Remove a Discord user's bot membership. Usage: !remuser <user id | @mention>"""
        user_id = parse_user_id(arg)
        if user_id is None:
            await ctx.send("Usage: !remuser <discord user id or @mention>")
            return
        if user_id in get_settings().admin_ids:
            await ctx.send(f"<@{user_id}> is an admin (BTK_DISCORD_ADMIN_IDS) -- remove them from config instead.")
            return

        async with acquire() as conn:
            deleted = await conn.fetchval(
                "DELETE FROM bot_member WHERE discord_user_id = $1 RETURNING discord_user_id", user_id
            )
        if deleted is None:
            await ctx.send(f"<@{user_id}> is not a member.")
        else:
            await ctx.send(f"Removed <@{user_id}> as a bot member.")

    @commands.command(name="members")
    async def members(self, ctx: commands.Context) -> None:
        """List bot admins and members."""
        async with acquire() as conn:
            rows = await conn.fetch("SELECT discord_user_id FROM bot_member ORDER BY added_at")

        admin_lines = [f"<@{i}>" for i in get_settings().admin_ids]
        member_lines = [f"<@{r['discord_user_id']}>" for r in rows]
        await ctx.send(
            f"Admins: {', '.join(admin_lines) or 'none'} | Members: {', '.join(member_lines) or 'none'}"
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Members(bot))
