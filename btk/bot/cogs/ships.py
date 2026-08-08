"""Ship commands, ported from Merlin's Hooks/ships/{eff,ship,cost,stop,prod,rprod}.py."""

import math
import re

from discord.ext import commands

from btk.bot.format import num2short, short2num
from btk.bot.paconf import (
    CONS_VALUE,
    GOVS,
    RACE_PRODTIME,
    RES_VALUE,
    ROID_VALUE,
    SHIP_VALUE,
    TARGET_EFFICIENCY,
)
from btk.db import acquire

TARGET_COLUMN = {"t1": "target1", "t2": "target2", "t3": "target3"}

EFF_RE = re.compile(r"(\d+(?:\.\d+)?[km]?)\s+(.+?)(?:\s+(t1|t2|t3))?$", re.IGNORECASE)
COST_RE = re.compile(r"(\d+(?:\.\d+)?[km]?)\s+(.+)$", re.IGNORECASE)
PROD_RE = re.compile(r"(\d+(?:\.\d+)?[km]?)\s+(\S+)\s+(\d+)(?:\s+(.*))?$", re.IGNORECASE)
RPROD_RE = re.compile(r"(\S+)\s+(\d+)\s+(\d+)(?:\s+(.*))?$", re.IGNORECASE)
GOV_RE = re.compile("(" + "|".join(GOVS) + ")", re.IGNORECASE)


def _race_prodtime(race: str) -> float:
    key = race[:3].lower()
    key = "etd" if key == "eit" else key
    return RACE_PRODTIME.get(key, 0.0)


def _parse_gov_and_pop(rest: str) -> tuple[str | None, int]:
    gov = None
    pop = 0
    for token in rest.split():
        m = GOV_RE.match(token)
        if m and gov is None:
            gov = m.group(1).lower()
            continue
        if token.isdigit() and not pop:
            pop = int(token)
    return gov, pop


def _calc_ticks(cost: float, num: int, bonus: float, factories: int) -> int:
    norm_cost = num * cost
    norm_req = math.sqrt(norm_cost) * math.log(norm_cost**2)
    output = (4000 * factories) ** 0.98 * bonus
    return math.ceil((norm_req + 10000 * factories) / output)


def _newton(f, guess: float, dx: float = 0.00001, tolerance: float = 0.00001) -> float:
    def transform(x: float) -> float:
        derivative = (f(x + dx) - f(x)) / dx
        return x - f(x) / derivative

    new_guess = transform(guess)
    while abs(guess - new_guess) >= tolerance:
        guess = new_guess
        new_guess = transform(guess)
    return new_guess


def _reverse_prod(ticks: int, factories: int, bonus: float) -> float:
    output = (4000 * factories) ** 0.98 * bonus
    target = ticks * output - 10000 * factories

    def rpu(x: float) -> float:
        return 2 * math.sqrt(x) * math.log(x) - target

    return _newton(rpu, 10)


