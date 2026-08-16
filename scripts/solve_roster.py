"""Solve an alliance's exact roster over a fixed-membership tick window via CP-SAT.

Usage:
    uv run btk-solve-roster "TiT" 136 213                      # latest round
    uv run btk-solve-roster "PussycatZ" 95 213 --round 118
    uv run btk-solve-roster "Chocolate Starfish" 69 197 \\
        --force-in z9jbog80 --insert --updated-by <discord_user_id>
    uv run btk-solve-roster "Tal Shiar" 186 213 --extend       # check outside the window too

`btk-verify-rosters` proves 1- and 2-member alliances by exact index lookup;
beyond that the search space needs a real solver. This is exact subset-sum
over the two identities that hold per docs/alliance-membership-inference.md
and scripts/verify_rosters.py's docstring -- size and xp are exact, value is
bounded by the alliance fund:

    alliance.size            == sum(member.size)
    (total_score-total_value)/60 == sum(member.xp)
    0 <= alliance.total_value - sum(member.value) <= 75_000_000/150

applied as one CP-SAT constraint set per tick across every tick in [lo, hi]
where the alliance has this member count -- the more ticks, the tighter the
search, since a spurious subset that satisfies one tick's equations almost
never satisfies the next tick's too.

THREE THINGS THAT MATTER FOR A CORRECT SOLVE, learned the hard way and now
built in rather than left as manual follow-ups:

1. EXCLUDE PLANETS ALREADY CLAIMED ELSEWHERE. By default the candidate pool
   drops any planet already confirmed to a *different* alliance in
   `planet_intel` (the closure property: a planet belongs to at most one
   alliance at a time). Skipping this let a first Chocolate Starfish solve
   return an "OPTIMAL" 22-of-401 roster that included a planet already
   proven to belong to PussycatZ -- a real bug that a plain OPTIMAL status
   does not protect against. Pass --no-exclude-claimed only to reproduce
   that failure mode deliberately.

2. PICK THE WINDOW TO MAXIMIZE STABLE-REGIME LENGTH, not member count. A
   short window at the alliance's *current* member count can be genuinely
   ambiguous (Chocolate Starfish's 16-tick, 22-member window had a second
   solution) while a longer window at a *smaller*, earlier member count is
   often unique, because a growing alliance's roster only ever gains
   members. `btk-find-joiners` dates every join event via the counted-score
   gap; the interval between two consecutive joins is the longest window at
   a fixed count, and its solution is a strict subset of every later
   roster -- so solve there, then union in the known joiners named by
   `btk-find-joiners` to reach the current roster.

3. A FULLY IDLE MEMBER (size=0, xp=0 for the whole window) IS INVISIBLE TO
   THE EQUATIONS. Its `value` only needs to fit inside the fund slack, not
   match exactly, so any other equally idle planet can silently substitute
   -- this is a worse version of the "generic idle planet" ambiguity
   (BBQQ's 151-tick window had 15 mutually-substitutable idle candidates
   for one slot). After confirming uniqueness, this script automatically
   detects any solved member that's idle for the entire window, excludes
   every idle candidate in the pool, and re-solves at the same N: if that
   comes back INFEASIBLE, the non-idle members are proven and the idle
   slot(s) are reported and left out of the insert rather than guessed.
   Pass --no-check-idle to skip this (matching the pre-automation behavior).

Every solve is followed by a UNIQUENESS PROBE: re-solve with the found set's
members forbidden (sum <= n-1). INFEASIBLE proves the roster is the only one
consistent with every tick in the window; a second OPTIMAL means the window
was too short and the result must not be trusted or inserted.

--extend checks the confirmed roster against every tick in the WHOLE round,
not just [lo, hi] -- useful both to see how far a "zero churn" result
actually extends (Imperium and Pink Fluffy Unicorns held for all 201 ticks
of round 118; VGN held for all but one anomalous tick) and to catch a
departure or arrival the moment it happens: at any tick just outside the
window where the alliance's member count is exactly N-1 or N+1, it tries
every single-planet removal or addition and reports a unique match by name
(this is how Tal Shiar's tick-214 departure, v1dl47xr, was identified and
its now-stale planet_intel row removed).
"""

