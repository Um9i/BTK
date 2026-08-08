"""Bot access control: two levels, admin and member.

Admins are config-only (BTK_DISCORD_ADMIN_IDS, comma-separated Discord user
IDs) -- no runtime promotion, so the bot can never be locked out of its own
admin commands. Members are managed at runtime via !adduser/!remuser
(admin-only) and stored in the bot_member table. Everyone else is ignored
entirely -- see global_member_check, registered as a bot-wide check in
btk/bot/client.py.
"""

import re

from discord.ext import commands

from btk.config import get_settings
from btk.db import acquire

MENTION_RE = re.compile(r"<@!?(\d+)>")


def parse_user_id(raw: str) -> int | None:
    raw = raw.strip()
    m = MENTION_RE.fullmatch(raw)
    if m:
        return int(m.group(1))
    return int(raw) if raw.isdigit() else None


def is_admin(discord_user_id: int) -> bool:
    return discord_user_id in get_settings().admin_ids


async def is_member(discord_user_id: int) -> bool:
    if is_admin(discord_user_id):
        return True
    async with acquire() as conn:
        return (
            await conn.fetchval(
                "SELECT 1 FROM bot_member WHERE discord_user_id = $1", discord_user_id
            )
            is not None
        )


class NotAMember(commands.CheckFailure):
    """Raised by the bot-wide membership check; handled silently (no reply)."""


async def global_member_check(ctx: commands.Context) -> bool:
    if not await is_member(ctx.author.id):
        raise NotAMember()
    return True


def admin_only():
    async def predicate(ctx: commands.Context) -> bool:
        if not is_admin(ctx.author.id):
            raise commands.CheckFailure("You must be a bot admin to use this command.")
        return True

    return commands.check(predicate)
