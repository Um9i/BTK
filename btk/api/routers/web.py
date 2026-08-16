"""Server-rendered HTML frontend (Starlette/Jinja2) over the same data the JSON API exposes.

Kept as plain DB queries against the same tables the JSON routers use, rather
than the web routes calling the JSON routers over HTTP -- there's no reason
to round-trip through HTTP within the same process for the same connection
pool.
"""

import asyncio
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

from asyncpg import Connection, Record
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from btk.api.charts import sparkline, with_deltas
from btk.api.deps import current_user, db_conn
from btk.auth import (
    SESSION_COOKIE_NAME,
    SESSION_TTL,
    new_session_token,
    session_expiry,
    verify_password,
)
from btk.config import get_settings

router = APIRouter(include_in_schema=False)
templates = Jinja2Templates(directory=Path(__file__).resolve().parent.parent / "templates")

# Accepts ":", "." or plain whitespace as the coordinate separator, so
# "1:1:1", "1.1.1" and "1 1 1" all resolve the same way.
PLANET_COORD_RE = re.compile(r"(\d+)[:.\s]+(\d+)[:.\s]+(\d+)")
GALAXY_COORD_RE = re.compile(r"(\d+)[:.\s]+(\d+)")
PLANET_LIST_PAGE_SIZE = 50

# Ranks every planet in the tick by score first, then lets each branch below
# filter that already-ranked set -- so "rank" on a coordinate or galaxy
# lookup is still the planet's real standing round-wide, not just its
# position within the handful of rows a search happens to return.
RANKED_PLANET_STAT_CTE = """
    WITH ranked AS (
        SELECT p.id, ps.x, ps.y, ps.z, ps.planet_name, ps.ruler_name, ps.race,
               ps.size, ps.score, ps.value, ps.xp, ps.special, pi.alliance,
               RANK() OVER (ORDER BY ps.score DESC) AS rank
        FROM planet_stat ps
        JOIN planet p ON p.id = ps.planet_id
        LEFT JOIN planet_intel pi ON pi.planet_id = p.id
        WHERE ps.tick_id = $1
    )
"""

# PA ticks hourly, 1 minute past the hour (see btk-ingest.timer) -- a tick
# ingested more recently than one full tick interval ago is "live"; older
# than that means either the round stopped ticking or ingest is stuck.
LIVE_THRESHOLD_SECONDS = 70 * 60