import argparse
import asyncio
from typing import Any

import asyncpg
from ortools.sat.python import cp_model

from btk.config import get_settings

FUND_MAX_VALUE = 75_000_000 // 150


async def load(
    conn: asyncpg.Connection, round_number: int | None, alliance: str, lo: int, hi: int
) -> dict[str, Any]:
    """Read one alliance's history and every planet's stats over [lo, hi] in one snapshot."""
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
            SELECT t.number AS tick, ast.members, ast.size, ast.total_score, ast.total_value
            FROM alliance_stat ast
            JOIN alliance a ON a.id = ast.alliance_id
            JOIN tick t ON t.id = ast.tick_id
            WHERE t.round_id = $1 AND a.name = $2 AND t.number BETWEEN $3 AND $4
            ORDER BY t.number
            """,
            round_id,
            alliance,
            lo,
            hi,
        )
        if not alliance_rows:
            raise SystemExit(f"no alliance_stat rows for {alliance!r} in ticks {lo}-{hi}")

        planet_rows = await conn.fetch(
            """
            SELECT p.external_id, t.number AS tick, ps.size, ps.xp, ps.value
            FROM planet_stat ps
            JOIN planet p ON p.id = ps.planet_id
            JOIN tick t ON t.id = ps.tick_id
            WHERE t.round_id = $1 AND p.round_id = $1 AND t.number BETWEEN $2 AND $3
            """,
            round_id,
            lo,
            hi,
        )

        claimed_rows = await conn.fetch(
            """
            SELECT p.external_id, pi.alliance
            FROM planet_intel pi
            JOIN planet p ON p.id = pi.planet_id
            WHERE p.round_id = $1
            """,
            round_id,
        )

    stats: dict[str, dict[int, tuple[int, int, int]]] = {}
    for r in planet_rows:
        stats.setdefault(r["external_id"], {})[r["tick"]] = (r["size"], r["xp"], r["value"])

    history = [
        {
            "tick": r["tick"],
            "members": r["members"],
            "size": r["size"],
            "xp": (r["total_score"] - r["total_value"]) // 60,
            "value_cap": r["total_value"],
        }
        for r in alliance_rows
    ]

    claimed_elsewhere = {r["external_id"] for r in claimed_rows if r["alliance"] != alliance}

    return {
        "round_number": round_number,
        "round_id": round_id,
        "stats": stats,
        "history": history,
        "claimed_elsewhere": claimed_elsewhere,
    }


async def load_full(conn: asyncpg.Connection, round_id: int, alliance: str) -> dict[str, Any]:
    """Like `load`, but unbounded by any tick window -- every tick in the round for both the
    alliance's history and every planet's stats. Used by --extend, which needs to see ticks
    outside [lo, hi] to check how far a solved roster holds and to catch departures/arrivals.
    Kept separate from `load` so the common path (solve a window) stays a cheap, bounded query.
    """
    async with conn.transaction(isolation="repeatable_read", readonly=True):
        alliance_rows = await conn.fetch(
            """
            SELECT t.number AS tick, ast.members, ast.size, ast.total_score, ast.total_value
            FROM alliance_stat ast
            JOIN alliance a ON a.id = ast.alliance_id
            JOIN tick t ON t.id = ast.tick_id
            WHERE t.round_id = $1 AND a.name = $2
            ORDER BY t.number
            """,
            round_id,
            alliance,
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

    stats: dict[str, dict[int, tuple[int, int, int]]] = {}
    for r in planet_rows:
        stats.setdefault(r["external_id"], {})[r["tick"]] = (r["size"], r["xp"], r["value"])

    history = [
        {
            "tick": r["tick"],
            "members": r["members"],
            "size": r["size"],
            "xp": (r["total_score"] - r["total_value"]) // 60,
            "value_cap": r["total_value"],
        }
        for r in alliance_rows
    ]

    return {"stats": stats, "history": history}


def build_model(
    data: dict[str, Any],
    force_in: set[str],
    exclude_claimed: bool,
    extra_exclude: set[str] = frozenset(),
) -> tuple[cp_model.CpModel, dict[str, Any], list[dict[str, Any]], list[str]]:
    """`extra_exclude` is orthogonal to `exclude_claimed` -- it's how the idle-member check
    removes idle candidates from the pool for a re-solve without touching the claimed-elsewhere
    exclusion the caller already chose."""
    history = data["history"]
    stats = data["stats"]
    ticks = [r["tick"] for r in history]
    n = history[0]["members"]
    if any(r["members"] != n for r in history):
        raise SystemExit(
            f"member count is not constant over ticks {ticks[0]}-{ticks[-1]} -- "
            "narrow the window to a span with no join/leave (see btk-find-joiners)"
        )

    pool = (data["claimed_elsewhere"] if exclude_claimed else set()) | extra_exclude
    cands = [e for e, by in stats.items() if all(t in by for t in ticks) and e not in pool]

    m = cp_model.CpModel()
    x = {c: m.NewBoolVar(c) for c in cands}
    m.Add(sum(x.values()) == n)
    for c in force_in:
        if c not in x:
            raise SystemExit(f"--force-in planet {c!r} is not a valid candidate for this window")
        m.Add(x[c] == 1)
    for r in history:
        t = r["tick"]
        m.Add(sum(x[c] * stats[c][t][0] for c in cands) == r["size"])
        m.Add(sum(x[c] * stats[c][t][1] for c in cands) == r["xp"])
        fund = m.NewIntVar(0, FUND_MAX_VALUE, f"fund_{t}")
        m.Add(sum(x[c] * stats[c][t][2] for c in cands) + fund == r["value_cap"])
    return m, x, history, cands


def solve(model: cp_model.CpModel, time_limit: float) -> tuple[cp_model.CpSolver, int]:
    solver = cp_model.CpSolver()
    solver.parameters.num_search_workers = 12
    solver.parameters.max_time_in_seconds = time_limit
    return solver, solver.Solve(model)


def find_idle_members(data: dict[str, Any], roster: set[str], lo: int, hi: int) -> set[str]:
    """Members of `roster` that are (size=0, xp=0) at every tick in [lo, hi] -- invisible to the
    exact size/xp equations, so any other equally idle planet could silently substitute."""
    stats = data["stats"]
    ticks = range(lo, hi + 1)
    return {
        c
        for c in roster
        if all(t in stats.get(c, {}) and stats[c][t][0] == 0 and stats[c][t][1] == 0 for t in ticks)
    }


def find_idle_candidates(
    data: dict[str, Any], lo: int, hi: int, exclude: set[str] = frozenset()
) -> set[str]:
    """Every candidate in the pool (not just the solved roster) that is (size=0, xp=0) at every
    tick in [lo, hi] -- the full set an idle roster member is indistinguishable from."""
    stats = data["stats"]
    ticks = range(lo, hi + 1)
    return {
        c
        for c, by in stats.items()
        if c not in exclude and all(t in by and by[t][0] == 0 and by[t][1] == 0 for t in ticks)
    }


def reconcile(
    stats: dict[str, dict[int, tuple[int, int, int]]], roster: set[str], target: dict[str, Any]
) -> tuple[bool, int | None]:
    """Check whether `roster` matches one alliance_stat row exactly: size and xp exact, value
    within the fund's slack. `target` is one row from a `history` list."""
    if target["members"] != len(roster):
        return False, None
    rows = []
    for c in roster:
        by = stats.get(c, {})
        if target["tick"] not in by:
            return False, None
        rows.append(by[target["tick"]])
    size = sum(r[0] for r in rows)
    xp = sum(r[1] for r in rows)
    value = sum(r[2] for r in rows)
    fund = target["value_cap"] - value
    ok = size == target["size"] and xp == target["xp"] and 0 <= fund <= FUND_MAX_VALUE
    return ok, fund


