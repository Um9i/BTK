"""Identify alliance joiners from the counted-score gap, without any solver.

Usage:
    uv run btk-find-joiners                       # latest round, read-only
    uv run btk-find-joiners --round 118 --alliance TiT
    uv run btk-find-joiners --insert --updated-by <discord_user_id>

The game counts only score earned *while in* an alliance towards that
alliance's score (see the in-game manual's alliance Score section), so

    gap = total_score - counted_score
        = the score every current member was carrying when they joined

That makes the gap a precise instrument, in two ways:

1. It is a churn detector. The gap is piecewise constant and moves ONLY when
   somebody joins or leaves -- a constant gap over a span proves no join or
   leave happened in it, which the member count alone cannot tell you (a
   simultaneous leave+join leaves the count unchanged).

2. It names the joiner. A rise of D at tick T means someone joined carrying
   exactly D, and carried-in score is the joiner's own score at tick T-1. So
   the joiner is simply whichever planet had score exactly D at T-1 -- a
   dictionary lookup, not a search. When exactly one planet matches, that is a
   strong identification; when several do, it is reported as ambiguous rather
   than guessed.

Validated against round 118: for TiT this recovered three joiners that an
independent CP-SAT subset-sum had separately proven (mz6jsab8, dn6nfrbp,
ges5dnee), three for three.

IMPORTANT BLIND SPOT. A member who joined carrying zero score contributes
nothing to the gap, so their later departure is invisible here -- and so is a
swap in which both parties carried zero. Alliances formed at signup (before
players allocate their startup bonus) consist entirely of such members, which
is exactly why large founder blocs stay unresolved. A gap that never moves is
therefore evidence of no *visible* churn, not proof of a fixed roster.

A related signature worth knowing: a founder who created their alliance
immediately at signup with the bonus unspent carries exactly 40,000 -- the
startup grant of 2,000,000 each of metal/crystal/eonium is 6,000,000
resources, and Value = Resources/150 = 40,000, with XP 0 so Score = Value.
"""

import argparse
import asyncio
from collections import defaultdict
from itertools import pairwise
from typing import Any

import asyncpg

from btk.config import get_settings


async def load_round(conn: asyncpg.Connection, round_number: int | None) -> dict[str, Any]:
    """Read alliance and planet stats for one round in a single MVCC snapshot.

    Both tables must come from the same snapshot: read separately against a
    live ticking database they can straddle an ingest, and a tick present in
    one but not the other silently corrupts every comparison.
    """
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
            SELECT a.name, t.number AS tick, ast.members,
                   ast.total_score - ast.score AS gap
            FROM alliance_stat ast
            JOIN alliance a ON a.id = ast.alliance_id
            JOIN tick t ON t.id = ast.tick_id
            WHERE t.round_id = $1
            """,
            round_id,
        )
        planet_rows = await conn.fetch(
            """
            SELECT p.external_id, t.number AS tick, ps.score,
                   ps.x, ps.y, ps.z, ps.ruler_name, ps.planet_name, ps.size
            FROM planet_stat ps
            JOIN planet p ON p.id = ps.planet_id
            JOIN tick t ON t.id = ps.tick_id
            WHERE t.round_id = $1 AND p.round_id = $1
            """,
            round_id,
        )

    by_score: dict[int, dict[int, list[str]]] = defaultdict(lambda: defaultdict(list))
    detail: dict[tuple[str, int], dict[str, Any]] = {}
    for r in planet_rows:
        by_score[r["tick"]][r["score"]].append(r["external_id"])
        detail[(r["external_id"], r["tick"])] = dict(r)

    history: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in alliance_rows:
        history[r["name"]].append({"tick": r["tick"], "members": r["members"], "gap": r["gap"]})
    for rows in history.values():
        rows.sort(key=lambda r: r["tick"])

    return {
        "round_number": round_number,
        "round_id": round_id,
        "by_score": by_score,
        "detail": detail,
        "history": history,
    }


def gap_events(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Every tick at which the carried-in-score gap moved."""
    events = []
    for prev, cur in pairwise(rows):
        if cur["gap"] == prev["gap"]:
            continue
        events.append(
            {
                "tick": cur["tick"],
                "prev_tick": prev["tick"],
                "delta": cur["gap"] - prev["gap"],
                "members_before": prev["members"],
                "members_after": cur["members"],
            }
        )
    return events


def classify(ev: dict[str, Any]) -> str:
    grew = ev["members_after"] > ev["members_before"]
    shrank = ev["members_after"] < ev["members_before"]
    if ev["delta"] > 0 and grew:
        return "join"
    if ev["delta"] < 0 and shrank:
        return "leave"
    if ev["delta"] > 0 and not grew:
        return "join+leave (count unchanged)"
    return "roster change"