def _next_tick_countdown() -> str:
    """Server-rendered T-minus fallback for when JS is unavailable; PA ticks hourly at :01:00 UTC."""
    now = datetime.now(UTC)
    next_tick = now.replace(minute=1, second=0, microsecond=0)
    if next_tick <= now:
        next_tick += timedelta(hours=1)
    remaining = int((next_tick - now).total_seconds())
    h, rem = divmod(remaining, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def _freshness(tick_row) -> dict:
    if tick_row is None:
        return {"live": False, "label": "NO DATA"}
    age = (datetime.now(UTC) - tick_row["inserted_at"]).total_seconds()
    if age < LIVE_THRESHOLD_SECONDS:
        return {"live": True, "label": "LIVE"}
    minutes = int(age // 60)
    if minutes < 60:
        rel = f"{minutes}m ago"
    else:
        rel = f"{minutes // 60}h {minutes % 60}m ago"
    return {"live": False, "label": f"LAST SYNC {rel}"}


async def _current_round(conn: Connection):
    return await conn.fetchrow("SELECT id, number, name FROM round ORDER BY id DESC LIMIT 1")


async def _latest_tick(conn: Connection, round_id: int):
    return await conn.fetchrow(
        "SELECT id, number, inserted_at FROM tick WHERE round_id = $1 ORDER BY number DESC LIMIT 1",
        round_id,
    )


async def _you_panel(
    conn: Connection, user: Record, round_id: int, tick_id: int, prev_tick_id: int | None
) -> dict:
    """The logged-in member's own planet, if linked (see !link, btk/bot/cogs/preferences.py).

    discord_link.planet_id points at a specific round's planet row, so it
    naturally goes stale across rounds -- the "p.round_id = $3" join below
    is what makes a link from a past round quietly fall back to the
    "not linked yet" nudge rather than showing another round's planet.
    """
    planet = await conn.fetchrow(
        """
        WITH ranked AS (
            SELECT planet_id, RANK() OVER (ORDER BY score DESC) AS rank
            FROM planet_stat WHERE tick_id = $2
        )
        SELECT p.id, ps.x, ps.y, ps.z, ps.planet_name, ps.ruler_name, ps.score,
               pi.alliance, ranked.rank, (ps.score - prev.score) AS score_delta
        FROM discord_link dl
        JOIN planet p ON p.id = dl.planet_id AND p.round_id = $3
        JOIN planet_stat ps ON ps.planet_id = p.id AND ps.tick_id = $2
        JOIN ranked ON ranked.planet_id = p.id
        LEFT JOIN planet_intel pi ON pi.planet_id = p.id
        LEFT JOIN planet_stat prev ON prev.planet_id = p.id AND prev.tick_id = $4
        WHERE dl.discord_user_id = $1
        """,
        user["discord_user_id"],
        tick_id,
        round_id,
        prev_tick_id,
    )
    if planet is None:
        return {"linked": False}
    alliance = None
    if planet["alliance"]:
        alliance = await conn.fetchrow(
            """
            SELECT a.id, a.name, s.rank
            FROM alliance_stat s
            JOIN alliance a ON a.id = s.alliance_id
            WHERE s.tick_id = $1 AND a.name ILIKE $2
            ORDER BY a.name
            LIMIT 1
            """,
            tick_id,
            planet["alliance"],
        )
    return {"linked": True, "planet": planet, "alliance": alliance}


@router.get("/")
async def index(
    request: Request,
    conn: Connection = Depends(db_conn),
    user: Record | None = Depends(current_user),
):
    round_row = await _current_round(conn)
    tick_row = await _latest_tick(conn, round_row["id"]) if round_row else None
    counts = {"alliances": 0, "galaxies": 0, "planets": 0}
    alliance_movers = []
    galaxy_movers = []
    planet_movers = []
    race_distribution = []
    recent_feed = []
    you = None
    if tick_row:
        counts["alliances"] = await conn.fetchval(
            "SELECT count(*) FROM alliance_stat WHERE tick_id = $1", tick_row["id"]
        )
        # x:200 is PA's reserved/system cluster, not real player space (same
        # exclusion as the "unclaimed" dimming on the galaxies list) -- left
        # in here, the headline count wouldn't match what scrolling the
        # actual list shows.
        counts["galaxies"] = await conn.fetchval(
            """
            SELECT count(*) FROM galaxy_stat s
            JOIN galaxy g ON g.id = s.galaxy_id
            WHERE s.tick_id = $1 AND g.x != 200
            """,
            tick_row["id"],
        )
        counts["planets"] = await conn.fetchval(
            "SELECT count(*) FROM planet_stat WHERE tick_id = $1 AND x != 200", tick_row["id"]
        )
        race_rows = await conn.fetch(
            """
            SELECT race, count(*) AS planets
            FROM planet_stat
            WHERE tick_id = $1 AND x != 200
            GROUP BY race
            ORDER BY planets DESC
            """,
            tick_row["id"],
        )
        race_total = sum(r["planets"] for r in race_rows) or 1
        race_distribution = [
            {"race": r["race"], "planets": r["planets"], "pct": r["planets"] / race_total * 100}
            for r in race_rows
        ]
        recent_feed = await conn.fetch(
            """
            SELECT tick_number, category, text
            FROM feed
            WHERE round_id = $1
            ORDER BY tick_number DESC, id DESC
            LIMIT 15
            """,
            round_row["id"],
        )
        # Standings (who's #1 right now) are already one click away in-game --
        # BTK's own value is history, so the homepage leads with *movers*
        # (score delta since last tick) instead of duplicating a current-
        # state leaderboard the game already shows. That needs a previous
        # tick to diff against, which the very first tick of a round has
        # none of -- movers (and the "you" panel's delta) just stay empty
        # rather than comparing against nothing.
        prev_tick_id = await conn.fetchval(
            "SELECT id FROM tick WHERE round_id = $1 AND number < $2 ORDER BY number DESC LIMIT 1",
            round_row["id"],
            tick_row["number"],
        )
        if user is not None:
            you = await _you_panel(conn, user, round_row["id"], tick_row["id"], prev_tick_id)
        if prev_tick_id:
            alliance_movers = await conn.fetch(
                """
                SELECT a.id, a.name, cur.rank, cur.total_score,
                       (cur.total_score - prev.total_score) AS score_delta
                FROM alliance_stat cur
                JOIN alliance_stat prev ON prev.alliance_id = cur.alliance_id AND prev.tick_id = $2
                JOIN alliance a ON a.id = cur.alliance_id
                WHERE cur.tick_id = $1
                ORDER BY score_delta DESC
                LIMIT 3
                """,
                tick_row["id"],
                prev_tick_id,
            )
            galaxy_movers = await conn.fetch(
                """
                SELECT g.id, g.x, g.y, cur.name, cur.score,
                       (cur.score - prev.score) AS score_delta
                FROM galaxy_stat cur
                JOIN galaxy_stat prev ON prev.galaxy_id = cur.galaxy_id AND prev.tick_id = $2
                JOIN galaxy g ON g.id = cur.galaxy_id
                WHERE cur.tick_id = $1 AND g.x != 200
                ORDER BY score_delta DESC
                LIMIT 3
                """,
                tick_row["id"],
                prev_tick_id,
            )
            planet_movers = await conn.fetch(
                """
                SELECT p.id, cur.x, cur.y, cur.z, cur.planet_name, cur.ruler_name, cur.race, cur.score,
                       (cur.score - prev.score) AS score_delta
                FROM planet_stat cur
                JOIN planet_stat prev ON prev.planet_id = cur.planet_id AND prev.tick_id = $2
                JOIN planet p ON p.id = cur.planet_id
                WHERE cur.tick_id = $1 AND cur.x != 200
                ORDER BY score_delta DESC
                LIMIT 5
                """,
                tick_row["id"],
                prev_tick_id,
            )
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "round": round_row,
            "tick": tick_row,
            "counts": counts,
            "freshness": _freshness(tick_row),
            "countdown": _next_tick_countdown(),
            "user": user,
            "alliance_movers": alliance_movers,
            "galaxy_movers": galaxy_movers,
            "planet_movers": planet_movers,
            "race_distribution": race_distribution,
            "recent_feed": recent_feed,
            "you": you,
        },
    )