def extend_check(full: dict[str, Any], roster: set[str]) -> list[dict[str, Any]]:
    """Check a solved `roster` against every tick in the alliance's full round history."""
    results = []
    for target in full["history"]:
        ok, fund = reconcile(full["stats"], roster, target)
        results.append(
            {"tick": target["tick"], "ok": ok, "members": target["members"], "fund": fund}
        )
    return results


def find_single_change(
    full: dict[str, Any], roster: set[str], tick: int, exclude: set[str] = frozenset()
) -> set[str]:
    """At `tick`, find every single-planet change to `roster` -- one member leaving, or one
    outside planet joining -- that makes it reconcile exactly. Tries removals if the alliance's
    real member count was one lower at this tick, additions if one higher; anything else returns
    empty, since a single-planet change can't explain a bigger jump."""
    target = next((r for r in full["history"] if r["tick"] == tick), None)
    if target is None:
        return set()
    delta = target["members"] - len(roster)
    stats = full["stats"]
    found = set()
    if delta == -1:
        for c in roster:
            ok, _ = reconcile(stats, roster - {c}, target)
            if ok:
                found.add(c)
    elif delta == 1:
        for c in stats:
            if c in roster or c in exclude:
                continue
            ok, _ = reconcile(stats, roster | {c}, target)
            if ok:
                found.add(c)
    return found


