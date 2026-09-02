"""Leaderboard, growth, and search commands, ported from a subset of
Merlin's target-search/lookup hooks (top10/last10/exp/value/search/info).

Unlike Merlin, BTK has no idle-tick or fleet-scan data, so this only covers
the pieces derivable from planet_stat/alliance_stat/planet_intel alone.
"""

from itertools import pairwise

from discord.ext import commands

from btk.bot.format import num2short
from btk.db import acquire

# Column names are interpolated directly into SQL (order-by target can't be
# parametrized), so only ever allow one of these known-safe literals in.
METRIC_COLUMNS = {"score": "score", "value": "value", "size": "size", "xp": "xp"}
RATIO_COLUMNS = {"score": "score", "value": "value"}
HISTORY_TICKS = 15
LEADERBOARD_LIMIT = 10
SEARCH_LIMIT = 25
MOVERS_DEFAULT_TICKS = 24


def _parse_leaderboard_args(rest: str) -> tuple[str, str | None, str | None]:
    """Tokens in any order: score|value|size|xp (default score), alliance=<name>, race=<name>."""
    metric = "score"
    alliance = None
    race = None
    for token in rest.split():
        key, sep, val = token.partition("=")
        if sep and key.lower() == "alliance":
            alliance = val
        elif sep and key.lower() == "race":
            race = val
        elif token.lower() in METRIC_COLUMNS:
            metric = token.lower()
    return metric, alliance, race


def _parse_movers_args(rest: str) -> tuple[str, int, str | None, str | None]:
    """Tokens in any order: score|value|size|xp (default score), ticks=<n> (default
    MOVERS_DEFAULT_TICKS), alliance=<name>, race=<name>."""
    metric = "score"
    ticks = MOVERS_DEFAULT_TICKS
    alliance = None
    race = None
    for token in rest.split():
        key, sep, val = token.partition("=")
        if sep and key.lower() == "alliance":
            alliance = val
        elif sep and key.lower() == "race":
            race = val
        elif sep and key.lower() == "ticks" and val.isdigit():
            ticks = int(val)
        elif token.lower() in METRIC_COLUMNS:
            metric = token.lower()
    return metric, ticks, alliance, race


def _parse_ratio_args(rest: str) -> tuple[str, str | None, str | None]:
    """Tokens in any order: score|value (default score, ratio is always vs. size),
    alliance=<name>, race=<name>."""
    metric = "score"
    alliance = None
    race = None
    for token in rest.split():
        key, sep, val = token.partition("=")
        if sep and key.lower() == "alliance":
            alliance = val
        elif sep and key.lower() == "race":
            race = val
        elif token.lower() in RATIO_COLUMNS:
            metric = token.lower()
    return metric, alliance, race


def _format_mover_row(rank: int, row) -> str:
    sign = "+" if row["delta"] >= 0 else ""
    line = f"{rank:>2}. {row['x']}:{row['y']}:{row['z']} '{row['ruler_name']}' of '{row['planet_name']}'"
    if row["alliance"]:
        line += f" [{row['alliance']}]"
    line += f" -- {sign}{num2short(row['delta'])} (now {num2short(row['cur_value'])})"
    return line


def _format_ratio_row(rank: int, row) -> str:
    line = f"{rank:>2}. {row['x']}:{row['y']}:{row['z']} '{row['ruler_name']}' of '{row['planet_name']}'"
    if row["alliance"]:
        line += f" [{row['alliance']}]"
    line += f" -- Ratio: {row['ratio']:.2f} (Size: {row['size']})"
    return line


def _format_leaderboard_row(rank: int, row) -> str:
    line = f"{rank:>2}. {row['x']}:{row['y']}:{row['z']} '{row['ruler_name']}' of '{row['planet_name']}'"
    if row["alliance"]:
        line += f" [{row['alliance']}]"
    line += (
        f" -- Score: {num2short(row['score'])} Value: {num2short(row['value'])} "
        f"Size: {row['size']} XP: {num2short(row['xp'])}"
    )
    return line


