"""Standalone calculator commands, ported from Merlin's Hooks/calcs/{roidcost,roidsave,bonus,launch}.py."""

import re
from datetime import UTC, datetime, timedelta

from discord.ext import commands

from btk.bot.format import num2short, short2num
from btk.bot.paconf import CLASS_ETA, GOVS, ROID_MINING, SHIP_VALUE, TICK_LENGTH
from btk.db import acquire

ROIDCOST_RE = re.compile(r"(\d+)\s+(\d+(?:\.\d+)?[km]?)(?:\s+(\d+))?$", re.IGNORECASE)
ROIDSAVE_RE = re.compile(r"(\d+)\s+(\d+)(?:\s+(\d+))?$")
LAUNCH_RE = re.compile(r"(\S+)\s+(\d+)$")


class Calcs(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def _current_tick(self, conn) -> int | None:
        return await conn.fetchval(
            """
            SELECT t.number FROM tick t
            JOIN round r ON r.id = t.round_id
            ORDER BY t.id DESC LIMIT 1
            """
        )

    @commands.command(name="roidcost")
    async def roidcost(self, ctx: commands.Context, *, arg: str) -> None:
        """Ticks to repay a value loss capping roids. Usage: !roidcost <roids> <value_cost> [mining_bonus]"""
        match = ROIDCOST_RE.match(arg.strip())
        if not match:
            await ctx.send("Usage: !roidcost <roids> <value_cost> [mining_bonus]")
            return
        raw_roids, raw_cost, raw_bonus = match.groups()
        roids = int(raw_roids)
        try:
            cost = short2num(raw_cost)
        except ValueError:
            await ctx.send(f"Couldn't parse number: {raw_cost}")
            return
        bonus_pct = int(raw_bonus or 0)

        if roids == 0:
            await ctx.send("Another NewDawn landing, eh?")
            return

        mining = ROID_MINING * ((bonus_pct + 100) / 100)
        ticks = (cost * SHIP_VALUE) // (roids * mining)
        reply = (
            f"Capping {roids} roids at {num2short(cost)} value with {bonus_pct}% bonus will repay "
            f"in {int(ticks)} ticks ({int(ticks / 24)} days)"
        )
        for gov in GOVS.values():
            if gov["prodcost"] == 0:
                continue
            ticks_b = ticks * (1 + gov["prodcost"])
            reply += f" {gov['name']}: {int(ticks_b)} ticks ({int(ticks_b / 24)} days)"
        await ctx.send(reply)

    @commands.command(name="roidsave")
    async def roidsave(self, ctx: commands.Context, *, arg: str) -> None:
        """Value mined by a number of roids in a number of ticks. Usage: !roidsave <roids> <ticks> [mining_bonus]"""
        match = ROIDSAVE_RE.match(arg.strip())
        if not match:
            await ctx.send("Usage: !roidsave <roids> <ticks> [mining_bonus]")
            return
        roids, ticks, raw_bonus = (int(g) for g in (match.group(1), match.group(2), match.group(3) or "0"))

        mining = ROID_MINING * (raw_bonus + 100) / 100
        value = ticks * roids * mining / SHIP_VALUE
        reply = f"In {ticks} ticks ({ticks // 24} days) {roids} roids with {raw_bonus}% bonus will mine {num2short(value)} value"
        for gov in GOVS.values():
            if gov["prodcost"] == 0:
                continue
            value_b = value / (1 + gov["prodcost"])
            reply += f" {gov['name']}: {num2short(value_b)} value"
        await ctx.send(reply)

    @commands.command(name="bonus")
    async def bonus(self, ctx: commands.Context, tick: int | None = None) -> None:
        """Upgrade bonus for a given tick, or the current tick. Usage: !bonus [tick]"""
        if tick is None:
            async with acquire() as conn:
                tick = await self._current_tick(conn)
            if tick is None:
                await ctx.send("Game is not ticking yet!")
                return

        resources = 10000 + tick * 4800
        roids = int(6 + tick * 0.15)
        research = 4000 + tick * 24
        construction = 2000 + tick * 18
        await ctx.send(
            f"Upgrade Bonus calculation for tick {tick} | Resources: {resources:,} EACH | "
            f"Roids: {roids:,} EACH | Research Points: {research:,} | Construction Units: {construction:,}"
        )

    @commands.command(name="launch")
    async def launch(self, ctx: commands.Context, *, arg: str) -> None:
        """Launch tick/time for a given ship class or eta, landing on a given tick.

        Usage: !launch <class|eta> <land_tick>
        """
        match = LAUNCH_RE.match(arg.strip())
        if not match:
            await ctx.send("Usage: !launch <class|eta> <land_tick>")
            return
        raw_eta, raw_land_tick = match.groups()
        land_tick = int(raw_land_tick)

        if raw_eta.lower() in CLASS_ETA:
            eta = CLASS_ETA[raw_eta.lower()]
        else:
            try:
                eta = int(raw_eta)
            except ValueError:
                await ctx.send(f"Invalid class or eta '{raw_eta}'")
                return

        async with acquire() as conn:
            current_tick = await self._current_tick(conn) or 0

        now = datetime.now(UTC)
        launch_tick = land_tick - eta
        launch_time = (
            now
            + timedelta(seconds=TICK_LENGTH * (launch_tick - current_tick + 1))
            - timedelta(minutes=now.minute % (TICK_LENGTH // 60) + 5)
        )
        prelaunch_tick = land_tick - eta + 1
        prelaunch_mod = launch_tick - current_tick

        await ctx.send(
            f"eta {eta} landing pt {land_tick} (currently {current_tick}) must launch at pt {launch_tick} "
            f"({launch_time.strftime('%m-%d %H:%M')}), or with prelaunch tick {prelaunch_tick} "
            f"(currently {prelaunch_mod:+d})"
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Calcs(bot))