@router.get("/web/alliances")
async def alliances_list(
    request: Request,
    conn: Connection = Depends(db_conn),
    user: Record | None = Depends(current_user),
):
    round_row = await _current_round(conn)
    rows = []
    tick_row = None
    if round_row:
        tick_row = await _latest_tick(conn, round_row["id"])
    if tick_row:
        rows = await conn.fetch(
            """
            SELECT a.id, a.name, s.rank, s.size, s.members, s.score, s.points, s.total_score, s.total_value
            FROM alliance_stat s
            JOIN alliance a ON a.id = s.alliance_id
            WHERE s.tick_id = $1
            ORDER BY s.rank
            """,
            tick_row["id"],
        )
    return templates.TemplateResponse(
        request, "alliances_list.html", {"round": round_row, "alliances": rows, "user": user}
    )


@router.get("/web/alliances/{alliance_id}")
async def alliance_detail(
    request: Request,
    alliance_id: int,
    conn: Connection = Depends(db_conn),
    user: Record | None = Depends(current_user),
):
    alliance_row = await conn.fetchrow(
        "SELECT name, round_id FROM alliance WHERE id = $1", alliance_id
    )
    if alliance_row is None:
        raise HTTPException(status_code=404, detail="No such alliance")
    name = alliance_row["name"]
    history = await conn.fetch(
        """
        SELECT t.number AS tick, s.rank, s.size, s.members, s.score, s.points, s.total_score, s.total_value
        FROM alliance_stat s
        JOIN tick t ON t.id = s.tick_id
        WHERE s.alliance_id = $1
        ORDER BY t.number DESC
        LIMIT 50
        """,
        alliance_id,
    )
    history = with_deltas(history, ["size", "score", "total_score", "total_value"])
    chronological = list(reversed(history))
    size_trend = sparkline([h["size"] for h in chronological])
    score_trend = sparkline([h["score"] for h in chronological])
    total_score_trend = sparkline([h["total_score"] for h in chronological])
    total_value_trend = sparkline([h["total_value"] for h in chronological])
    # alliance_listing.txt has no per-planet membership (see db/schema.sql's
    # note on planet_intel), so the only "roster" for an alliance is
    # whatever's been manually tagged via !intel alliance=<name> -- shown
    # here for logged-in members only, same as the intel box on a planet.
    intel_planets = []
    intel_coverage = None
    suggested_planets = []
    if user is not None:
        tick_row = await _latest_tick(conn, alliance_row["round_id"])
        if tick_row:
            intel_planets = await conn.fetch(
                """
                SELECT p.id, ps.x, ps.y, ps.z, ps.planet_name, ps.ruler_name, ps.race, ps.special,
                       ps.score, ps.value, ps.size,
                       pi.nick, pi.comment, pi.amps, pi.dists, pi.defwhore
                FROM planet_intel pi
                JOIN planet p ON p.id = pi.planet_id
                JOIN planet_stat ps ON ps.planet_id = p.id AND ps.tick_id = $1
                WHERE pi.alliance ILIKE $2
                ORDER BY ps.x, ps.y, ps.z
                """,
                tick_row["id"],
                name,
            )
            # "Accuracy" here means completeness -- how much of the alliance's own
            # reported totals (total_score/total_value/size, summed across every
            # member) the scouted roster actually accounts for. There's no
            # membership list to check against (see the comment above), so this
            # is the only honest cross-check available: if the roster is
            # complete and each planet's stats are current, the sums should
            # land at ~100%.
            latest = history[0] if history else None
            if latest is not None:
                roster_score = sum(p["score"] for p in intel_planets)
                roster_value = sum(p["value"] for p in intel_planets)
                roster_size = sum(p["size"] for p in intel_planets)
                intel_coverage = {
                    "score": roster_score,
                    "total_score": latest["total_score"],
                    "value": roster_value,
                    "total_value": latest["total_value"],
                    "size": roster_size,
                    "total_size": latest["size"],
                    "score_pct": (roster_score / latest["total_score"] * 100)
                    if latest["total_score"]
                    else None,
                    "value_pct": (roster_value / latest["total_value"] * 100)
                    if latest["total_value"]
                    else None,
                    "size_pct": (roster_size / latest["size"] * 100) if latest["size"] else None,
                }
                # Coverage is short by some number of members and some amount of
                # score/value/size -- divide each by the missing member count to
                # get an expected "average missing member" across all three, and
                # rank candidate planets by combined closeness (a relative
                # distance across all three dimensions, not score alone) --
                # e.g. a one-member alliance's founder planet should match its
                # reported size *and* score *and* value almost exactly, which
                # score alone can't tell apart from a same-scoring planet of a
                # very different size. Never a detection, just a fit.
                #
                # Only shown for alliances with exactly one member. Averaging
                # the gap across N missing members and matching against that
                # single average profile only makes sense when there's
                # actually one real planet at that average -- for larger
                # alliances every member has its own size/score/value, often
                # wildly different from the alliance-wide average, so
                # "closest to the average" carries no real signal. Confirmed
                # empirically: validated this ranking against an alliance
                # whose true roster was independently solved by exact
                # multi-tick subset-sum matching, and true members' rank
                # positions were scattered across the entire candidate list
                # (as bad as 264th out of ~300) rather than clustered near
                # the top -- see docs/alliance-membership-inference.md.
                #
                # Candidates already tagged to THIS alliance are excluded (they're
                # already in the roster above) but candidates tagged to a
                # *different* alliance are still included and flagged in the
                # template -- excluding them outright would hide exactly the
                # case that matters most: a planet that's a near-exact fit but
                # was mistagged to the wrong alliance by a scouting mistake.
                remaining_members = latest["members"] - len(intel_planets)
                remaining_score = latest["total_score"] - roster_score
                remaining_value = latest["total_value"] - roster_value
                remaining_size = latest["size"] - roster_size
                if latest["members"] == 1 and remaining_members > 0 and remaining_score > 0:
                    avg_missing_score = remaining_score / remaining_members
                    avg_missing_value = remaining_value / remaining_members
                    avg_missing_size = remaining_size / remaining_members
                    suggested_planets = await conn.fetch(
                        """
                        SELECT p.id, ps.x, ps.y, ps.z, ps.planet_name, ps.ruler_name,
                               ps.score, ps.value, ps.size, pi.alliance AS tagged_alliance
                        FROM planet_stat ps
                        JOIN planet p ON p.id = ps.planet_id
                        LEFT JOIN planet_intel pi ON pi.planet_id = p.id
                        WHERE ps.tick_id = $1 AND ps.x != 200
                              AND (pi.alliance IS NULL OR pi.alliance = '' OR pi.alliance NOT ILIKE $5)
                        ORDER BY
                            POWER((ps.score - $2) / NULLIF($2, 0)::float, 2)
                            + POWER((ps.value - $3) / NULLIF($3, 0)::float, 2)
                            + POWER((ps.size - $4) / NULLIF($4, 0)::float, 2)
                            ASC NULLS LAST
                        LIMIT 8
                        """,
                        tick_row["id"],
                        avg_missing_score,
                        avg_missing_value,
                        avg_missing_size,
                        name,
                    )
                    intel_coverage["remaining_members"] = remaining_members
                    intel_coverage["avg_missing_score"] = avg_missing_score
                    intel_coverage["avg_missing_value"] = avg_missing_value
                    intel_coverage["avg_missing_size"] = avg_missing_size
    # Empty columns are noise in a dense table -- only render Flags/Amps/
    # Dists/Nick if at least one row actually has something in them. And a
    # Comment repeated identically on every row (e.g. every inferred member
    # of an algorithmically-solved alliance) says nothing per-row -- surface
    # it once as a note instead of 40 duplicate cells.
    show_flags = any(p["special"] or p["defwhore"] for p in intel_planets)
    show_amps = any(p["amps"] is not None for p in intel_planets)
    show_dists = any(p["dists"] is not None for p in intel_planets)
    show_nick = any(p["nick"] for p in intel_planets)
    comment_values = {p["comment"] for p in intel_planets if p["comment"]}
    uniform_comment = next(iter(comment_values)) if len(comment_values) == 1 else None
    show_comment_column = len(comment_values) > 1
    leading_cols = 4 + (1 if show_flags else 0)  # Coords, Planet, Ruler, Race[, Flags]
    trailing_cols = sum([show_amps, show_dists, show_nick, show_comment_column])
    return templates.TemplateResponse(
        request,
        "alliance_detail.html",
        {
            "name": name,
            "history": history,
            "size_trend": size_trend,
            "score_trend": score_trend,
            "total_score_trend": total_score_trend,
            "total_value_trend": total_value_trend,
            "user": user,
            "intel_planets": intel_planets,
            "intel_coverage": intel_coverage,
            "suggested_planets": suggested_planets,
            "show_flags": show_flags,
            "show_amps": show_amps,
            "show_dists": show_dists,
            "show_nick": show_nick,
            "show_comment_column": show_comment_column,
            "uniform_comment": uniform_comment,
            "roster_leading_cols": leading_cols,
            "roster_trailing_cols": trailing_cols,
        },
    )