class Stats(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def _current_round_id(self, conn) -> int | None:
        return await conn.fetchval("SELECT id FROM round ORDER BY id DESC LIMIT 1")

    async def _latest_tick(self, conn, round_id: int):
        return await conn.fetchrow(
            "SELECT id, number FROM tick WHERE round_id = $1 ORDER BY number DESC LIMIT 1",
            round_id,
        )

    async def _leaderboard(self, ctx: commands.Context, arg: str, ascending: bool) -> None:
        metric, alliance, race = _parse_leaderboard_args(arg or "")
        column = METRIC_COLUMNS[metric]
        direction = "ASC" if ascending else "DESC"

        async with acquire() as conn:
            round_id = await self._current_round_id(conn)
            if round_id is None:
                await ctx.send("No round data yet.")
                return
            tick = await self._latest_tick(conn, round_id)
            if tick is None:
                await ctx.send("No ticks ingested yet.")
                return

            rows = await conn.fetch(
                f"""
                SELECT ps.x, ps.y, ps.z, ps.planet_name, ps.ruler_name, ps.race,
                       ps.size, ps.score, ps.value, ps.xp, pi.alliance
                FROM planet_stat ps
                LEFT JOIN planet_intel pi ON pi.planet_id = ps.planet_id
                WHERE ps.tick_id = $1
                  AND ($2::text IS NULL OR pi.alliance ILIKE $2)
                  AND ($3::text IS NULL OR ps.race ILIKE $3)
                ORDER BY ps.{column} {direction}
                LIMIT {LEADERBOARD_LIMIT}
                """,
                tick["id"],
                alliance,
                race,
            )
        if not rows:
            await ctx.send("No planets matched.")
            return
        lines = [_format_leaderboard_row(i, row) for i, row in enumerate(rows, start=1)]
        await ctx.send(f"Tick {tick['number']} by {metric}:\n```\n" + "\n".join(lines) + "\n```")

    @commands.command(name="top10")
    async def top10(self, ctx: commands.Context, *, arg: str = "") -> None:
        """Top 10 planets by score/value/size/xp. Usage: !top10 [alliance=<name>] [race=<name>] [score|value|size|xp]"""
        await self._leaderboard(ctx, arg, ascending=False)

    @commands.command(name="last10")
    async def last10(self, ctx: commands.Context, *, arg: str = "") -> None:
        """Bottom 10 planets by score/value/size/xp. Usage: !last10 [alliance=<name>] [race=<name>] [score|value|size|xp]"""
        await self._leaderboard(ctx, arg, ascending=True)

    async def _growth(self, ctx: commands.Context, arg: str, metric: str) -> None:
        parts = arg.strip().replace(".", ":").split(":")
        if len(parts) != 3 or not all(p.isdigit() for p in parts):
            await ctx.send(f"Usage: !{metric} <x:y:z>")
            return
        x, y, z = (int(p) for p in parts)
        column = METRIC_COLUMNS[metric]

        async with acquire() as conn:
            round_id = await self._current_round_id(conn)
            if round_id is None:
                await ctx.send("No round data yet.")
                return
            planet_id = await conn.fetchval(
                """
                SELECT ps.planet_id
                FROM planet_stat ps
                JOIN tick t ON t.id = ps.tick_id
                WHERE t.round_id = $1 AND ps.x = $2 AND ps.y = $3 AND ps.z = $4
                ORDER BY t.number DESC
                LIMIT 1
                """,
                round_id,
                x,
                y,
                z,
            )
            if planet_id is None:
                await ctx.send(f"No planet with coords {x}:{y}:{z} found")
                return

            rows = await conn.fetch(
                f"""
                SELECT t.number AS tick_number, ps.{column} AS value
                FROM planet_stat ps
                JOIN tick t ON t.id = ps.tick_id
                WHERE ps.planet_id = $1
                ORDER BY t.number DESC
                LIMIT {HISTORY_TICKS}
                """,
                planet_id,
            )
        rows = list(reversed(rows))
        if len(rows) < 2:
            await ctx.send(f"Not enough tick history for {x}:{y}:{z} yet.")
            return

        lines = []
        for prev, cur in pairwise(rows):
            delta = cur["value"] - prev["value"]
            sign = "+" if delta >= 0 else ""
            lines.append(
                f"Tick {cur['tick_number']}: {num2short(cur['value'])} ({sign}{num2short(delta)})"
            )
        total_gain = rows[-1]["value"] - rows[0]["value"]
        await ctx.send(
            f"{metric.title()} for {x}:{y}:{z} over last {len(rows)} ticks "
            f"(total {'+' if total_gain >= 0 else ''}{num2short(total_gain)}):\n```\n"
            + "\n".join(lines)
            + "\n```"
        )

    async def _movers(self, ctx: commands.Context, arg: str, ascending: bool) -> None:
        metric, ticks, alliance, race = _parse_movers_args(arg or "")
        column = METRIC_COLUMNS[metric]
        direction = "ASC" if ascending else "DESC"

        async with acquire() as conn:
            round_id = await self._current_round_id(conn)
            if round_id is None:
                await ctx.send("No round data yet.")
                return
            tick = await self._latest_tick(conn, round_id)
            if tick is None:
                await ctx.send("No ticks ingested yet.")
                return
            old_tick_id = await conn.fetchval(
                """
                SELECT id FROM tick WHERE round_id = $1
                ORDER BY number DESC OFFSET $2 LIMIT 1
                """,
                round_id,
                ticks,
            )
            if old_tick_id is None:
                await ctx.send(f"Not enough tick history for a {ticks}-tick window yet.")
                return

            rows = await conn.fetch(
                f"""
                SELECT cur.x, cur.y, cur.z, cur.planet_name, cur.ruler_name, pi.alliance,
                       cur.{column} AS cur_value,
                       (cur.{column} - old.{column}) AS delta
                FROM planet_stat cur
                JOIN planet_stat old ON old.planet_id = cur.planet_id AND old.tick_id = $2
                LEFT JOIN planet_intel pi ON pi.planet_id = cur.planet_id
                WHERE cur.tick_id = $1
                  AND ($3::text IS NULL OR pi.alliance ILIKE $3)
                  AND ($4::text IS NULL OR cur.race ILIKE $4)
                ORDER BY delta {direction}
                LIMIT {LEADERBOARD_LIMIT}
                """,
                tick["id"],
                old_tick_id,
                alliance,
                race,
            )
        if not rows:
            await ctx.send("No planets matched.")
            return
        lines = [_format_mover_row(i, row) for i, row in enumerate(rows, start=1)]
        await ctx.send(
            f"Tick {tick['number']} -- {metric} change over last {ticks} ticks:\n```\n"
            + "\n".join(lines)
            + "\n```"
        )

    @commands.command(name="gainers")
    async def gainers(self, ctx: commands.Context, *, arg: str = "") -> None:
        """Top 10 planets by score/value/size/xp gained over N ticks. Usage: !gainers [ticks=24] [alliance=<name>] [race=<name>] [score|value|size|xp]"""
        await self._movers(ctx, arg, ascending=False)

    @commands.command(name="crashers")
    async def crashers(self, ctx: commands.Context, *, arg: str = "") -> None:
        """Top 10 planets by score/value/size/xp lost over N ticks. Usage: !crashers [ticks=24] [alliance=<name>] [race=<name>] [score|value|size|xp]"""
        await self._movers(ctx, arg, ascending=True)

    @commands.command(name="ratio")
    async def ratio(self, ctx: commands.Context, *, arg: str = "") -> None:
        """Top 10 planets by score/size or value/size ratio. Usage: !ratio [alliance=<name>] [race=<name>] [score|value]"""
        metric, alliance, race = _parse_ratio_args(arg or "")
        column = RATIO_COLUMNS[metric]

        async with acquire() as conn:
            round_id = await self._current_round_id(conn)
            if round_id is None:
                await ctx.send("No round data yet.")
                return
            tick = await self._latest_tick(conn, round_id)
            if tick is None:
                await ctx.send("No ticks ingested yet.")
                return

            rows = await conn.fetch(
                f"""
                SELECT ps.x, ps.y, ps.z, ps.planet_name, ps.ruler_name, ps.size, pi.alliance,
                       (ps.{column}::float / ps.size) AS ratio
                FROM planet_stat ps
                LEFT JOIN planet_intel pi ON pi.planet_id = ps.planet_id
                WHERE ps.tick_id = $1
                  AND ps.size > 0
                  AND ($2::text IS NULL OR pi.alliance ILIKE $2)
                  AND ($3::text IS NULL OR ps.race ILIKE $3)
                ORDER BY ratio DESC
                LIMIT {LEADERBOARD_LIMIT}
                """,
                tick["id"],
                alliance,
                race,
            )
        if not rows:
            await ctx.send("No planets matched.")
            return
        lines = [_format_ratio_row(i, row) for i, row in enumerate(rows, start=1)]
        await ctx.send(
            f"Tick {tick['number']} by {metric}/size ratio:\n```\n" + "\n".join(lines) + "\n```"
        )

    @commands.command(name="xp")
    async def xp(self, ctx: commands.Context, *, arg: str) -> None:
        """XP gain of a planet over the last 15 ticks. Usage: !xp <x:y:z>"""
        await self._growth(ctx, arg, "xp")

    @commands.command(name="value")
    async def value(self, ctx: commands.Context, *, arg: str) -> None:
        """Value growth of a planet over the last 15 ticks. Usage: !value <x:y:z>"""
        await self._growth(ctx, arg, "value")

    @commands.command(name="search")
    async def search(self, ctx: commands.Context, *, arg: str) -> None:
        """Search planets by scouted alliance or nick. Usage: !search <alliance|nick>"""
        needle = arg.strip()
        if not needle:
            await ctx.send("Usage: !search <alliance|nick>")
            return

        async with acquire() as conn:
            round_id = await self._current_round_id(conn)
            if round_id is None:
                await ctx.send("No round data yet.")
                return
            tick = await self._latest_tick(conn, round_id)
            if tick is None:
                await ctx.send("No ticks ingested yet.")
                return

            rows = await conn.fetch(
                f"""
                SELECT ps.x, ps.y, ps.z, ps.planet_name, ps.ruler_name, pi.alliance, pi.nick
                FROM planet_intel pi
                JOIN planet_stat ps ON ps.planet_id = pi.planet_id AND ps.tick_id = $1
                WHERE pi.alliance ILIKE $2 OR pi.nick ILIKE $2
                ORDER BY ps.x, ps.y, ps.z
                LIMIT {SEARCH_LIMIT + 1}
                """,
                tick["id"],
                f"%{needle}%",
            )
        if not rows:
            await ctx.send(f"No scouted planets matching '{needle}'")
            return
        truncated = len(rows) > SEARCH_LIMIT
        rows = rows[:SEARCH_LIMIT]
        lines = [
            f"{r['x']}:{r['y']}:{r['z']} '{r['ruler_name']}' of '{r['planet_name']}'"
            + (f" [{r['alliance']}]" if r["alliance"] else "")
            + (f" ({r['nick']})" if r["nick"] else "")
            for r in rows
        ]
        suffix = f"\n(+{len(rows)} more, refine your search)" if truncated else ""
        await ctx.send(f"Matches for '{needle}':\n```\n" + "\n".join(lines) + "\n```" + suffix)

    @commands.command(name="info")
    async def info(self, ctx: commands.Context, *, arg: str) -> None:
        """Alliance info: current stats, trend since last tick, and scouted member count. Usage: !info <alliance>"""
        name = arg.strip()
        if not name:
            await ctx.send("Usage: !info <alliance>")
            return

        async with acquire() as conn:
            round_id = await self._current_round_id(conn)
            if round_id is None:
                await ctx.send("No round data yet.")
                return
            tick = await self._latest_tick(conn, round_id)
            if tick is None:
                await ctx.send("No ticks ingested yet.")
                return
            prev_tick_id = await conn.fetchval(
                "SELECT id FROM tick WHERE round_id = $1 AND number < $2 ORDER BY number DESC LIMIT 1",
                round_id,
                tick["number"],
            )

            row = None
            for pattern in (name, f"{name}%", f"%{name}%"):
                row = await conn.fetchrow(
                    """
                    SELECT a.id AS alliance_id, a.name, s.*,
                           prev.score AS prev_score, prev.total_value AS prev_total_value
                    FROM alliance_stat s
                    JOIN alliance a ON a.id = s.alliance_id
                    LEFT JOIN alliance_stat prev ON prev.alliance_id = a.id AND prev.tick_id = $3
                    WHERE s.tick_id = $1 AND a.name ILIKE $2
                    ORDER BY a.name
                    LIMIT 1
                    """,
                    tick["id"],
                    pattern,
                    prev_tick_id,
                )
                if row is not None:
                    break
            if row is None:
                await ctx.send(f"No alliance matching '{name}' found")
                return

            scouted = await conn.fetchval(
                "SELECT count(*) FROM planet_intel WHERE alliance ILIKE $1",
                row["name"],
            )

        score_delta = row["score"] - row["prev_score"] if row["prev_score"] is not None else None
        value_delta = (
            row["total_value"] - row["prev_total_value"]
            if row["prev_total_value"] is not None
            else None
        )
        trend = ""
        if score_delta is not None:
            trend = (
                f" | Since last tick: Score {'+' if score_delta >= 0 else ''}{num2short(score_delta)} "
                f"Value {'+' if value_delta >= 0 else ''}{num2short(value_delta)}"
            )
        await ctx.send(
            f"'{row['name']}' Rank #{row['rank']} Members: {row['members']} "
            f"Score: {num2short(row['score'])} Points: {row['points']} "
            f"Total Value: {num2short(row['total_value'])} -- Scouted: {scouted}/{row['size']}"
            + trend
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Stats(bot))