class Ships(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def _current_round_id(self, conn) -> int | None:
        return await conn.fetchval("SELECT id FROM round ORDER BY id DESC LIMIT 1")

    async def _find_ship(self, conn, round_id: int, name: str) -> dict | None:
        # Cascading match like Merlin's Ship.load(): exact, then prefix, then substring.
        for pattern in (name, f"{name}%", f"%{name}%"):
            row = await conn.fetchrow(
                "SELECT * FROM ship WHERE round_id = $1 AND name ILIKE $2 ORDER BY name LIMIT 1",
                round_id,
                pattern,
            )
            if row is not None:
                return dict(row)
        return None

    @commands.command(name="ship")
    async def ship(self, ctx: commands.Context, *, names: str) -> None:
        """Show the stats of one or more ships. Usage: !ship <ship> [ship ...]"""
        lines = []
        async with acquire() as conn:
            round_id = await self._current_round_id(conn)
            if round_id is None:
                await ctx.send("No round data yet.")
                return
            for name in names.split():
                ship = await self._find_ship(conn, round_id, name)
                if ship is None:
                    lines.append(f"No Ship called: {name}")
                    continue
                line = f"{ship['name']} ({ship['race'][:3]}) is class {ship['class']} | Target 1: {ship['target1']} |"
                if ship["target2"]:
                    line += f" Target 2: {ship['target2']} |"
                if ship["target3"]:
                    line += f" Target 3: {ship['target3']} |"
                line += f" Type: {ship['type']} | Init: {ship['initiative']} |"
                line += f" EMPres: {ship['empres']} |"
                if ship["type"].lower() == "emp":
                    line += f" Guns: {ship['guns']} |"
                else:
                    line += f" D/C: {ship['damage'] * 10000 // ship['total_cost']} |"
                line += f" A/C: {ship['armor'] * 10000 // ship['total_cost']} |"
                line += f" Base ETA: {ship['baseeta']}"
                lines.append(line)
        await ctx.send("\n".join(lines))

    @commands.command(name="cost")
    async def cost(self, ctx: commands.Context, *, arg: str) -> None:
        """Calculate the cost of producing a number of ships. Usage: !cost <number> <ship>"""
        match = COST_RE.match(arg.strip())
        if not match:
            await ctx.send("Usage: !cost <number> <ship>")
            return
        raw_num, name = match.groups()
        try:
            num = short2num(raw_num)
        except ValueError:
            await ctx.send(f"Couldn't parse number: {raw_num}")
            return

        async with acquire() as conn:
            round_id = await self._current_round_id(conn)
            if round_id is None:
                await ctx.send("No round data yet.")
                return
            ship = await self._find_ship(conn, round_id, name)
        if ship is None:
            await ctx.send(f"No ship called: {name}")
            return

        reply = (
            f"Buying {raw_num} {ship['name']} ({num2short(ship['total_cost'] * num // SHIP_VALUE)}) "
            f"will cost {num2short(ship['metal'] * num)} metal, {num2short(ship['crystal'] * num)} crystal "
            f"and {num2short(ship['eonium'] * num)} eonium."
        )
        for gov in GOVS.values():
            bonus = gov["prodcost"]
            if bonus == 0:
                continue
            reply += (
                f" {gov['name']}: {num2short(math.floor(ship['metal'] * (1 + bonus)) * num)} metal, "
                f"{num2short(math.floor(ship['crystal'] * (1 + bonus)) * num)} crystal and "
                f"{num2short(math.floor(ship['eonium'] * (1 + bonus)) * num)} eonium."
            )
        value_added = ship["total_cost"] * num * (1.0 / SHIP_VALUE - 1.0 / RES_VALUE)
        reply += f" It will add {num2short(value_added)} value"
        await ctx.send(reply)

    @commands.command(name="stop")
    async def stop(self, ctx: commands.Context, *, arg: str) -> None:
        """Calculate the defence required to stop a number of ships. Usage: !stop <number> <ship> [t1|t2|t3]"""
        match = EFF_RE.match(arg.strip())
        if not match:
            await ctx.send("Usage: !stop <number> <ship> [t1|t2|t3]")
            return
        raw_num, name, attacker_slot = match.groups()
        attacker_slot = (attacker_slot or "t1").lower()
        try:
            num = short2num(raw_num)
        except ValueError:
            await ctx.send(f"Couldn't parse number: {raw_num}")
            return

        async with acquire() as conn:
            round_id = await self._current_round_id(conn)
            if round_id is None:
                await ctx.send("No round data yet.")
                return
            ship = await self._find_ship(conn, round_id, name)
            if ship is None and "asteroids".rfind(name.lower()) > -1:
                ship = {
                    "name": "Asteroids",
                    "class": "Roids",
                    "armor": 50,
                    "total_cost": ROID_VALUE * SHIP_VALUE,
                    "empres": 0,
                }
            elif ship is None and "constructions".rfind(name.lower()) > -1:
                ship = {
                    "name": "Constructions",
                    "class": "Structure",
                    "armor": 500,
                    "total_cost": CONS_VALUE * SHIP_VALUE,
                    "empres": 0,
                }
            if ship is None:
                await ctx.send(f"No ship called: {name}")
                return

            attackers = await conn.fetch(
                f"SELECT * FROM ship WHERE round_id = $1 AND {TARGET_COLUMN[attacker_slot]} = $2 ORDER BY name",
                round_id,
                ship["class"],
            )
        if not attackers:
            await ctx.send(f"{ship['name']} are not hit by anything as that category ({attacker_slot})")
            return

        if ship["class"] == "Roids":
            verb = "Capturing"
        elif ship["class"].startswith("Struct"):
            verb = "Destroying"
        else:
            verb = "Stopping"

        efficiency = TARGET_EFFICIENCY[attacker_slot]
        cost = num2short(ship["total_cost"] * num // SHIP_VALUE)
        parts = [f"{verb} {raw_num} {ship['name']} ({cost}) as {attacker_slot} requires "]
        for a in attackers:
            # Ships with no damage stat (e.g. some Cloak-type covert ships) fight
            # like EMP: guns against the target's empres, not damage against armor.
            uses_guns = (a["type"] or "").lower() == "emp" or not a["damage"]
            try:
                if uses_guns:
                    needed = int(math.ceil(num / (float(100 - ship["empres"]) / 100) / a["guns"]) / efficiency)
                else:
                    needed = int(math.ceil(float(ship["armor"] * num) / a["damage"]) / efficiency)
            except ZeroDivisionError:
                parts.append(f"{a['name']}: n/a ")
                continue
            parts.append(f"{a['name']}: {needed} ({num2short(a['total_cost'] * needed // SHIP_VALUE)}) ")
        await ctx.send("".join(parts))

    @commands.command(name="bestagainst")
    async def bestagainst(self, ctx: commands.Context, *, arg: str) -> None:
        """Rank ships by cheapest value spent to stop a number of a target. Usage: !bestagainst <number> <ship> [t1|t2|t3]"""
        match = EFF_RE.match(arg.strip())
        if not match:
            await ctx.send("Usage: !bestagainst <number> <ship> [t1|t2|t3]")
            return
        raw_num, name, attacker_slot = match.groups()
        attacker_slot = (attacker_slot or "t1").lower()
        try:
            num = short2num(raw_num)
        except ValueError:
            await ctx.send(f"Couldn't parse number: {raw_num}")
            return

        async with acquire() as conn:
            round_id = await self._current_round_id(conn)
            if round_id is None:
                await ctx.send("No round data yet.")
                return
            ship = await self._find_ship(conn, round_id, name)
            if ship is None:
                await ctx.send(f"No ship called: {name}")
                return

            attackers = await conn.fetch(
                f"SELECT * FROM ship WHERE round_id = $1 AND {TARGET_COLUMN[attacker_slot]} = $2 ORDER BY name",
                round_id,
                ship["class"],
            )
        if not attackers:
            await ctx.send(f"{ship['name']} are not hit by anything as that category ({attacker_slot})")
            return

        efficiency = TARGET_EFFICIENCY[attacker_slot]
        ranked = []
        for a in attackers:
            # Ships with no damage stat (e.g. some Cloak-type covert ships) fight
            # like EMP: guns against the target's empres, not damage against armor.
            uses_guns = (a["type"] or "").lower() == "emp" or not a["damage"]
            try:
                if uses_guns:
                    needed = int(math.ceil(num / (float(100 - ship["empres"]) / 100) / a["guns"]) / efficiency)
                else:
                    needed = int(math.ceil(float(ship["armor"] * num) / a["damage"]) / efficiency)
            except ZeroDivisionError:
                continue
            value_spent = a["total_cost"] * needed // SHIP_VALUE
            ranked.append((a["name"], needed, value_spent))

        if not ranked:
            await ctx.send(f"No attacker in that category ({attacker_slot}) could get a needed count.")
            return

        ranked.sort(key=lambda r: r[2])
        lines = [f"{n}: {needed} ({num2short(v)})" for n, needed, v in ranked[:5]]
        await ctx.send(
            f"Cheapest ways to stop {raw_num} {ship['name']} as {attacker_slot}: " + " | ".join(lines)
        )

    @commands.command(name="prod")
    async def prod(self, ctx: commands.Context, *, arg: str) -> None:
        """Ticks to build <number> <ship> with <factories>. Usage: !prod <number> <ship> <factories> [pop] [gov]"""
        match = PROD_RE.match(arg.strip())
        if not match:
            await ctx.send("Usage: !prod <number> <ship> <factories> [population] [government]")
            return
        raw_num, name, raw_factories, rest = match.groups()
        try:
            num = short2num(raw_num)
        except ValueError:
            await ctx.send(f"Couldn't parse number: {raw_num}")
            return
        factories = int(raw_factories)
        gov_key, pop = _parse_gov_and_pop(rest or "")

        async with acquire() as conn:
            round_id = await self._current_round_id(conn)
            if round_id is None:
                await ctx.send("No round data yet.")
                return
            ship = await self._find_ship(conn, round_id, name)
        if ship is None:
            await ctx.send(f"{name} is not a ship.")
            return

        m, c, e = ship["metal"], ship["crystal"], ship["eonium"]
        bonus = 1 + pop / 100.0
        if gov_key:
            gov = GOVS[gov_key]
            m *= 1 + gov["prodcost"]
            c *= 1 + gov["prodcost"]
            e *= 1 + gov["prodcost"]
            bonus += gov["prodtime"]
        bonus += _race_prodtime(ship["race"])
        cost = math.floor(m) + math.floor(c) + math.floor(e)

        ticks = _calc_ticks(cost, num, bonus, factories)
        reply = (
            f"It will take {ticks} ticks to build {num2short(num)} {ship['name']} "
            f"({num2short(num * ship['total_cost'] // SHIP_VALUE)})"
        )
        reply += f" using {factories} factories" if factories > 1 else ""
        if gov_key or _race_prodtime(ship["race"]):
            reply += " with a"
        if gov_key:
            reply += f" {GOVS[gov_key]['name']}"
        reply += f" {ship['race']}" if _race_prodtime(ship["race"]) else ""
        if gov_key or _race_prodtime(ship["race"]):
            reply += " planet"
        if pop:
            reply += f" with {pop}% population"
        await ctx.send(reply)

    @commands.command(name="rprod")
    async def rprod(self, ctx: commands.Context, *, arg: str) -> None:
        """How many <ship> buildable in <ticks> with <factories>. Usage: !rprod <ship> <ticks> <factories> [pop] [gov]"""
        match = RPROD_RE.match(arg.strip())
        if not match:
            await ctx.send("Usage: !rprod <ship> <ticks> <factories> [population] [government]")
            return
        name, raw_ticks, raw_factories, rest = match.groups()
        ticks = int(raw_ticks)
        factories = int(raw_factories)
        gov_key, pop = _parse_gov_and_pop(rest or "")

        async with acquire() as conn:
            round_id = await self._current_round_id(conn)
            if round_id is None:
                await ctx.send("No round data yet.")
                return
            ship = await self._find_ship(conn, round_id, name)
        if ship is None:
            await ctx.send(f"{name} is not a ship.")
            return

        m, c, e = ship["metal"], ship["crystal"], ship["eonium"]
        bonus = 1 + pop / 100.0
        if gov_key:
            gov = GOVS[gov_key]
            m *= 1 + gov["prodcost"]
            c *= 1 + gov["prodcost"]
            e *= 1 + gov["prodcost"]
            bonus += gov["prodtime"]
        bonus += _race_prodtime(ship["race"])
        cost = math.floor(m) + math.floor(c) + math.floor(e)

        try:
            resources = _reverse_prod(ticks, factories, bonus)
        except ZeroDivisionError:
            await ctx.send("Couldn't converge on an answer for those numbers -- try smaller ticks/factories.")
            return
        ships = int(resources / cost)

        reply = (
            f"You can build {num2short(ships)} {ship['name']} "
            f"({num2short(ships * ship['total_cost'] // SHIP_VALUE)}) in {ticks} ticks"
        )
        reply += f" using {factories} factories" if factories > 1 else ""
        if gov_key or _race_prodtime(ship["race"]):
            reply += " with a"
        if gov_key:
            reply += f" {GOVS[gov_key]['name']}"
        reply += f" {ship['race']}" if _race_prodtime(ship["race"]) else ""
        if gov_key or _race_prodtime(ship["race"]):
            reply += " planet"
        if pop:
            reply += f" with {pop}% population"
        await ctx.send(reply)

    @commands.command(name="eff")
    async def eff(self, ctx: commands.Context, *, arg: str) -> None:
        """Calculate the efficiency of ships against a target class.

        Usage: !eff <number> <ship> [t1|t2|t3]  (defaults to t1)
        """
        match = EFF_RE.match(arg.strip())
        if not match:
            await ctx.send("Usage: !eff <number> <ship> [t1|t2|t3]")
            return

        raw_num, name, target = match.groups()
        target = (target or "t1").lower()
        try:
            num = short2num(raw_num)
        except ValueError:
            await ctx.send(f"Couldn't parse number: {raw_num}")
            return

        async with acquire() as conn:
            round_id = await self._current_round_id(conn)
            if round_id is None:
                await ctx.send("No round data yet.")
                return

            ship = await self._find_ship(conn, round_id, name)
            if ship is None:
                await ctx.send(f"No ship called: {name}")
                return

            target_class = ship[TARGET_COLUMN[target]]
            if not target_class:
                await ctx.send(f"{ship['name']} has no {target} target.")
                return

            total_damage = (ship["damage"] or 0) * num
            cost = num2short(num * ship["total_cost"] // SHIP_VALUE)

            if ship["target1"] == "Roids":
                killed = total_damage // 50
                await ctx.send(
                    f"{raw_num} {ship['name']} ({cost}) will capture Asteroid: "
                    f"{killed} ({num2short(killed * ROID_VALUE)})"
                )
                return
            if ship["target1"] == "Resources":
                killed = total_damage * 50
                await ctx.send(
                    f"{raw_num} {ship['name']} ({cost}) will capture Resource: "
                    f"{num2short(killed)} ({num2short(killed // RES_VALUE)})"
                )
                return
            if ship["target1"].startswith("Struct"):
                killed = total_damage // 500
                await ctx.send(
                    f"{raw_num} {ship['name']} ({cost}) will destroy Structure: "
                    f"{killed} ({num2short(killed * CONS_VALUE)})"
                )
                return

            targets = await conn.fetch(
                "SELECT * FROM ship WHERE round_id = $1 AND class = $2 ORDER BY name",
                round_id,
                target_class,
            )
            if not targets:
                await ctx.send(f"{ship['name']} does not have any targets in that category ({target})")
                return

            ship_type = (ship["type"] or "").lower()
            if ship_type == "emp":
                verb = "hug"
            elif ship_type == "steal":
                verb = "steal"
            elif ship_type[:4] == "norm" or ship_type == "cloak":
                verb = "destroy"
            else:
                await ctx.send(f"Unrecognised ship type: {ship['type']}")
                return

            # Ships with no damage stat (e.g. some Cloak-type covert ships) fight
            # like EMP: guns against the target's empres, not damage against armor.
            uses_guns = ship_type == "emp" or not ship["damage"]
            if uses_guns and ship_type != "emp":
                verb = "hug"

            efficiency = TARGET_EFFICIENCY[target]
            parts = [f"{raw_num} {ship['name']} ({cost}) hitting {target_class} will {verb} "]
            for t in targets:
                try:
                    if uses_guns:
                        killed = int(efficiency * ship["guns"] * num * float(100 - t["empres"]) / 100)
                    else:
                        killed = int(efficiency * total_damage / t["armor"])
                except ZeroDivisionError:
                    parts.append(f"{t['name']}: n/a ")
                    continue
                parts.append(f"{t['name']}: {killed} ({num2short(t['total_cost'] * killed // SHIP_VALUE)}) ")
            await ctx.send("".join(parts))


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Ships(bot))
