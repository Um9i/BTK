"""Prove the rosters of 1- and 2-member alliances from aggregate stats alone.

Usage:
    uv run btk-verify-rosters                    # verify latest round, read-only
    uv run btk-verify-rosters --round 118
    uv run btk-verify-rosters --insert --updated-by <discord_user_id>

`alliance_listing.txt` carries only per-alliance aggregates, never a membership
list (see db/schema.sql and docs/alliance-membership-inference.md). For most
alliances recovering the roster is a hard subset-sum problem. For the smallest
ones it is not.

WHAT IS AND IS NOT A PLAIN SUM. An alliance's `total_score` and `total_value`
both include the ALLIANCE FUND (`resources / 150`), which belongs to no planet,
so neither is a sum over members. Only two quantities are exact:

    alliance.size        == sum(member.size)
    (total_score - total_value) / 60 == sum(member.xp)      [Score = XP*60 + Value]

and value is merely bounded, the shortfall being the fund:

    0 <= total_value - sum(member.value) <= 75_000_000 / 150

Matching on the (size, score, value) triple -- as this script originally did --
therefore only works for alliances whose fund happens to be empty, and silently
reports NO MATCH for every alliance that has ever used its fund. Candidates are
now indexed by the exact (size, xp) pair instead, with value applied as a bound.
Pair search stays O(n) per tick because one member still fully determines the
other: the complement of (size, xp) is (S - size, X - xp).

Matching at a single tick is weak evidence -- idle planets share stats, and that
ambiguity has produced real mis-taggings before. So a roster is only reported
CONFIRMED if the candidate set is a single planet (or pair) after intersecting
EVERY tick at which the alliance had that member count. Anything else is
reported and skipped rather than guessed.

Both stat tables are read inside one REPEATABLE READ transaction. That matters:
reading them separately against a live ticking DB can straddle an ingest, and a
tick present in one snapshot but not the other silently empties every
intersection (or, in the other direction, hides a genuine contradiction).
"""

import argparse
import asyncio
from collections import defaultdict
from typing import Any

import asyncpg

from btk.config import get_settings

# the in-game alliance fund holds at most 75,000,000 resources, and
# Value = Resources / 150, so it can contribute at most this much value
FUND_MAX_VALUE = 75_000_000 // 150

Key = tuple[int, int]  # (size, xp)


async def load_round(conn: asyncpg.Connection, round_number: int | None) -> dict[str, Any]:
    """Read alliance and planet stats for one round in a single MVCC snapshot."""
    async with conn.transaction(isolation="repeatable_read", readonly=True):
        if round_number is None:
            round_number = await conn.fetchval("SELECT max(number) FROM round")
            if round_number is None:
                raise SystemExit("no rounds in the database")
        round_id = await conn.fetchval("SELECT id FROM round WHERE number = $1", round_number)
        if round_id is None:
            raise SystemExit(f"round {round_number} not found")

        alliance_rows = await conn.fetch(
            """
            SELECT a.name, t.number AS tick, ast.members, ast.size,
                   ast.total_score, ast.total_value
            FROM alliance_stat ast
            JOIN alliance a ON a.id = ast.alliance_id
            JOIN tick t ON t.id = ast.tick_id
            WHERE t.round_id = $1
            """,
            round_id,
        )
        planet_rows = await conn.fetch(
            """
            SELECT p.external_id, t.number AS tick, ps.size, ps.xp, ps.value
            FROM planet_stat ps
            JOIN planet p ON p.id = ps.planet_id
            JOIN tick t ON t.id = ps.tick_id
            WHERE t.round_id = $1 AND p.round_id = $1
            """,
            round_id,
        )

    # tick -> (size, xp) -> [(external_id, value)]
    by_tick: dict[int, dict[Key, list[tuple[str, int]]]] = defaultdict(lambda: defaultdict(list))
    for r in planet_rows:
        by_tick[r["tick"]][(r["size"], r["xp"])].append((r["external_id"], r["value"]))

    history: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in alliance_rows:
        history[r["name"]].append(
            {
                "tick": r["tick"],
                "members": r["members"],
                "size": r["size"],
                "xp": (r["total_score"] - r["total_value"]) // 60,
                "value_cap": r["total_value"],
            }
        )
    for rows in history.values():
        rows.sort(key=lambda r: r["tick"])

    return {
        "round_number": round_number,
        "round_id": round_id,
        "by_tick": by_tick,
        "history": history,
    }