@router.get("/web/galaxies")
async def galaxies_list(
    request: Request,
    conn: Connection = Depends(db_conn),
    user: Record | None = Depends(current_user),
):
    round_row = await _current_round(conn)
    rows = []
    tick_row = None
    if round_row:
        tick_row = await _latest_tick(conn, round_row["id"])
    if tick_row:
        rows = await conn.fetch(
            """
            SELECT g.id, g.x, g.y, s.name, s.size, s.score, s.value, s.xp,
                   RANK() OVER (ORDER BY s.score DESC) AS rank
            FROM galaxy_stat s
            JOIN galaxy g ON g.id = s.galaxy_id
            WHERE s.tick_id = $1
            ORDER BY s.score DESC
            """,
            tick_row["id"],
        )
    return templates.TemplateResponse(
        request, "galaxies_list.html", {"round": round_row, "galaxies": rows, "user": user}
    )


@router.get("/web/galaxies/{galaxy_id}")
async def galaxy_detail(
    request: Request,
    galaxy_id: int,
    conn: Connection = Depends(db_conn),
    user: Record | None = Depends(current_user),
):
    galaxy = await conn.fetchrow("SELECT id, x, y FROM galaxy WHERE id = $1", galaxy_id)
    if galaxy is None:
        raise HTTPException(status_code=404, detail="No such galaxy")
    history = await conn.fetch(
        """
        SELECT t.number AS tick, s.name, s.size, s.score, s.value, s.xp
        FROM galaxy_stat s
        JOIN tick t ON t.id = s.tick_id
        WHERE s.galaxy_id = $1
        ORDER BY t.number DESC
        LIMIT 50
        """,
        galaxy_id,
    )
    history = with_deltas(history, ["size", "score", "value", "xp"])
    chronological = list(reversed(history))
    size_trend = sparkline([h["size"] for h in chronological])
    score_trend = sparkline([h["score"] for h in chronological])
    value_trend = sparkline([h["value"] for h in chronological])
    xp_trend = sparkline([h["xp"] for h in chronological])
    planets = []
    rank = None
    if history:
        latest_tick_id = await conn.fetchval(
            "SELECT id FROM tick WHERE round_id = (SELECT round_id FROM galaxy WHERE id = $1) ORDER BY number DESC LIMIT 1",
            galaxy_id,
        )
        planets = await conn.fetch(
            """
            SELECT p.id, ps.x, ps.y, ps.z, ps.planet_name, ps.ruler_name, ps.race,
                   ps.size, ps.score, ps.value, ps.xp, ps.special, pi.alliance
            FROM planet_stat ps
            JOIN planet p ON p.id = ps.planet_id
            LEFT JOIN planet_intel pi ON pi.planet_id = p.id
            WHERE ps.tick_id = $1 AND ps.galaxy_id = $2
            ORDER BY ps.z
            """,
            latest_tick_id,
            galaxy_id,
        )
        rank = await conn.fetchval(
            """
            WITH ranked AS (
                SELECT galaxy_id, RANK() OVER (ORDER BY score DESC) AS rank
                FROM galaxy_stat
                WHERE tick_id = $1
            )
            SELECT rank FROM ranked WHERE galaxy_id = $2
            """,
            latest_tick_id,
            galaxy_id,
        )
    # Alliance tagging is scouted intel, same visibility rule as the roster
    # on an alliance page -- logged-out visitors don't see it. And an empty
    # column is noise, so only show Flags/Alliance if this galaxy actually
    # has something in them.
    show_flags = any(p["special"] for p in planets)
    show_alliance = user is not None and any(p["alliance"] for p in planets)
    return templates.TemplateResponse(
        request,
        "galaxy_detail.html",
        {
            "galaxy": galaxy,
            "history": history,
            "planets": planets,
            "rank": rank,
            "size_trend": size_trend,
            "score_trend": score_trend,
            "value_trend": value_trend,
            "xp_trend": xp_trend,
            "user": user,
            "show_flags": show_flags,
            "show_alliance": show_alliance,
        },
    )


