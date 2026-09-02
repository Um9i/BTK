"""Scan-request commands, adapted from Merlin's Hooks/scans/request.py.

Unlike Merlin, BTK doesn't ingest scan results itself (no Scan/PlanetScan
tables), so there's no automatic freshness checking or scan quota here --
just generating a link into the game's live scan page and posting it to a
shared Discord channel, plus a lightweight request queue for list/cancel.
"""

import re

import discord
import httpx
from discord.ext import commands

from btk.bot.access import is_admin
from btk.bot.paconf import SCAN_TYPES
from btk.config import get_settings
from btk.db import acquire

REQ_RE = re.compile(r"(\d+)[:.\-](\d+)[:.\-](\d+)\s+([A-Za-z]+)$")

# The game's own scan-result page -- whoever ran the scan just pastes this
# link in the scans channel, with no reference back to the request it
# fulfills. showscan.pl is publicly viewable (no game login needed) and its
# own heading states exactly what it's a scan of, e.g.
# "<h2 ...>Planet Scan on 1:1:2 in tick 18</h2>" -- that's the ground truth
# _match_request uses, rather than guessing from message order.
SCAN_RESULT_RE = re.compile(r"https?://\S*/showscan\.pl\S*", re.IGNORECASE)
BOT_REQUEST_ID_RE = re.compile(r"^\[(\d+)\]")
SCAN_HEADING_RE = re.compile(
    r"<h2[^>]*>\s*([A-Za-z ]+?)\s+Scan on\s+(\d+):(\d+):(\d+)\s+in tick", re.IGNORECASE
)
SCAN_TYPE_BY_NAME = {info["name"].lower(): code for code, info in SCAN_TYPES.items()}


class Scans(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def _current_round_and_tick(self, conn) -> tuple[int | None, int]:
        round_id = await conn.fetchval("SELECT id FROM round ORDER BY number DESC LIMIT 1")
        if round_id is None:
            return None, 0
        tick_number = await conn.fetchval(
            "SELECT number FROM tick WHERE round_id = $1 ORDER BY number DESC LIMIT 1", round_id
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
            await ctx.send(
                "Usage: !reqscan <x:y:z> <types e.g. PDUNJA> | list | cancel <id> [id ...]"
            )
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
        await ctx.send(
            f"Requested {len(ids)} scan(s) of {x}:{y}:{z} ({types}). !reqscan cancel {id_range} to cancel."
        )

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
        lines = [
            f"[{r['id']}] {r['x']}:{r['y']}:{r['z']} {SCAN_TYPES[r['scan_type']]['name']}"
            for r in rows
        ]
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
                row = await conn.fetchrow(
                    "SELECT requested_by, active FROM scan_request WHERE id = $1", req_id
                )
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

    @commands.Cog.listener("on_message")
    async def on_scan_result(self, message: discord.Message) -> None:
        """Whoever ran a requested scan just pastes the result link back into the scans
        channel -- relay it to whoever asked for it instead of leaving them to notice it."""
        if message.author.bot:
            return
        channel_id = get_settings().discord_scans_channel_id
        if not channel_id or message.channel.id != channel_id:
            return
        match = SCAN_RESULT_RE.search(message.content)
        if not match:
            return
        link = match.group(0)

        async with acquire() as conn:
            request = await self._match_request(conn, message, link)
            if request is None:
                return
            await conn.execute(
                "UPDATE scan_request SET active = false WHERE id = $1", request["id"]
            )

        scan_name = SCAN_TYPES[request["scan_type"]]["name"]
        notice = (
            f"{message.author.display_name} posted your {scan_name} scan of "
            f"{request['x']}:{request['y']}:{request['z']} -- {link}"
        )
        requester = self.bot.get_user(request["requested_by"]) or await self.bot.fetch_user(
            request["requested_by"]
        )
        try:
            await requester.send(notice)
        except discord.Forbidden:
            # DMs closed -- the link's already public in-channel, so just ping them there.
            await message.channel.send(f"{requester.mention} {notice}")
            return
        try:
            await message.add_reaction("\N{WHITE HEAVY CHECK MARK}")
        except discord.HTTPException:
            pass

    async def _fetch_scan_page(self, url: str) -> str | None:
        try:
            async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                return resp.text
        except httpx.HTTPError:
            return None

    def _parse_scan_page(self, html: str) -> dict | None:
        match = SCAN_HEADING_RE.search(html)
        if not match:
            return None
        name, x, y, z = match.groups()
        return {"name": name.strip(), "x": int(x), "y": int(y), "z": int(z)}

    async def _match_request(self, conn, message: discord.Message, link: str):
        """An explicit reply to the bot's own "[id] ... requested" post is unambiguous even
        with several open requests, so it's trusted outright. Otherwise, fetch the scan result
        itself and match on what it actually reports (coords + scan type) -- never a guess."""
        if message.reference and message.reference.message_id:
            ref = message.reference.resolved
            if ref is None:
                try:
                    ref = await message.channel.fetch_message(message.reference.message_id)
                except discord.HTTPException:
                    ref = None
            if isinstance(ref, discord.Message) and ref.author.bot:
                id_match = BOT_REQUEST_ID_RE.match(ref.content)
                if id_match:
                    row = await conn.fetchrow(
                        "SELECT id, requested_by, x, y, z, scan_type FROM scan_request WHERE id = $1 AND active",
                        int(id_match.group(1)),
                    )
                    if row is not None:
                        return row

        html = await self._fetch_scan_page(link)
        if html is None:
            return None
        parsed = self._parse_scan_page(html)
        if parsed is None:
            return None

        round_id, _ = await self._current_round_and_tick(conn)
        if round_id is None:
            return None

        scan_type = SCAN_TYPE_BY_NAME.get(parsed["name"].lower())
        if scan_type is not None:
            # The page named a scan type BTK recognizes -- match on it exactly rather than
            # falling back to coords-only, so a Development scan never gets attributed to an
            # open Planet-scan request at the same coords.
            return await conn.fetchrow(
                """
                SELECT id, requested_by, x, y, z, scan_type FROM scan_request
                WHERE round_id = $1 AND active AND x = $2 AND y = $3 AND z = $4 AND scan_type = $5
                ORDER BY id LIMIT 1
                """,
                round_id,
                parsed["x"],
                parsed["y"],
                parsed["z"],
                scan_type,
            )

        return await conn.fetchrow(
            """
            SELECT id, requested_by, x, y, z, scan_type FROM scan_request
            WHERE round_id = $1 AND active AND x = $2 AND y = $3 AND z = $4
            ORDER BY id LIMIT 1
            """,
            round_id,
            parsed["x"],
            parsed["y"],
            parsed["z"],
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Scans(bot))