def fund_ok(value_cap: int, members_value: int) -> bool:
    """The unexplained remainder must be a physically possible alliance fund."""
    fund = value_cap - members_value
    return 0 <= fund <= FUND_MAX_VALUE


def singles_at_tick(by_key: dict[Key, list[tuple[str, int]]], target: dict[str, Any]) -> set[str]:
    """Planets matching the alliance's exact (size, xp), with value within fund slack."""
    return {
        ext
        for ext, value in by_key.get((target["size"], target["xp"]), ())
        if fund_ok(target["value_cap"], value)
    }


def pairs_at_tick(
    by_key: dict[Key, list[tuple[str, int]]], target: dict[str, Any]
) -> set[tuple[str, str]]:
    """Every unordered pair matching the alliance's exact (size, xp) totals.

    O(n) per tick: picking one member fixes the other's key exactly, so this is
    a dict lookup per candidate rather than a scan over pairs.
    """
    ts, tx = target["size"], target["xp"]
    out: set[tuple[str, str]] = set()
    for (s, x), entries in by_key.items():
        need = (ts - s, tx - x)
        partners = by_key.get(need)
        if not partners:
            continue
        if need == (s, x):
            # partner shares the key: needs two distinct planets from this bucket
            for i in range(len(entries)):
                for j in range(i + 1, len(entries)):
                    (ea, va), (eb, vb) = entries[i], entries[j]
                    if fund_ok(target["value_cap"], va + vb):
                        out.add(tuple(sorted((ea, eb))))  # type: ignore[arg-type]
        else:
            for ea, va in entries:
                for eb, vb in partners:
                    if ea != eb and fund_ok(target["value_cap"], va + vb):
                        out.add(tuple(sorted((ea, eb))))  # type: ignore[arg-type]
    return out


def verify(data: dict[str, Any], size: int, historical: bool = False) -> list[dict[str, Any]]:
    """Intersect per-tick candidates across every tick with this member count.

    Alliances that no longer exist at the round's latest tick are skipped unless
    `historical` is set. They resolve perfectly well, but a planet that was in a
    now-defunct alliance is usually in a *current* one too, and `planet_intel`
    stores a single alliance per planet -- so writing a historical roster would
    overwrite the live one. In round 118 for instance `qq` (ticks 22-83) and
    `Fairies` (ticks 84-209) are the same two planets under a renamed alliance.
    """
    by_tick = data["by_tick"]
    latest = max(by_tick) if by_tick else 0
    results = []
    for name, rows in sorted(data["history"].items()):
        ticks = sorted(
            (r for r in rows if r["members"] == size and r["tick"] in by_tick),
            key=lambda r: r["tick"],
        )
        if not ticks:
            continue
        # "still exists" is judged only over ticks present in BOTH snapshots, so
        # alliance data running ahead of planet data can't make a live alliance
        # look defunct.
        seen = [r["tick"] for r in rows if r["tick"] in by_tick]
        if not historical and seen and max(seen) != latest:
            continue
        surviving: set[Any] | None = None
        widest = 0
        for r in ticks:
            found: set[Any] = (
                singles_at_tick(by_tick[r["tick"]], r)
                if size == 1
                else pairs_at_tick(by_tick[r["tick"]], r)
            )
            widest = max(widest, len(found))
            surviving = found if surviving is None else surviving & found
            if not surviving:
                break
        results.append(
            {
                "alliance": name,
                "members": size,
                "n_ticks": len(ticks),
                "span": (ticks[0]["tick"], ticks[-1]["tick"]),
                "surviving": surviving or set(),
                "widest": widest,
            }
        )
    return results