@router.get("/web/planets")
async def planets_list(
    request: Request,
    q: str = "",
    page: int = 1,
    conn: Connection = Depends(db_conn),
    user: Record | None = Depends(current_user),
):
    round_row = await _current_round(conn)
    rows = []
    tick_row = None
    total = 0
    page = max(page, 1)
    if round_row:
        tick_row = await _latest_tick(conn, round_row["id"])
    if tick_row:
        q = q.strip()
        coord_match = PLANET_COORD_RE.fullmatch(q)
        galaxy_match = None if coord_match else GALAXY_COORD_RE.fullmatch(q)
        if coord_match:
            x, y, z = (int(v) for v in coord_match.groups())
            rows = await conn.fetch(
                RANKED_PLANET_STAT_CTE + "SELECT * FROM ranked WHERE x = $2 AND y = $3 AND z = $4",
                tick_row["id"],
                x,
                y,
                z,
            )
            total = len(rows)
        elif galaxy_match:
            x, y = (int(v) for v in galaxy_match.groups())
            rows = await conn.fetch(
                RANKED_PLANET_STAT_CTE + "SELECT * FROM ranked WHERE x = $2 AND y = $3 ORDER BY z",
                tick_row["id"],
                x,
                y,
            )
            total = len(rows)
        elif q:
            pattern = f"%{q}%"
            rows = await conn.fetch(
                RANKED_PLANET_STAT_CTE
                + """
                SELECT * FROM ranked
                WHERE planet_name ILIKE $2 OR ruler_name ILIKE $2
                ORDER BY score DESC
                LIMIT 100
                """,
                tick_row["id"],
                pattern,
            )
            total = len(rows)
        else:
            # x:200 is PA's reserved/system cluster, not real player space --
            # excluded from browsing/counts the same way the homepage totals
            # and the galaxies list already treat it. A direct coordinate or
            # galaxy lookup above still honors an explicit 200:x search.
            total = await conn.fetchval(
                "SELECT count(*) FROM planet_stat WHERE tick_id = $1 AND x != 200", tick_row["id"]
            )
            rows = await conn.fetch(
                """
                SELECT p.id, ps.x, ps.y, ps.z, ps.planet_name, ps.ruler_name, ps.race,
                       ps.size, ps.score, ps.value, ps.xp, ps.special, pi.alliance,
                       RANK() OVER (ORDER BY ps.score DESC) AS rank
                FROM planet_stat ps
                JOIN planet p ON p.id = ps.planet_id
                LEFT JOIN planet_intel pi ON pi.planet_id = p.id
                WHERE ps.tick_id = $1 AND ps.x != 200
                ORDER BY ps.score DESC
                LIMIT $2 OFFSET $3
                """,
                tick_row["id"],
                PLANET_LIST_PAGE_SIZE,
                (page - 1) * PLANET_LIST_PAGE_SIZE,
            )
    show_flags = any(p["special"] for p in rows)
    show_alliance = user is not None and any(p["alliance"] for p in rows)
    return templates.TemplateResponse(
        request,
        "planets_list.html",
        {
            "round": round_row,
            "planets": rows,
            "q": q,
            "page": page,
            "total": total,
            "page_size": PLANET_LIST_PAGE_SIZE,
            "paginated": not q,
            "user": user,
            "show_flags": show_flags,
            "show_alliance": show_alliance,
        },
    )


