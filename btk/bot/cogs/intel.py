"""!lookup and !intel, ported from Merlin's Hooks/lookup.py and Hooks/intel/intel.py.

Ranks aren't stored (see db/schema.sql's design note on deriving rank/growth
at query time) except for alliance_stat.rank, which the ingest already
carries straight from the dump -- planet/galaxy ranks are computed here with
a window function over the latest tick instead.
"""

import re

from discord.ext import commands

from btk.db import acquire

PLANET_COORD_RE = re.compile(r"(\d+)[:.\s]+(\d+)[:.\s]+(\d+)")
GALAXY_COORD_RE = re.compile(r"(\d+)[:.\s]+(\d+)")

SPECIAL_FLAGS = {
    "P": "Prot",
    "D": "Del",
    "R": "Reset",
    "V": "Vac",
    "C": "Closed",
    "E": "Exile",
}

TRUE_VALS = {"yes", "true", "1", "y"}
FALSE_VALS = {"no", "false", "0", "n"}

PLANET_RANKS_CTE = """
    SELECT *,
           RANK() OVER (ORDER BY score DESC) AS score_rank,
           RANK() OVER (ORDER BY value DESC) AS value_rank,
           RANK() OVER (ORDER BY size DESC) AS size_rank,
           RANK() OVER (ORDER BY xp DESC) AS xp_rank
    FROM planet_stat
    WHERE tick_id = $1
"""

GALAXY_RANKS_CTE = """
    SELECT gs.*, g.x, g.y,
           RANK() OVER (ORDER BY gs.score DESC) AS score_rank,
           RANK() OVER (ORDER BY gs.value DESC) AS value_rank,
           RANK() OVER (ORDER BY gs.size DESC) AS size_rank,
           RANK() OVER (ORDER BY gs.xp DESC) AS xp_rank
    FROM galaxy_stat gs
    JOIN galaxy g ON g.id = gs.galaxy_id
    WHERE gs.tick_id = $1
"""


def _format_planet(row, nick: str | None = None) -> str:
    line = f"{row['x']}:{row['y']}:{row['z']} ({row['race']}) '{row['ruler_name']}' of '{row['planet_name']}' "
    if nick:
        line += f"[{nick}] "
    if row["special"]:
        flags = [SPECIAL_FLAGS.get(f, f) for f in row["special"].split(",") if f]
        if flags:
            line += f"({', '.join(flags)}) "
    line += f"Score: {row['score']} ({row['score_rank']}) "
    line += f"Value: {row['value']} ({row['value_rank']}) "
    line += f"Size: {row['size']} ({row['size_rank']}) "
    line += f"XP: {row['xp']} ({row['xp_rank']})"
    return line


def _format_galaxy(row, planet_count: int) -> str:
    return (
        f"{row['x']}:{row['y']} '{row['name']}' ({planet_count}) "
        f"Score: {row['score']} ({row['score_rank']}) "
        f"Value: {row['value']} ({row['value_rank']}) "
        f"Size: {row['size']} ({row['size_rank']}) "
        f"XP: {row['xp']} ({row['xp_rank']})"
    )


def _format_alliance(row) -> str:
    return (
        f"'{row['name']}' Members: {row['members']} "
        f"Score: {row['score']} (#{row['rank']}) "
        f"Points: {row['points']} "
        f"Total Score: {row['total_score']} Total Value: {row['total_value']}"
    )


def _format_intel(row) -> str:
    parts = []
    if row["nick"]:
        parts.append(f"Nick: {row['nick']}")
    if row["alliance"]:
        parts.append(f"Alliance: {row['alliance']}")
    if row["comment"]:
        parts.append(f"Comment: {row['comment']}")
    if row["amps"] is not None:
        parts.append(f"Amps: {row['amps']}")
    if row["dists"] is not None:
        parts.append(f"Dists: {row['dists']}")
    if row["defwhore"] is not None:
        parts.append(f"Defwhore: {'yes' if row['defwhore'] else 'no'}")
    return " - ".join(parts)


def _parse_intel_opts(rest: str) -> tuple[dict[str, str], str | None]:
    """Space-separated key=value tokens, except "comment=..." which takes the rest of the message."""
    if rest.lower().startswith("comment="):
        return {}, rest[len("comment=") :].strip()
    opts = {}
    for token in rest.split():
        key, sep, val = token.partition("=")
        if sep:
            opts[key.lower()] = val
    return opts, None