def analyse(data: dict[str, Any], only: str | None) -> list[dict[str, Any]]:
    found = []
    for name, rows in sorted(data["history"].items()):
        if only and name != only:
            continue
        events = gap_events(rows)
        span = f"{rows[0]['tick']}-{rows[-1]['tick']}"
        print(
            f"\n{name}  ({rows[-1]['members']} members, ticks {span}, "
            f"gap {rows[0]['gap']:,} -> {rows[-1]['gap']:,})"
        )
        if not events:
            print(
                "   gap never moved: no visible join or leave (blind to zero-carried-score members)"
            )
            continue
        for ev in events:
            kind = classify(ev)
            if kind != "join":
                print(
                    f"   tick {ev['tick']:>4}  {kind}: gap {ev['delta']:+,} "
                    f"(members {ev['members_before']} -> {ev['members_after']})"
                )
                continue
            cands = data["by_score"][ev["prev_tick"]].get(ev["delta"], [])
            if len(cands) == 1:
                ext = cands[0]
                d = data["detail"][(ext, ev["prev_tick"])]
                coords = f"{d['x']}:{d['y']}:{d['z']}"
                # A later unambiguous "leave" event whose magnitude exactly matches this
                # joiner's own carried-in delta is very likely the same planet leaving
                # again -- don't insert it as a current member if so (see the Fourteenth
                # pass's hief0xd7/z3gu9okd staleness bug, both caught by this exact match).
                matching_leave = next(
                    (
                        e
                        for e in events
                        if e["tick"] > ev["tick"]
                        and classify(e) == "leave"
                        and -e["delta"] == ev["delta"]
                    ),
                    None,
                )
                if matching_leave:
                    print(
                        f"   tick {ev['tick']:>4}  join carrying {ev['delta']:,} -> "
                        f"{ext}  {coords}  {d['ruler_name']} / {d['planet_name']}  "
                        f"STALE RISK: {name} has a later leave at tick {matching_leave['tick']} "
                        f"of the exact same magnitude -- likely this same planet leaving again; "
                        f"not inserting"
                    )
                else:
                    print(
                        f"   tick {ev['tick']:>4}  join carrying {ev['delta']:,} -> "
                        f"{ext}  {coords}  {d['ruler_name']} / {d['planet_name']}"
                    )
                    found.append({"alliance": name, "ext": ext, **ev})
            elif cands:
                print(
                    f"   tick {ev['tick']:>4}  join carrying {ev['delta']:,} -> "
                    f"AMBIGUOUS, {len(cands)} planets match: {cands}"
                )
            else:
                print(
                    f"   tick {ev['tick']:>4}  join carrying {ev['delta']:,} -> "
                    f"no planet had that score at tick {ev['prev_tick']}"
                )
    return found


async def insert(
    conn: asyncpg.Connection, round_id: int, found: list[dict[str, Any]], updated_by: int
) -> None:
    claims: dict[str, list[str]] = defaultdict(list)
    for f in found:
        claims[f["ext"]].append(f["alliance"])
    contested = {e: a for e, a in claims.items() if len(set(a)) > 1}
    if contested:
        for ext, who in sorted(contested.items()):
            print(f"  CONFLICT {ext} claimed by {sorted(set(who))}")
        raise SystemExit("refusing to insert: a planet is claimed by two alliances")

    async with conn.transaction():
        for f in found:
            comment = (
                f"{f['alliance']} joiner identified by counted-score gap analysis: the "
                f"alliance's (total_score - counted_score) gap rose by exactly "
                f"{f['delta']:,} at tick {f['tick']}, and carried-in score equals the "
                f"joiner's own score at the tick before joining -- this planet was the "
                f"UNIQUE planet in the round with that score at tick {f['prev_tick']}. "
                f"NOT a full-roster subset-sum proof. Found by btk-find-joiners."
            )
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
                f["alliance"],
                comment,
                updated_by,
                round_id,
                f["ext"],
            )
    print(f"\ninserted/updated {len(found)} planet_intel rows")


async def run(args: argparse.Namespace) -> None:
    settings = get_settings()
    conn = await asyncpg.connect(dsn=settings.db_dsn)
    try:
        data = await load_round(conn, args.round)
        print(f"round {data['round_number']}: {len(data['history'])} alliances")
        found = analyse(data, args.alliance)
        print(f"\n{len(found)} joiners uniquely identified")
        if args.insert:
            if args.updated_by is None:
                raise SystemExit("--insert requires --updated-by <discord_user_id>")
            await insert(conn, data["round_id"], found, args.updated_by)
        elif found:
            print("(read-only; pass --insert --updated-by <id> to write planet_intel)")
    finally:
        await conn.close()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--round", type=int, default=None, help="round number (default: latest)")
    ap.add_argument("--alliance", default=None, help="restrict to one alliance")
    ap.add_argument("--insert", action="store_true", help="write results to planet_intel")
    ap.add_argument("--updated-by", type=int, default=None, help="discord user id for insert")
    asyncio.run(run(ap.parse_args()))


if __name__ == "__main__":
    main()