@router.get("/web/planets/{planet_id}")
async def planet_detail(
    request: Request,
    planet_id: int,
    conn: Connection = Depends(db_conn),
    user: Record | None = Depends(current_user),
):
    external_id = await conn.fetchval("SELECT external_id FROM planet WHERE id = $1", planet_id)
    if external_id is None:
        raise HTTPException(status_code=404, detail="No such planet")
    history = await conn.fetch(
        """
        SELECT t.number AS tick, s.x, s.y, s.z, s.planet_name, s.ruler_name,
               s.race, s.size, s.score, s.value, s.xp, s.special
        FROM planet_stat s
        JOIN tick t ON t.id = s.tick_id
        WHERE s.planet_id = $1
        ORDER BY t.number DESC
        LIMIT 50
        """,
        planet_id,
    )
    history = with_deltas(history, ["size", "score", "value", "xp"])
    chronological = list(reversed(history))
    size_trend = sparkline([h["size"] for h in chronological])
    score_trend = sparkline([h["score"] for h in chronological])
    value_trend = sparkline([h["value"] for h in chronological])
    xp_trend = sparkline([h["xp"] for h in chronological])
    rank = None
    if history:
        latest_tick_id = await conn.fetchval(
            "SELECT id FROM tick WHERE round_id = (SELECT round_id FROM planet WHERE id = $1) ORDER BY number DESC LIMIT 1",
            planet_id,
        )
        rank = await conn.fetchval(
            """
            WITH ranked AS (
                SELECT planet_id, RANK() OVER (ORDER BY score DESC) AS rank
                FROM planet_stat
                WHERE tick_id = $1
            )
            SELECT rank FROM ranked WHERE planet_id = $2
            """,
            latest_tick_id,
            planet_id,
        )
    # Scouting notes are player-submitted intel on other alliances, not
    # public data -- only shown to logged-in (Discord-verified) members.
    intel = None
    if user is not None:
        intel = await conn.fetchrow(
            "SELECT nick, alliance, comment, amps, dists, defwhore FROM planet_intel WHERE planet_id = $1",
            planet_id,
        )
    return templates.TemplateResponse(
        request,
        "planet_detail.html",
        {
            "external_id": external_id,
            "history": history,
            "rank": rank,
            "size_trend": size_trend,
            "score_trend": score_trend,
            "value_trend": value_trend,
            "xp_trend": xp_trend,
            "user": user,
            "intel": intel,
        },
    )


