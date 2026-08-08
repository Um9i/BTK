"""Scan-request commands, adapted from Merlin's Hooks/scans/request.py.

Unlike Merlin, BTK doesn't ingest scan results itself (no Scan/PlanetScan
tables), so there's no automatic freshness checking or scan quota here --
just generating a link into the game's live scan page and posting it to a
shared Discord channel, plus a lightweight request queue for list/cancel.
"""

import re

from discord.ext import commands

from btk.bot.access import is_admin
from btk.bot.paconf import SCAN_TYPES
from btk.config import get_settings
from btk.db import acquire

REQ_RE = re.compile(r"(\d+)[:.\-](\d+)[:.\-](\d+)\s+([A-Za-z]+)$")


class Scans(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def _current_round_and_tick(self, conn) -> tuple[int | None, int]:
        round_id = await conn.fetchval("SELECT id FROM round ORDER BY id DESC LIMIT 1")
        if round_id is None:
            return None, 0
        tick_number = await conn.fetchval(
            "SELECT number FROM tick WHERE round_id = $1 ORDER BY id DESC LIMIT 1", round_id
        )
        return round_id, tick_number or 0

    def _scan_link(self, scan_type: str, x: int, y: int, z: int) -> str:
        info = SCAN_TYPES[scan_type]
        return f"{get_settings().scan_request_base_url}?id={info['type']}&x={x}&y={y}&z={z}"

    @commands.command(name="reqscan", aliases=["req"])
    async def reqscan(self, ctx: commands.Context, *, arg: str) -> None:
        """Request planet scan(s). Usage: !reqscan <x:y:z> <types e.g. PDUNJA> | list | cancel <id> [id ...]"""
        arg = arg.strip()
        head, _, rest = arg.partition(" ")

        if head.lower() in ("l", "list"):
            await self._list(ctx)
            return
        if head.lower() in ("c", "cancel"):
            await self._cancel(ctx, rest)
            return

        match = REQ_RE.match(arg)
        if not match:
            await ctx.send("Usage: !reqscan <x:y:z> <types e.g. PDUNJA> | list | cancel <id> [id ...]")
            return

        x, y, z, types = match.groups()
        x, y, z = int(x), int(y), int(z)
        types = types.upper()

        unknown = [t for t in types if t not in SCAN_TYPES]
        if unknown:
            await ctx.send(f"Unknown or non-requestable scan type(s): {''.join(unknown)}")
            return

        channel_id = get_settings().discord_scans_channel_id
        channel = self.bot.get_channel(channel_id) if channel_id else None
        if channel is None:
            await ctx.send("Scans channel isn't configured or isn't reachable.")
            return

        async with acquire() as conn:
            round_id, tick_number = await self._current_round_and_tick(conn)
            if round_id is None:
                await ctx.send("No round data yet.")
                return

            ids = []
            for t in types:
                req_id = await conn.fetchval(
                    """
                    INSERT INTO scan_request (round_id, requested_by, x, y, z, scan_type, tick_number)
                    VALUES ($1, $2, $3, $4, $5, $6, $7)
                    RETURNING id
                    """,
                    round_id,
                    ctx.author.id,
                    x,
                    y,
                    z,
                    t,
                    tick_number,
                )
                ids.append(req_id)
                link = self._scan_link(t, x, y, z)
                await channel.send(
                    f"[{req_id}] {ctx.author.display_name} requested a {SCAN_TYPES[t]['name']} Scan "
                    f"of {x}:{y}:{z} -- {link}"
                )

        id_range = f"{ids[0]}-{ids[-1]}" if len(ids) > 1 else str(ids[0])
        await ctx.send(f"Requested {len(ids)} scan(s) of {x}:{y}:{z} ({types}). !reqscan cancel {id_range} to cancel.")

    async def _list(self, ctx: commands.Context) -> None:
        async with acquire() as conn:
            round_id, _ = await self._current_round_and_tick(conn)
            if round_id is None:
                await ctx.send("No round data yet.")
                return
            rows = await conn.fetch(
                "SELECT id, x, y, z, scan_type FROM scan_request WHERE round_id = $1 AND active ORDER BY id",
                round_id,
            )
        if not rows:
            await ctx.send("There are no open scan requests.")
            return
        lines = [f"[{r['id']}] {r['x']}:{r['y']}:{r['z']} {SCAN_TYPES[r['scan_type']]['name']}" for r in rows]
        await ctx.send("Open scan requests: " + " | ".join(lines))

    async def _cancel(self, ctx: commands.Context, ids_part: str) -> None:
        ids_part = ids_part.strip()
        if not ids_part:
            await ctx.send("Usage: !reqscan cancel <id> [id ...]")
            return
        try:
            ids = [int(i) for i in ids_part.split()]
        except ValueError:
            await ctx.send("Usage: !reqscan cancel <id> [id ...]")
            return

        cancelled, denied, missing = [], [], []
        admin = is_admin(ctx.author.id)
        async with acquire() as conn:
            for req_id in ids:
                row = await conn.fetchrow("SELECT requested_by, active FROM scan_request WHERE id = $1", req_id)
                if row is None or not row["active"]:
                    missing.append(req_id)
                    continue
                if row["requested_by"] != ctx.author.id and not admin:
                    denied.append(req_id)
                    continue
                await conn.execute("UPDATE scan_request SET active = false WHERE id = $1", req_id)
                cancelled.append(req_id)

        parts = []
        if cancelled:
            parts.append(f"Cancelled request(s): {', '.join(map(str, cancelled))}")
        if denied:
            parts.append(f"Not yours: {', '.join(map(str, denied))}")
        if missing:
            parts.append(f"No open request: {', '.join(map(str, missing))}")
        await ctx.send(" | ".join(parts) if parts else "Nothing to cancel.")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Scans(bot))