def render(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    confirmed = []
    for r in results:
        lo, hi = r["span"]
        where = f"{r['n_ticks']} ticks ({lo}-{hi})"
        n = len(r["surviving"])
        if n == 1:
            members = next(iter(r["surviving"]))
            members = (members,) if isinstance(members, str) else members
            print(f"  CONFIRMED  {r['alliance']:<24} {' + '.join(members):<22} {where}")
            confirmed.append({**r, "members_ext": list(members)})
        elif n > 1:
            print(f"  AMBIGUOUS  {r['alliance']:<24} {n} candidates survive {where}")
        else:
            print(f"  NO MATCH   {r['alliance']:<24} contradiction over {where}")
    return confirmed


async def insert(
    conn: asyncpg.Connection, round_id: int, confirmed: list[dict[str, Any]], updated_by: int
) -> None:
    # A planet holds one alliance in planet_intel, so two confirmed rosters
    # naming the same planet would silently overwrite each other. Refuse rather
    # than let insert order decide which one wins.
    claims: dict[str, list[str]] = {}
    for r in confirmed:
        for ext in r["members_ext"]:
            claims.setdefault(ext, []).append(f"{r['alliance']} ({r['span'][0]}-{r['span'][1]})")
    # Two windows can both legitimately confirm the same planet for the same
    # alliance (e.g. a 1-member and a later 2-member window either side of a
    # join) -- only a planet claimed by *different* alliance names is a real
    # conflict.
    contested = {
        ext: who
        for ext, who in claims.items()
        if len({w.split(" (")[0] for w in who}) > 1
    }
    if contested:
        for ext, who in sorted(contested.items()):
            print(f"  CONFLICT {ext} claimed by: {', '.join(who)}")
        raise SystemExit(
            f"refusing to insert: {len(contested)} planet(s) claimed by more than one "
            "alliance (usually a rename -- re-run without --include-historical)"
        )

    async with conn.transaction():
        for r in confirmed:
            lo, hi = r["span"]
            if r["members"] == 1:
                how = (
                    "sole member of a 1-member alliance, matched on the alliance's exact "
                    "(size, xp) with value within alliance-fund slack"
                )
            else:
                how = (
                    "2-member alliance resolved by exhaustive pair search on exact "
                    "(size, xp) totals with value within alliance-fund slack"
                )
            comment = (
                f"{r['alliance']} confirmed exact -- {how}; a unique match survived "
                f"every one of {r['n_ticks']} ticks ({lo}-{hi}). "
                f"Verified by btk-verify-rosters."
            )
            for ext in r["members_ext"]:
                await conn.execute(
                    """
                    INSERT INTO planet_intel (planet_id, nick, alliance, comment, updated_by)
                    SELECT p.id, NULL, $1, $2, $3
                    FROM planet p
                    WHERE p.round_id = $4 AND p.external_id = $5
                    ON CONFLICT (planet_id) DO UPDATE
                    SET alliance = excluded.alliance,
                        comment = excluded.comment,
                        updated_by = excluded.updated_by,
                        updated_at = now()
                    """,
                    r["alliance"],
                    comment,
                    updated_by,
                    round_id,
                    ext,
                )
    total = sum(len(r["members_ext"]) for r in confirmed)
    print(f"\ninserted/updated {total} planet_intel rows")


async def run(args: argparse.Namespace) -> None:
    settings = get_settings()
    conn = await asyncpg.connect(dsn=settings.db_dsn)
    try:
        data = await load_round(conn, args.round)
        print(
            f"round {data['round_number']}: "
            f"{len(data['by_tick'])} ticks, {len(data['history'])} alliances"
        )
        confirmed: list[dict[str, Any]] = []
        for size in (1, 2):
            results = verify(data, size, historical=args.include_historical)
            if not results:
                continue
            print(f"\n{size}-member alliances:")
            confirmed.extend(render(results))

        print(f"\n{len(confirmed)} alliances proven")
        if args.insert:
            if args.updated_by is None:
                raise SystemExit("--insert requires --updated-by <discord_user_id>")
            await insert(conn, data["round_id"], confirmed, args.updated_by)
        elif confirmed:
            print("(read-only; pass --insert --updated-by <id> to write planet_intel)")
    finally:
        await conn.close()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--round", type=int, default=None, help="round number (default: latest)")
    ap.add_argument("--insert", action="store_true", help="write results to planet_intel")
    ap.add_argument(
        "--include-historical",
        action="store_true",
        help="also report alliances that no longer exist at the latest tick",
    )
    ap.add_argument("--updated-by", type=int, default=None, help="discord user id for insert")
    asyncio.run(run(ap.parse_args()))


if __name__ == "__main__":
    main()