@router.get("/login")
async def login_form(
    request: Request, next: str = "/", user: Record | None = Depends(current_user)
):
    if user is not None:
        return RedirectResponse(next, status_code=303)
    return templates.TemplateResponse(
        request, "login.html", {"next": next, "error": None, "user": None}
    )


@router.post("/login")
async def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    next: str = Form("/"),
    conn: Connection = Depends(db_conn),
):
    row = await conn.fetchrow(
        "SELECT discord_user_id, password_hash FROM web_credential WHERE username = $1",
        username.strip(),
    )
    valid = row is not None and await asyncio.to_thread(
        verify_password, password, row["password_hash"]
    )
    if not valid:
        return templates.TemplateResponse(
            request,
            "login.html",
            {"next": next, "error": "Wrong username or password.", "user": None},
            status_code=401,
        )

    token = new_session_token()
    await conn.execute(
        "INSERT INTO web_session (token, discord_user_id, expires_at) VALUES ($1, $2, $3)",
        token,
        row["discord_user_id"],
        session_expiry(),
    )
    response = RedirectResponse(next or "/", status_code=303)
    response.set_cookie(
        SESSION_COOKIE_NAME,
        token,
        httponly=True,
        secure=get_settings().web_cookie_secure,
        samesite="lax",
        max_age=int(SESSION_TTL.total_seconds()),
    )
    return response


@router.post("/logout")
async def logout(request: Request, conn: Connection = Depends(db_conn)):
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if token:
        await conn.execute("DELETE FROM web_session WHERE token = $1", token)
    response = RedirectResponse("/", status_code=303)
    response.delete_cookie(SESSION_COOKIE_NAME)
    return response