class Intel(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def _current_round_id(self, conn) -> int | None:
        return await conn.fetchval("SELECT id FROM round ORDER BY id DESC LIMIT 1")

    async def _latest_tick_id(self, conn, round_id: int) -> int | None:
        return await conn.fetchval(
            "SELECT id FROM tick WHERE round_id = $1 ORDER BY number DESC LIMIT 1", round_id
        )

    async def _find_alliance_stat(self, conn, tick_id: int, name: str):
        for pattern in (name, f"{name}%", f"%{name}%"):
            row = await conn.fetchrow(
                """
                SELECT a.name, s.*
                FROM alliance_stat s
                JOIN alliance a ON a.id = s.alliance_id
                WHERE s.tick_id = $1 AND a.name ILIKE $2
                ORDER BY a.name
                LIMIT 1
                """,
                tick_id,
                pattern,
            )
            if row is not None:
                return row
        return None

    @commands.command(name="lookup")
    async def lookup(self, ctx: commands.Context, *, arg: str) -> None:
        """Look up a planet, galaxy, or alliance. Usage: !lookup <x:y:z|x:y|alliance>"""
        arg = arg.strip()
        async with acquire() as conn:
            round_id = await self._current_round_id(conn)
            if round_id is None:
                await ctx.send("No round data yet.")
                return
            tick_id = await self._latest_tick_id(conn, round_id)
            if tick_id is None:
                await ctx.send("No ticks ingested yet.")
                return

            planet_match = PLANET_COORD_RE.fullmatch(arg)
            if planet_match:
                x, y, z = (int(v) for v in planet_match.groups())
                row = await conn.fetchrow(
                    f"WITH ranked AS ({PLANET_RANKS_CTE}) SELECT * FROM ranked WHERE x = $2 AND y = $3 AND z = $4",
                    tick_id,
                    x,
                    y,
                    z,
                )
                if row is None:
                    await ctx.send(f"No planet with coords {x}:{y}:{z} found")
                    return
                nick = await conn.fetchval(
                    "SELECT nick FROM planet_intel WHERE planet_id = $1", row["planet_id"]
                )
                await ctx.send(_format_planet(row, nick))
                return

            galaxy_match = GALAXY_COORD_RE.fullmatch(arg)
            if galaxy_match:
                x, y = (int(v) for v in galaxy_match.groups())
                row = await conn.fetchrow(
                    f"WITH ranked AS ({GALAXY_RANKS_CTE}) SELECT * FROM ranked WHERE x = $2 AND y = $3",
                    tick_id,
                    x,
                    y,
                )
                if row is None:
                    await ctx.send(f"No galaxy with coords {x}:{y} found")
                    return
                planet_count = await conn.fetchval(
                    "SELECT count(*) FROM planet_stat WHERE tick_id = $1 AND galaxy_id = $2",
                    tick_id,
                    row["galaxy_id"],
                )
                await ctx.send(_format_galaxy(row, planet_count))
                return

            row = await self._find_alliance_stat(conn, tick_id, arg)
        if row is None:
            await ctx.send(f"No planet, galaxy, or alliance matching '{arg}' found")
            return
        await ctx.send(_format_alliance(row))

    @commands.command(name="intel")
    async def intel(self, ctx: commands.Context, *, arg: str) -> None:
        """View or set scouting notes for a planet. Usage: !intel <x:y:z> [nick=|alliance=|comment=|amps=|dists=|defwhore=]"""
        arg = arg.strip()
        match = PLANET_COORD_RE.match(arg)
        if not match:
            await ctx.send(
                "Usage: !intel <x:y:z> [nick=... | alliance=... | comment=... | amps=N | dists=N | defwhore=yes|no]"
            )
            return
        x, y, z = (int(v) for v in match.groups())
        rest = arg[match.end() :].strip()

        async with acquire() as conn:
            round_id = await self._current_round_id(conn)
            if round_id is None:
                await ctx.send("No round data yet.")
                return
            tick_id = await self._latest_tick_id(conn, round_id)
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

            existing = await conn.fetchrow(
                "SELECT nick, alliance, comment, amps, dists, defwhore FROM planet_intel WHERE planet_id = $1",
                planet_id,
            )

            if not rest:
                if existing is None or not _format_intel(existing):
                    await ctx.send(f"No intel stored for {x}:{y}:{z}")
                    return
                await ctx.send(f"Intel for {x}:{y}:{z} -- {_format_intel(existing)}")
                return

            opts, comment = _parse_intel_opts(rest)
            merged = (
                dict(existing)
                if existing
                else {
                    "nick": None,
                    "alliance": None,
                    "comment": None,
                    "amps": None,
                    "dists": None,
                    "defwhore": None,
                }
            )
            if comment is not None:
                merged["comment"] = comment or None
            if "nick" in opts:
                merged["nick"] = (
                    None if opts["nick"].lower() in ("-", "none", "null") else opts["nick"]
                )
            if "alliance" in opts:
                merged["alliance"] = (
                    None if opts["alliance"].lower() in ("-", "none", "null") else opts["alliance"]
                )
            if "amps" in opts and opts["amps"].isdigit():
                merged["amps"] = int(opts["amps"])
            if "dists" in opts and opts["dists"].isdigit():
                merged["dists"] = int(opts["dists"])
            if "defwhore" in opts:
                v = opts["defwhore"].lower()
                if v in TRUE_VALS:
                    merged["defwhore"] = True
                elif v in FALSE_VALS:
                    merged["defwhore"] = False

            await conn.execute(
                """
                INSERT INTO planet_intel (planet_id, nick, alliance, comment, amps, dists, defwhore, updated_by, updated_at)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, now())
                ON CONFLICT (planet_id) DO UPDATE SET
                    nick = $2, alliance = $3, comment = $4, amps = $5, dists = $6, defwhore = $7,
                    updated_by = $8, updated_at = now()
                """,
                planet_id,
                merged["nick"],
                merged["alliance"],
                merged["comment"],
                merged["amps"],
                merged["dists"],
                merged["defwhore"],
                ctx.author.id,
            )

        formatted = _format_intel(merged)
        await ctx.send(f"Intel stored for {x}:{y}:{z}" + (f" -- {formatted}" if formatted else ""))


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Intel(bot))