async def run(args: argparse.Namespace) -> None:
    settings = get_settings()
    conn = await asyncpg.connect(dsn=settings.db_dsn)
    try:
        data = await load(conn, args.round, args.alliance, args.lo, args.hi)
        print(
            f"round {data['round_number']}: {args.alliance!r}, "
            f"ticks {args.lo}-{args.hi}, {len(data['history'])} ticks with data"
        )
        force_in = set(args.force_in or ())
        model, x, history, cands = build_model(data, force_in, not args.no_exclude_claimed)
        n = history[0]["members"]
        excluded = len(data["claimed_elsewhere"]) if not args.no_exclude_claimed else 0
        print(f"N={n}, pool={len(cands)} (excluded {excluded} claimed elsewhere)")

        solver, status = solve(model, args.time)
        print(f"status: {solver.StatusName(status)} ({solver.WallTime():.1f}s)")
        if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            raise SystemExit("no solution found")

        picked = sorted(c for c in cands if solver.Value(x[c]))
        last = history[-1]["tick"]
        for c in picked:
            sz, xp, v = data["stats"][c][last]
            print(f"   {c}  size={sz:>6} xp={xp:>7} value={v:>9}")
        fund = history[-1]["value_cap"] - sum(data["stats"][c][last][2] for c in picked)
        print(f"   implied fund at tick {last}: {fund:,} value ({fund * 150:,} resources)")

        print("uniqueness probe: forbidding this exact set...")
        model.Add(sum(x[c] for c in picked) <= len(picked) - 1)
        solver2, status2 = solve(model, args.time)
        print(f"status: {solver2.StatusName(status2)} ({solver2.WallTime():.1f}s)")
        if status2 in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            alt = sorted(c for c in cands if solver2.Value(x[c]))
            print(f"NOT UNIQUE -- another solution exists: {alt}")
            print("refusing to treat this as confirmed; widen the tick window and retry")
            return
        print(f"UNIQUE -- confirmed over {len(history)} ticks ({args.lo}-{args.hi})")

        confirmed = set(picked)
        if not args.no_check_idle:
            idle_in_solution = find_idle_members(data, confirmed, args.lo, args.hi)
            if idle_in_solution:
                idle_pool = find_idle_candidates(data, args.lo, args.hi)
                print(
                    f"\n{len(idle_in_solution)} solved member(s) are fully idle (size=0, xp=0) "
                    f"for the whole window: {sorted(idle_in_solution)}"
                )
                print(
                    f"{len(idle_pool)} candidate(s) in the pool share that profile -- "
                    "re-solving with all of them excluded to check the rest is still forced..."
                )
                model3, _x3, _, _cands3 = build_model(
                    data, force_in, not args.no_exclude_claimed, extra_exclude=idle_pool
                )
                solver3, status3 = solve(model3, args.time)
                print(f"status: {solver3.StatusName(status3)} ({solver3.WallTime():.1f}s)")
                if status3 == cp_model.INFEASIBLE:
                    confirmed -= idle_in_solution
                    print(
                        f"INFEASIBLE -- confirms the other {len(confirmed)} member(s); the idle "
                        f"slot(s) are left unresolved rather than guessed among {len(idle_pool)} "
                        "indistinguishable candidates"
                    )
                else:
                    print(
                        "did not come back INFEASIBLE -- the idle ambiguity does not cleanly "
                        "isolate to just the idle slot(s); treat this roster as unconfirmed"
                    )
                    return

        if args.extend:
            print("\n--extend: checking the confirmed roster against the whole round...")
            full = await load_full(conn, data["round_id"], args.alliance)
            results = extend_check(full, confirmed)
            failing = [r for r in results if not r["ok"]]
            print(
                f"{len(results) - len(failing)} of {len(results)} ticks reconcile; "
                f"{len(failing)} do not"
            )
            for r in failing:
                note = ""
                if r["members"] != len(confirmed):
                    changed = find_single_change(full, confirmed, r["tick"])
                    if len(changed) == 1:
                        (ext,) = changed
                        kind = "joined" if r["members"] > len(confirmed) else "left"
                        note = f" -- single-planet change identified: {ext} {kind} at this tick"
                    elif changed:
                        note = (
                            f" -- {len(changed)} candidates could explain this: {sorted(changed)}"
                        )
                print(
                    f"   tick {r['tick']:>4}  alliance has {r['members']} members "
                    f"(roster has {len(confirmed)}){note}"
                )

        if args.insert:
            if args.updated_by is None:
                raise SystemExit("--insert requires --updated-by <discord_user_id>")
            comment = (
                f"{args.alliance} confirmed exact -- CP-SAT subset-sum, fund-aware model "
                f"(size, xp exact; value bounded by 0<=fund<=500000), unique {n}-of-{len(cands)} "
                f"solution over ticks {args.lo}-{args.hi} ({len(history)} ticks). "
                f"Found by btk-solve-roster."
            )
            async with conn.transaction():
                for ext in sorted(confirmed):
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
                        args.alliance,
                        comment,
                        args.updated_by,
                        data["round_id"],
                        ext,
                    )
            print(f"inserted/updated {len(confirmed)} planet_intel rows")
        else:
            print("(read-only; pass --insert --updated-by <id> to write planet_intel)")
    finally:
        await conn.close()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("alliance", help="alliance name, exact match")
    ap.add_argument("lo", type=int, help="first tick of the fixed-membership window")
    ap.add_argument("hi", type=int, help="last tick of the fixed-membership window")
    ap.add_argument("--round", type=int, default=None, help="round number (default: latest)")
    ap.add_argument("--time", type=float, default=180.0, help="solver time limit per solve, sec")
    ap.add_argument(
        "--force-in", action="append", metavar="EXTERNAL_ID", help="planet(s) to require in-set"
    )
    ap.add_argument(
        "--no-exclude-claimed",
        action="store_true",
        help="do NOT drop planets already confirmed to a different alliance (see docstring)",
    )
    ap.add_argument(
        "--no-check-idle",
        action="store_true",
        help="skip auto-detecting fully-idle roster members and re-proving without them",
    )
    ap.add_argument(
        "--extend",
        action="store_true",
        help="check the confirmed roster against every tick in the whole round, and try to "
        "identify single-planet joins/leaves at any tick where it stops reconciling",
    )
    ap.add_argument("--insert", action="store_true", help="write results to planet_intel")
    ap.add_argument("--updated-by", type=int, default=None, help="discord user id for insert")
    asyncio.run(run(ap.parse_args()))


if __name__ == "__main__":
    main()
