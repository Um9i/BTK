"""Server-rendered HTML frontend (Starlette/Jinja2) over the same data the JSON API exposes.

Kept as plain DB queries against the same tables the JSON routers use, rather
than the web routes calling the JSON routers over HTTP -- there's no reason
to round-trip through HTTP within the same process for the same connection
pool.
"""

import asyncio
import re
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import quote

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
# Shared page size for the planets/alliances/galaxies/needs-intel listings.
LIST_PAGE_SIZE = 50
# Detail pages' tick-history log tables (alliance/galaxy/planet) -- a
# conceptually separate listing, kept as its own constant even though the
# value happens to match.
TICK_LOG_PAGE_SIZE = 50

ALLIANCE_SORT_COLUMNS = {
    "rank": "s.rank",
    "size": "s.size",
    "members": "s.members",
    "score": "s.score",
    "points": "s.points",
    "total_score": "s.total_score",
    "total_value": "s.total_value",
}
GALAXY_SORT_COLUMNS = {"rank": "rank", "size": "size", "score": "score", "value": "value", "xp": "xp"}
# A whitelist, not a column-name mapping -- both planets_list() branches that
# use this query `ranked` (RANKED_PLANET_STAT_CTE), whose own output columns
# are already named exactly these, unqualified, so `sort` gets interpolated
# directly once validated against this set rather than looked up in a dict.
PLANET_SORT_COLUMNS = {"rank", "size", "score", "value", "xp"}

MOVERS_MODES = {"gainers", "crashers", "ratio"}
MOVERS_METRIC_COLUMNS = {"score": "score", "value": "value", "size": "size", "xp": "xp"}
# xp only ever goes up, so every planet's "xp change" over any window is
# always exactly 0 under crashers' ascending sort -- there's no such thing as
# a crasher by xp, so it's excluded here rather than in gainers'.
CRASHER_METRIC_COLUMNS = {"score": "score", "value": "value", "size": "size"}
RATIO_METRIC_COLUMNS = {"score": "score", "value": "value"}
MOVERS_DEFAULT_TICKS = 24
MOVERS_TICK_PRESETS = (1, 6, 24, 72, 168)
MOVERS_LIMIT = 50


def _sort_url_factory(current_sort: str, current_dir: str, q: str = ""):
    """Click-to-sort column headers: clicking the active column flips its direction,
    clicking a different one sorts by it fresh (ascending for "rank" -- 1st, 2nd, 3rd...
    reads naturally in that order; descending for every stat column -- biggest first is
    the interesting direction on a first click). Plain query-string GET links, so this
    needs no client state and works identically with JS off. `q` (only ever set on
    /web/planets' search results) is carried through so re-sorting doesn't silently
    drop the current search."""
    q_suffix = f"&q={quote(q)}" if q else ""

    def sort_url(field: str) -> str:
        if field == current_sort:
            new_dir = "asc" if current_dir == "desc" else "desc"
        else:
            new_dir = "asc" if field == "rank" else "desc"
        return f"?sort={field}&dir={new_dir}{q_suffix}"

    return sort_url

# Ranks every planet in the tick by score first, then lets each branch below
# filter that already-ranked set -- so "rank" on a coordinate or galaxy
# lookup is still the planet's real standing round-wide, not just its
# position within the handful of rows a search happens to return.
RANKED_PLANET_STAT_CTE = """
    WITH ranked AS (
        SELECT p.id, ps.x, ps.y, ps.z, ps.planet_name, ps.ruler_name, ps.race,
               ps.size, ps.score, ps.value, ps.xp, ps.special, pi.alliance,
               pi.nick, pi.comment, pi.amps, pi.dists, pi.defwhore,
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


def _seconds_to_next_tick() -> int:
    """Seconds until the next tick lands; PA ticks hourly at :01:00 UTC."""
    now = datetime.now(UTC)
    next_tick = now.replace(minute=1, second=0, microsecond=0)
    if next_tick <= now:
        next_tick += timedelta(hours=1)
    return int((next_tick - now).total_seconds())


def _next_tick_countdown() -> str:
    """Server-rendered T-minus fallback for when JS is unavailable."""
    h, rem = divmod(_seconds_to_next_tick(), 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def _tick_progress() -> float:
    """How far through the current tick hour we are, 0-100 -- drives the homepage tick
    spine's fill. Server-rendered so the bar is already in the right place on first paint;
    app.js then advances it every second alongside the countdown."""
    return (3600 - _seconds_to_next_tick()) / 3600 * 100


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


AVG_RATE_WINDOW = 10


def _avg_delta(history: list[dict], field: str, window: int = AVG_RATE_WINDOW) -> float | None:
    """Mean of `<field>_delta` over the most recent `window` rows of a with_deltas() history
    (newest first) -- None if there's nothing to average (e.g. a fresh round)."""
    deltas = [h[f"{field}_delta"] for h in history[:window] if h[f"{field}_delta"] is not None]
    return sum(deltas) / len(deltas) if deltas else None


def _comment_display(rows: list, comment_key: str = "comment") -> tuple[str | None, bool]:
    """Collapsing a shared comment into one subtitle line only reads right when EVERY row
    in the set actually carries it -- e.g. an entire CP-SAT-solved roster tagged with the
    same explanatory note in one pass. If only some rows have it (a mixed search result,
    a roster with a few manually-scouted outliers), collapsing still hid which row the note
    was actually about -- a real bug, not just a rare edge case. Returns
    (uniform_comment, show_comment_column): show a column whenever any row has a comment,
    unless literally all of them share the exact same one, which is the only case worth a
    single subtitle instead of N duplicate cells."""
    values = [r[comment_key] for r in rows]
    non_empty = [v for v in values if v]
    if not non_empty:
        return None, False
    distinct = set(non_empty)
    if len(distinct) == 1 and len(non_empty) == len(rows):
        return next(iter(distinct)), False
    return None, True


async def _current_round(conn: Connection):
    return await conn.fetchrow("SELECT id, number, name FROM round ORDER BY number DESC LIMIT 1")


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
        ), prev_ranked AS (
            SELECT planet_id, RANK() OVER (ORDER BY score DESC) AS rank
            FROM planet_stat WHERE tick_id = $4
        )
        SELECT p.id, ps.x, ps.y, ps.z, ps.planet_name, ps.ruler_name, ps.score,
               pi.alliance, ranked.rank, (ps.score - prev.score) AS score_delta,
               (ranked.rank - prev_ranked.rank) AS rank_delta
        FROM discord_link dl
        JOIN planet p ON p.id = dl.planet_id AND p.round_id = $3
        JOIN planet_stat ps ON ps.planet_id = p.id AND ps.tick_id = $2
        JOIN ranked ON ranked.planet_id = p.id
        LEFT JOIN planet_intel pi ON pi.planet_id = p.id
        LEFT JOIN planet_stat prev ON prev.planet_id = p.id AND prev.tick_id = $4
        LEFT JOIN prev_ranked ON prev_ranked.planet_id = p.id
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
            SELECT a.id, a.name, s.rank, s.members,
                   (s.rank - prev.rank) AS rank_delta
            FROM alliance_stat s
            JOIN alliance a ON a.id = s.alliance_id
            LEFT JOIN alliance_stat prev ON prev.alliance_id = s.alliance_id AND prev.tick_id = $3
            WHERE s.tick_id = $1 AND a.name ILIKE $2
            ORDER BY a.name
            LIMIT 1
            """,
            tick_id,
            planet["alliance"],
            prev_tick_id,
        )
        if alliance is not None:
            # Same "how much of the roster is actually scouted" question the
            # alliance page answers with a full breakdown -- here it's just
            # the headline count, one click away from the full picture.
            scouted = await conn.fetchval(
                """
                SELECT count(*)
                FROM planet_intel pi
                JOIN planet p ON p.id = pi.planet_id
                JOIN planet_stat ps ON ps.planet_id = p.id AND ps.tick_id = $1
                WHERE pi.alliance ILIKE $2
                """,
                tick_id,
                planet["alliance"],
            )
            alliance = {**dict(alliance), "scouted": scouted}
    # The game's text feed has no structured planet/galaxy reference (see
    # db/schema.sql's note on the feed table's known gap) -- a plain text
    # match on the viewer's own ruler/planet name is the only way to slice
    # "things that happened to me" out of everyone else's news.
    feed = await conn.fetch(
        """
        SELECT tick_number, category, text
        FROM feed
        WHERE round_id = $1 AND (text ILIKE $2 OR text ILIKE $3)
        ORDER BY tick_number DESC, id DESC
        LIMIT 15
        """,
        round_id,
        f"%{planet['ruler_name']}%",
        f"%{planet['planet_name']}%",
    )
    return {"linked": True, "planet": planet, "alliance": alliance, "feed": feed}


@router.get("/web/tick-status")
async def tick_status(conn: Connection = Depends(db_conn)) -> dict:
    """Polled client-side (see index.html) so the homepage can flash "new data" the
    moment a tick actually lands, instead of only finding out on the next manual
    reload. Deliberately as public as /health and /status/game -- this is operational
    freshness info, not game data, so it isn't behind the JSON API's login gate."""
    round_row = await _current_round(conn)
    if round_row is None:
        return {"round": None, "tick": None}
    tick_row = await _latest_tick(conn, round_row["id"])
    return {"round": round_row["number"], "tick": tick_row["number"] if tick_row else None}


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
    watched_planets = []
    watched_alliances = []
    if tick_row:
        counts["alliances"] = await conn.fetchval(
            "SELECT count(*) FROM alliance_stat WHERE tick_id = $1", tick_row["id"]
        )
        counts["galaxies"] = await conn.fetchval(
            """
            SELECT count(*) FROM galaxy_stat s
            JOIN galaxy g ON g.id = s.galaxy_id
            WHERE s.tick_id = $1
            """,
            tick_row["id"],
        )
        counts["planets"] = await conn.fetchval(
            "SELECT count(*) FROM planet_stat WHERE tick_id = $1", tick_row["id"]
        )
        race_rows = await conn.fetch(
            """
            SELECT race, count(*) AS planets
            FROM planet_stat
            WHERE tick_id = $1
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
            # Ranked the same way as everywhere else -- over the FULL tick, not
            # just the watched subset, so a watched planet's rank means the same
            # thing here as it does on its own detail page.
            watched_planets = await conn.fetch(
                """
                WITH ranked AS (
                    SELECT planet_id, RANK() OVER (ORDER BY score DESC) AS rank
                    FROM planet_stat WHERE tick_id = $2
                )
                SELECT p.id, ps.x, ps.y, ps.z, ps.planet_name, ps.ruler_name, ps.score,
                       ranked.rank, (ps.score - prev.score) AS score_delta
                FROM watchlist w
                JOIN planet p ON p.id = w.target_id AND w.target_type = 'planet'
                JOIN planet_stat ps ON ps.planet_id = p.id AND ps.tick_id = $2
                JOIN ranked ON ranked.planet_id = p.id
                LEFT JOIN planet_stat prev ON prev.planet_id = p.id AND prev.tick_id = $3
                WHERE w.discord_user_id = $1
                ORDER BY w.created_at
                """,
                user["discord_user_id"],
                tick_row["id"],
                prev_tick_id,
            )
            watched_alliances = await conn.fetch(
                """
                SELECT a.id, a.name, s.rank, s.total_score,
                       (s.total_score - prev.total_score) AS score_delta,
                       (s.rank - prev.rank) AS rank_delta
                FROM watchlist w
                JOIN alliance a ON a.id = w.target_id AND w.target_type = 'alliance'
                JOIN alliance_stat s ON s.alliance_id = a.id AND s.tick_id = $2
                LEFT JOIN alliance_stat prev ON prev.alliance_id = a.id AND prev.tick_id = $3
                WHERE w.discord_user_id = $1
                ORDER BY w.created_at
                """,
                user["discord_user_id"],
                tick_row["id"],
                prev_tick_id,
            )
        if prev_tick_id:
            alliance_movers = await conn.fetch(
                """
                SELECT a.id, a.name, cur.rank, cur.total_score,
                       (cur.total_score - prev.total_score) AS score_delta,
                       (cur.rank - prev.rank) AS rank_delta
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
            # galaxy_stat has no stored rank column -- computed here per tick via a
            # window function, same approach as galaxy_detail's history query.
            galaxy_movers = await conn.fetch(
                """
                WITH cur AS (
                    SELECT *, RANK() OVER (ORDER BY score DESC) AS rank
                    FROM galaxy_stat WHERE tick_id = $1
                ), prev AS (
                    SELECT *, RANK() OVER (ORDER BY score DESC) AS rank
                    FROM galaxy_stat WHERE tick_id = $2
                )
                SELECT g.id, g.x, g.y, cur.name, cur.score,
                       (cur.score - prev.score) AS score_delta,
                       cur.rank, (cur.rank - prev.rank) AS rank_delta
                FROM cur
                JOIN prev ON prev.galaxy_id = cur.galaxy_id
                JOIN galaxy g ON g.id = cur.galaxy_id
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
                WHERE cur.tick_id = $1
                ORDER BY score_delta DESC
                LIMIT 5
                """,
                tick_row["id"],
                prev_tick_id,
            )
            # A single-tick delta doesn't say whether this is a one-off spike or a
            # sustained climb -- a small trend line answers that at a glance. One
            # query for all 5 rows' last 10 ticks, rather than 5 round-trips.
            if planet_movers:
                mover_ids = [r["id"] for r in planet_movers]
                trend_rows = await conn.fetch(
                    """
                    WITH ranked AS (
                        SELECT ps.planet_id, t.number AS tick, ps.score,
                               ROW_NUMBER() OVER (PARTITION BY ps.planet_id ORDER BY t.number DESC) AS rn
                        FROM planet_stat ps
                        JOIN tick t ON t.id = ps.tick_id
                        WHERE ps.planet_id = ANY($1::int[])
                    )
                    SELECT planet_id, tick, score FROM ranked WHERE rn <= 10 ORDER BY planet_id, tick
                    """,
                    mover_ids,
                )
                by_planet: dict[int, list[int]] = defaultdict(list)
                for r in trend_rows:
                    by_planet[r["planet_id"]].append(r["score"])
                planet_movers = [
                    {**dict(r), "trend": sparkline(by_planet.get(r["id"], []))}
                    for r in planet_movers
                ]
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "round": round_row,
            "tick": tick_row,
            "counts": counts,
            "freshness": _freshness(tick_row),
            "countdown": _next_tick_countdown(),
            "tick_progress": _tick_progress(),
            "user": user,
            "alliance_movers": alliance_movers,
            "galaxy_movers": galaxy_movers,
            "planet_movers": planet_movers,
            "race_distribution": race_distribution,
            "recent_feed": recent_feed,
            "you": you,
            "watched_planets": watched_planets,
            "watched_alliances": watched_alliances,
        },
    )


@router.get("/web/alliances")
async def alliances_list(
    request: Request,
    sort: str = "rank",
    dir: str = "asc",
    page: int = 1,
    conn: Connection = Depends(db_conn),
    user: Record | None = Depends(current_user),
):
    sort = sort if sort in ALLIANCE_SORT_COLUMNS else "rank"
    dir = "desc" if dir == "desc" else "asc"
    page = max(page, 1)
    round_row = await _current_round(conn)
    rows = []
    total = 0
    tick_row = None
    if round_row:
        tick_row = await _latest_tick(conn, round_row["id"])
    if tick_row:
        total = await conn.fetchval(
            "SELECT count(*) FROM alliance_stat WHERE tick_id = $1", tick_row["id"]
        )
        rows = await conn.fetch(
            f"""
            SELECT a.id, a.name, s.rank, s.size, s.members, s.score, s.points, s.total_score, s.total_value
            FROM alliance_stat s
            JOIN alliance a ON a.id = s.alliance_id
            WHERE s.tick_id = $1
            ORDER BY {ALLIANCE_SORT_COLUMNS[sort]} {dir.upper()}
            LIMIT $2 OFFSET $3
            """,
            tick_row["id"],
            LIST_PAGE_SIZE,
            (page - 1) * LIST_PAGE_SIZE,
        )
    return templates.TemplateResponse(
        request,
        "alliances_list.html",
        {
            "round": round_row,
            "alliances": rows,
            "user": user,
            "sort": sort,
            "dir": dir,
            "sort_url": _sort_url_factory(sort, dir),
            "page": page,
            "total": total,
            "page_size": LIST_PAGE_SIZE,
        },
    )


@router.get("/web/alliances/{alliance_id}")
async def alliance_detail(
    request: Request,
    alliance_id: int,
    page: int = 1,
    conn: Connection = Depends(db_conn),
    user: Record | None = Depends(current_user),
):
    alliance_row = await conn.fetchrow(
        "SELECT name, round_id FROM alliance WHERE id = $1", alliance_id
    )
    if alliance_row is None:
        raise HTTPException(status_code=404, detail="No such alliance")
    name = alliance_row["name"]
    page = max(page, 1)
    log_total = await conn.fetchval(
        "SELECT count(*) FROM alliance_stat WHERE alliance_id = $1", alliance_id
    )
    log_fields = ["rank", "size", "score", "total_score", "total_value"]
    # Fetch one extra row past the page so the oldest visible row's delta is
    # computed against the tick just before the page, not left None just
    # because the LIMIT/OFFSET window happened to end there (see with_deltas()).
    page_rows = await conn.fetch(
        """
        SELECT t.number AS tick, s.rank, s.size, s.members, s.score, s.points, s.total_score, s.total_value
        FROM alliance_stat s
        JOIN tick t ON t.id = s.tick_id
        WHERE s.alliance_id = $1
        ORDER BY t.number DESC
        LIMIT $2 OFFSET $3
        """,
        alliance_id,
        TICK_LOG_PAGE_SIZE + 1,
        (page - 1) * TICK_LOG_PAGE_SIZE,
    )
    history = with_deltas(page_rows, log_fields)[:TICK_LOG_PAGE_SIZE]
    # Vitals/sparklines always reflect the true latest tick, independent of
    # which log page is being browsed -- page 1's own fetch already covers
    # that window, so only pages 2+ need a second, small unpaginated query.
    if page == 1:
        trend = history
    else:
        trend_rows = await conn.fetch(
            """
            SELECT t.number AS tick, s.rank, s.size, s.members, s.score, s.points, s.total_score, s.total_value
            FROM alliance_stat s
            JOIN tick t ON t.id = s.tick_id
            WHERE s.alliance_id = $1
            ORDER BY t.number DESC
            LIMIT $2
            """,
            alliance_id,
            TICK_LOG_PAGE_SIZE,
        )
        trend = with_deltas(trend_rows, log_fields)
    avg_score_delta = _avg_delta(trend, "score")
    chronological = list(reversed(trend))
    size_trend = sparkline([h["size"] for h in chronological])
    score_trend = sparkline([h["score"] for h in chronological])
    total_score_trend = sparkline([h["total_score"] for h in chronological])
    total_value_trend = sparkline([h["total_value"] for h in chronological])
    latest = trend[0] if trend else None
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
                        WHERE ps.tick_id = $1
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
    # Dists if at least one row actually has something in them. Nick and
    # Comment never get their own columns here -- they fold into the Ruler
    # tooltip (macros.intel_tooltip) the same way the planets list folds
    # them into its Alliance tooltip, since this roster has no Alliance
    # column of its own to hang them off of. uniform_comment still covers
    # the case where every row shares the exact same note (e.g. a whole
    # alliance inferred in one CP-SAT pass) -- that's worth a single
    # subtitle line instead of an identical tooltip on every row.
    show_flags = any(p["special"] or p["defwhore"] for p in intel_planets)
    show_amps = any(p["amps"] is not None for p in intel_planets)
    show_dists = any(p["dists"] is not None for p in intel_planets)
    show_nick = any(p["nick"] for p in intel_planets)
    uniform_comment, show_comment_column = _comment_display(intel_planets)
    leading_cols = 4 + (1 if show_flags else 0)  # Coords, Planet, Ruler, Race[, Flags]
    trailing_cols = sum([show_amps, show_dists])
    watching = False
    if user is not None:
        watching = await conn.fetchval(
            "SELECT 1 FROM watchlist WHERE discord_user_id = $1 AND target_type = 'alliance' AND target_id = $2",
            user["discord_user_id"],
            alliance_id,
        )
    # Rank and Members both often sit unchanged for a long stretch of a log page
    # (a stable top alliance, a roster that hasn't grown) -- checked over the
    # FULL history, not just this page, same rule galaxy Name uses above. When
    # one holds, state it once instead of repeating it down every row.
    constant_rank = None
    constant_members = None
    if log_total and log_total > 1:
        distinct_ranks = await conn.fetch(
            "SELECT DISTINCT rank FROM alliance_stat WHERE alliance_id = $1 LIMIT 2", alliance_id
        )
        if len(distinct_ranks) == 1:
            constant_rank = distinct_ranks[0]["rank"]
        distinct_members = await conn.fetch(
            "SELECT DISTINCT members FROM alliance_stat WHERE alliance_id = $1 LIMIT 2",
            alliance_id,
        )
        if len(distinct_members) == 1:
            constant_members = distinct_members[0]["members"]
    return templates.TemplateResponse(
        request,
        "alliance_detail.html",
        {
            "alliance_id": alliance_id,
            "name": name,
            "history": history,
            "latest": latest,
            "log_page": page,
            "log_total": log_total,
            "log_page_size": TICK_LOG_PAGE_SIZE,
            "size_trend": size_trend,
            "score_trend": score_trend,
            "total_score_trend": total_score_trend,
            "total_value_trend": total_value_trend,
            "avg_score_delta": avg_score_delta,
            "constant_rank": constant_rank,
            "constant_members": constant_members,
            "watching": bool(watching),
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
    sort: str = "rank",
    dir: str = "asc",
    page: int = 1,
    conn: Connection = Depends(db_conn),
    user: Record | None = Depends(current_user),
):
    sort = sort if sort in GALAXY_SORT_COLUMNS else "rank"
    dir = "desc" if dir == "desc" else "asc"
    page = max(page, 1)
    round_row = await _current_round(conn)
    rows = []
    total = 0
    tick_row = None
    if round_row:
        tick_row = await _latest_tick(conn, round_row["id"])
    if tick_row:
        total = await conn.fetchval(
            """
            SELECT count(*) FROM galaxy_stat s
            JOIN galaxy g ON g.id = s.galaxy_id
            WHERE s.tick_id = $1
            """,
            tick_row["id"],
        )
        # The RANK() window has to run unfiltered/unlimited (it needs every
        # galaxy at this tick to rank correctly) -- wrapped in a CTE so the
        # outer SELECT can sort by the computed rank and page the result.
        rows = await conn.fetch(
            f"""
            WITH ranked AS (
                SELECT g.id, g.x, g.y, s.name, s.size, s.score, s.value, s.xp,
                       RANK() OVER (ORDER BY s.score DESC) AS rank
                FROM galaxy_stat s
                JOIN galaxy g ON g.id = s.galaxy_id
                WHERE s.tick_id = $1
            )
            SELECT * FROM ranked
            ORDER BY {GALAXY_SORT_COLUMNS[sort]} {dir.upper()}
            LIMIT $2 OFFSET $3
            """,
            tick_row["id"],
            LIST_PAGE_SIZE,
            (page - 1) * LIST_PAGE_SIZE,
        )
    return templates.TemplateResponse(
        request,
        "galaxies_list.html",
        {
            "round": round_row,
            "galaxies": rows,
            "user": user,
            "sort": sort,
            "dir": dir,
            "sort_url": _sort_url_factory(sort, dir),
            "page": page,
            "total": total,
            "page_size": LIST_PAGE_SIZE,
        },
    )


@router.get("/web/movers")
async def movers(
    request: Request,
    mode: str = "gainers",
    metric: str = "score",
    ticks: int = MOVERS_DEFAULT_TICKS,
    conn: Connection = Depends(db_conn),
    user: Record | None = Depends(current_user),
):
    """Whole-universe "who's growing/shrinking fastest" and "who's biggest for their
    score/value" leaderboards -- the aggregate counterparts to a single planet's own
    !xp/!value growth history (btk/bot/cogs/stats.py). Gainers/crashers are the same
    planet compared to itself `ticks` ago; ratio has no time dimension, just this
    tick's size-per-metric across every planet -- highest first means "most size for
    the least score/value", i.e. the juiciest/weakest-for-its-size targets. This is
    deliberately the inverse of the bot's own !ratio command (metric-per-size), which
    answers a different question (score efficiency, not target-picking)."""
    mode = mode if mode in MOVERS_MODES else "gainers"
    if mode == "ratio":
        metric_columns = RATIO_METRIC_COLUMNS
    elif mode == "crashers":
        metric_columns = CRASHER_METRIC_COLUMNS
    else:
        metric_columns = MOVERS_METRIC_COLUMNS
    metric = metric if metric in metric_columns else "score"
    ticks = ticks if 1 <= ticks <= 1000 else MOVERS_DEFAULT_TICKS
    column = metric_columns[metric]

    round_row = await _current_round(conn)
    tick_row = await _latest_tick(conn, round_row["id"]) if round_row else None
    rows = []
    insufficient_history = False
    if tick_row and mode == "ratio":
        rows = await conn.fetch(
            f"""
            SELECT p.id, ps.x, ps.y, ps.z, ps.planet_name, ps.ruler_name, ps.race, ps.size,
                   ps.{column} AS metric_value, pi.alliance,
                   (ps.size::float / ps.{column} * 1000000) AS ratio
            FROM planet_stat ps
            JOIN planet p ON p.id = ps.planet_id
            LEFT JOIN planet_intel pi ON pi.planet_id = ps.planet_id
            WHERE ps.tick_id = $1 AND ps.size > 0 AND ps.{column} > 0
            ORDER BY ratio DESC
            LIMIT {MOVERS_LIMIT}
            """,
            tick_row["id"],
        )
    elif tick_row:
        old_tick_id = await conn.fetchval(
            "SELECT id FROM tick WHERE round_id = $1 ORDER BY number DESC OFFSET $2 LIMIT 1",
            round_row["id"],
            ticks,
        )
        if old_tick_id is None:
            insufficient_history = True
        else:
            direction = "DESC" if mode == "gainers" else "ASC"
            rows = await conn.fetch(
                f"""
                SELECT p.id, cur.x, cur.y, cur.z, cur.planet_name, cur.ruler_name, cur.race, pi.alliance,
                       cur.{column} AS cur_value,
                       (cur.{column} - old.{column}) AS delta
                FROM planet_stat cur
                JOIN planet_stat old ON old.planet_id = cur.planet_id AND old.tick_id = $2
                JOIN planet p ON p.id = cur.planet_id
                LEFT JOIN planet_intel pi ON pi.planet_id = cur.planet_id
                WHERE cur.tick_id = $1
                ORDER BY delta {direction}
                LIMIT {MOVERS_LIMIT}
                """,
                tick_row["id"],
                old_tick_id,
            )
    show_alliance = user is not None and any(r["alliance"] for r in rows)
    return templates.TemplateResponse(
        request,
        "movers.html",
        {
            "round": round_row,
            "tick": tick_row,
            "mode": mode,
            "metric": metric,
            "ticks": ticks,
            "tick_presets": MOVERS_TICK_PRESETS,
            "rows": rows,
            "insufficient_history": insufficient_history,
            "show_alliance": show_alliance,
            "user": user,
        },
    )


@router.get("/web/map")
async def cluster_map(
    request: Request,
    conn: Connection = Depends(db_conn),
    user: Record | None = Depends(current_user),
):
    """A galaxy-by-galaxy occupancy grid -- one row per galaxy, one glyph per planet
    slot, grouped by cluster (galaxies sharing an x, see db/schema.sql's note on
    clusters). A sorted table answers "who's #1"; this answers "where is everyone"
    at a glance, closer to reading a seating chart than a leaderboard."""
    round_row = await _current_round(conn)
    tick_row = await _latest_tick(conn, round_row["id"]) if round_row else None
    clusters = []
    viewer_alliance = None
    if tick_row:
        galaxy_rows = await conn.fetch(
            """
            SELECT g.id, g.x, g.y
            FROM galaxy_stat gs
            JOIN galaxy g ON g.id = gs.galaxy_id
            WHERE gs.tick_id = $1
            ORDER BY g.x, g.y
            """,
            tick_row["id"],
        )
        planet_rows = await conn.fetch(
            """
            SELECT p.id, ps.x, ps.y, ps.z, ps.ruler_name, ps.planet_name, pi.alliance
            FROM planet_stat ps
            JOIN planet p ON p.id = ps.planet_id
            LEFT JOIN planet_intel pi ON pi.planet_id = p.id
            WHERE ps.tick_id = $1
            """,
            tick_row["id"],
        )
        # Alliance tagging is scouted intel, same visibility rule as everywhere
        # else -- logged-out visitors get occupied/empty only, not who holds a
        # slot. Only shown at all if this user has actually linked a planet
        # with a confirmed alliance (see !link, btk/bot/cogs/preferences.py).
        if user is not None:
            viewer_alliance = await conn.fetchval(
                """
                SELECT pi.alliance
                FROM discord_link dl
                JOIN planet p ON p.id = dl.planet_id AND p.round_id = $2
                LEFT JOIN planet_intel pi ON pi.planet_id = p.id
                WHERE dl.discord_user_id = $1
                """,
                user["discord_user_id"],
                round_row["id"],
            )
        by_coord = {(r["x"], r["y"], r["z"]): r for r in planet_rows}
        max_z = max((r["z"] for r in planet_rows), default=0)

        by_x: dict[int, list] = defaultdict(list)
        for g in galaxy_rows:
            by_x[g["x"]].append(g)
        for x in sorted(by_x):
            galaxies = []
            for g in by_x[x]:
                cells = []
                for z in range(1, max_z + 1):
                    planet = by_coord.get((g["x"], g["y"], z))
                    mine = (
                        user is not None
                        and planet is not None
                        and viewer_alliance
                        and planet["alliance"]
                        and planet["alliance"].strip().lower() == viewer_alliance.strip().lower()
                    )
                    cells.append({"z": z, "planet": planet, "mine": mine})
                galaxies.append({"id": g["id"], "x": g["x"], "y": g["y"], "cells": cells})
            clusters.append({"x": x, "galaxies": galaxies})
    return templates.TemplateResponse(
        request,
        "map.html",
        {"round": round_row, "clusters": clusters, "user": user},
    )


@router.get("/web/galaxies/{galaxy_id}")
async def galaxy_detail(
    request: Request,
    galaxy_id: int,
    page: int = 1,
    conn: Connection = Depends(db_conn),
    user: Record | None = Depends(current_user),
):
    galaxy = await conn.fetchrow("SELECT id, x, y FROM galaxy WHERE id = $1", galaxy_id)
    if galaxy is None:
        raise HTTPException(status_code=404, detail="No such galaxy")
    page = max(page, 1)
    log_total = await conn.fetchval(
        "SELECT count(*) FROM galaxy_stat WHERE galaxy_id = $1", galaxy_id
    )
    log_fields = ["rank", "size", "score", "value", "xp"]
    # galaxy_stat has no stored rank column (unlike alliance_stat) -- computed here
    # per tick via a window function scoped to this galaxy's round, so both the
    # current rank and with_deltas' rank-over-time come from one query. Fetches
    # one extra row past the page so the oldest visible row's delta is computed
    # against the tick just before the page (see with_deltas()).
    page_rows = await conn.fetch(
        """
        WITH ranked AS (
            SELECT gs.galaxy_id, gs.tick_id,
                   RANK() OVER (PARTITION BY gs.tick_id ORDER BY gs.score DESC) AS rank
            FROM galaxy_stat gs
            JOIN tick t ON t.id = gs.tick_id
            WHERE t.round_id = (SELECT round_id FROM galaxy WHERE id = $1)
        )
        SELECT t.number AS tick, s.name, s.size, s.score, s.value, s.xp, r.rank
        FROM galaxy_stat s
        JOIN tick t ON t.id = s.tick_id
        JOIN ranked r ON r.galaxy_id = s.galaxy_id AND r.tick_id = s.tick_id
        WHERE s.galaxy_id = $1
        ORDER BY t.number DESC
        LIMIT $2 OFFSET $3
        """,
        galaxy_id,
        TICK_LOG_PAGE_SIZE + 1,
        (page - 1) * TICK_LOG_PAGE_SIZE,
    )
    history = with_deltas(page_rows, log_fields)[:TICK_LOG_PAGE_SIZE]
    # Vitals/sparklines always reflect the true latest tick, independent of
    # which log page is being browsed -- see the alliance_detail comment above.
    if page == 1:
        trend = history
    else:
        trend_rows = await conn.fetch(
            """
            WITH ranked AS (
                SELECT gs.galaxy_id, gs.tick_id,
                       RANK() OVER (PARTITION BY gs.tick_id ORDER BY gs.score DESC) AS rank
                FROM galaxy_stat gs
                JOIN tick t ON t.id = gs.tick_id
                WHERE t.round_id = (SELECT round_id FROM galaxy WHERE id = $1)
            )
            SELECT t.number AS tick, s.name, s.size, s.score, s.value, s.xp, r.rank
            FROM galaxy_stat s
            JOIN tick t ON t.id = s.tick_id
            JOIN ranked r ON r.galaxy_id = s.galaxy_id AND r.tick_id = s.tick_id
            WHERE s.galaxy_id = $1
            ORDER BY t.number DESC
            LIMIT $2
            """,
            galaxy_id,
            TICK_LOG_PAGE_SIZE,
        )
        trend = with_deltas(trend_rows, log_fields)
    avg_score_delta = _avg_delta(trend, "score")
    chronological = list(reversed(trend))
    size_trend = sparkline([h["size"] for h in chronological])
    score_trend = sparkline([h["score"] for h in chronological])
    value_trend = sparkline([h["value"] for h in chronological])
    xp_trend = sparkline([h["xp"] for h in chronological])
    rank = trend[0]["rank"] if trend else None
    planets = []
    if trend:
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
    # Alliance tagging is scouted intel, same visibility rule as the roster
    # on an alliance page -- logged-out visitors don't see it. And an empty
    # column is noise, so only show Flags/Alliance if this galaxy actually
    # has something in them.
    show_flags = any(p["special"] for p in planets)
    show_alliance = user is not None and any(p["alliance"] for p in planets)
    # A galaxy's Name repeats identically down the whole log the vast majority
    # of the time (galaxies are rarely renamed) -- checked over the FULL
    # history, not just this page, same "is this column constant over the
    # complete set?" rule show_flags/show_alliance already apply per-column.
    # When it does hold, state it once above the table instead of repeating
    # it down every row.
    constant_name = None
    if log_total and log_total > 1:
        distinct_names = await conn.fetch(
            "SELECT DISTINCT name FROM galaxy_stat WHERE galaxy_id = $1 LIMIT 2", galaxy_id
        )
        if len(distinct_names) == 1:
            constant_name = distinct_names[0]["name"]
    return templates.TemplateResponse(
        request,
        "galaxy_detail.html",
        {
            "galaxy": galaxy,
            "history": history,
            "latest": trend[0] if trend else None,
            "log_page": page,
            "log_total": log_total,
            "log_page_size": TICK_LOG_PAGE_SIZE,
            "planets": planets,
            "rank": rank,
            "rank_change": trend[0]["rank_delta"] if trend else None,
            "avg_score_delta": avg_score_delta,
            "size_trend": size_trend,
            "score_trend": score_trend,
            "value_trend": value_trend,
            "xp_trend": xp_trend,
            "user": user,
            "show_flags": show_flags,
            "show_alliance": show_alliance,
            "constant_name": constant_name,
        },
    )


async def _find_alliance_by_name(conn: Connection, tick_id: int, name: str) -> Record | None:
    """Exact match first, then prefix, then substring -- same tiered fallback as the
    bot's !lookup (btk/bot/cogs/intel.py's _find_alliance_stat), reused here for the
    header search's `@name` shortcut."""
    for pattern in (name, f"{name}%", f"%{name}%"):
        row = await conn.fetchrow(
            """
            SELECT a.id, a.name
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


async def _planet_flags(conn: Connection, tick_id: int, where_sql: str, *params) -> dict:
    """Whether any planet in the FULL matching set (not just whatever page happens to be
    on screen) has something in each optional column. Needed for the paginated planets_list
    branches -- computing this from just the current page's rows means a column can appear
    on page 1 and vanish on page 2 of the exact same query, reflowing the table under the
    user (a real bug caught in the 2026-08-17 audit, not hypothetical)."""
    row = await conn.fetchrow(
        RANKED_PLANET_STAT_CTE
        + f"""
        SELECT bool_or(special IS NOT NULL AND special <> '') AS show_flags,
               bool_or(alliance IS NOT NULL AND alliance <> '') AS show_alliance,
               bool_or(nick IS NOT NULL AND nick <> '') AS show_nick,
               bool_or(amps IS NOT NULL) AS show_amps,
               bool_or(dists IS NOT NULL) AS show_dists
        FROM ranked
        WHERE {where_sql}
        """,
        tick_id,
        *params,
    )
    return dict(row)


async def _planet_id_at_rank(conn: Connection, tick_id: int, rank: int) -> int | None:
    """The planet currently holding a given score rank -- backs the header search's
    `#123` shortcut ("jump to whoever's #123 right now")."""
    return await conn.fetchval(
        """
        WITH ranked AS (
            SELECT planet_id, RANK() OVER (ORDER BY score DESC) AS rank
            FROM planet_stat WHERE tick_id = $1
        )
        SELECT planet_id FROM ranked WHERE rank = $2 LIMIT 1
        """,
        tick_id,
        rank,
    )


@router.get("/web/planets")
async def planets_list(
    request: Request,
    q: str = "",
    page: int = 1,
    sort: str = "rank",
    dir: str = "asc",
    conn: Connection = Depends(db_conn),
    user: Record | None = Depends(current_user),
):
    sort = sort if sort in PLANET_SORT_COLUMNS else "rank"
    dir = "desc" if dir == "desc" else "asc"
    round_row = await _current_round(conn)
    rows = []
    tick_row = None
    total = 0
    page = max(page, 1)
    # Only the two paginated branches below (free-text search, default browse)
    # set this True -- the coord/galaxy exact-match branches return a single
    # planet or a single galaxy's worth (bounded naturally, never worth
    # paging), and the command-redirect/no-match branch never reaches here.
    paginated = False
    # Set by the two paginated branches via _planet_flags() (computed over the
    # FULL matching set, not just the page); left None for the coord/galaxy
    # branches, which fall back to the any()-over-rows check below since their
    # `rows` already IS the full set.
    flags = None
    if round_row:
        tick_row = await _latest_tick(conn, round_row["id"])
    if tick_row:
        q = q.strip()
        # "@name" / "#123" are command-style shortcuts on top of the normal
        # coord/galaxy/name search below -- @ jumps straight to an alliance,
        # # jumps to whoever currently holds that score rank. An unresolved
        # command falls through to the ordinary empty-results state rather
        # than being searched as literal text (an alliance name almost never
        # collides with a real planet/ruler name starting with "@" or "#").
        command_redirect = None
        if q.startswith("@") and len(q) > 1:
            alliance = await _find_alliance_by_name(conn, tick_row["id"], q[1:].strip())
            command_redirect = f"/web/alliances/{alliance['id']}" if alliance else False
        elif q.startswith("#") and len(q) > 1 and q[1:].isdigit():
            planet_id = await _planet_id_at_rank(conn, tick_row["id"], int(q[1:]))
            command_redirect = f"/web/planets/{planet_id}" if planet_id else False
        if command_redirect:
            return RedirectResponse(command_redirect, status_code=303)
        if command_redirect is False:
            return templates.TemplateResponse(
                request,
                "planets_list.html",
                {
                    "round": round_row,
                    "planets": [],
                    "q": q,
                    "q_param": quote(q),
                    "page": page,
                    "total": 0,
                    "page_size": LIST_PAGE_SIZE,
                    "paginated": False,
                    "user": user,
                    "show_flags": False,
                    "show_alliance": False,
                    "show_nick": False,
                    "show_amps": False,
                    "show_dists": False,
                    "show_comment_column": False,
                    "uniform_comment": None,
                    "sort": sort,
                    "dir": dir,
                    "sort_url": _sort_url_factory(sort, dir),
                },
            )
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
            # nick is scouted intel (planet_intel), not public dump data --
            # only logged-in users get to search by it, same gate as showing
            # it at all (see show_nick below).
            nick_clause = "OR nick ILIKE $2" if user is not None else ""
            match_clause = f"planet_name ILIKE $2 OR ruler_name ILIKE $2 {nick_clause}"
            total = await conn.fetchval(
                RANKED_PLANET_STAT_CTE + f"SELECT count(*) FROM ranked WHERE {match_clause}",
                tick_row["id"],
                pattern,
            )
            rows = await conn.fetch(
                RANKED_PLANET_STAT_CTE
                + f"""
                SELECT * FROM ranked
                WHERE {match_clause}
                ORDER BY {sort} {dir.upper()}
                LIMIT $3 OFFSET $4
                """,
                tick_row["id"],
                pattern,
                LIST_PAGE_SIZE,
                (page - 1) * LIST_PAGE_SIZE,
            )
            paginated = True
            flags = await _planet_flags(conn, tick_row["id"], match_clause, pattern)
        else:
            total = await conn.fetchval(
                "SELECT count(*) FROM planet_stat WHERE tick_id = $1", tick_row["id"]
            )
            # Reuses RANKED_PLANET_STAT_CTE (same columns/join the other branches
            # already query through) rather than repeating the join by hand --
            # also what makes sharing _planet_flags() below straightforward.
            rows = await conn.fetch(
                RANKED_PLANET_STAT_CTE
                + f"""
                SELECT * FROM ranked
                ORDER BY {sort} {dir.upper()}
                LIMIT $2 OFFSET $3
                """,
                tick_row["id"],
                LIST_PAGE_SIZE,
                (page - 1) * LIST_PAGE_SIZE,
            )
            paginated = True
            flags = await _planet_flags(conn, tick_row["id"], "TRUE")
    if flags is not None:
        show_flags = flags["show_flags"] or False
        show_alliance = user is not None and (flags["show_alliance"] or False)
        show_nick = user is not None and (flags["show_nick"] or False)
        show_amps = user is not None and (flags["show_amps"] or False)
        show_dists = user is not None and (flags["show_dists"] or False)
    else:
        # coord/galaxy exact-match branches (or no tick data at all) -- `rows`
        # already IS the full matching set here, so checking it directly is
        # correct, not just a fallback for a missing query.
        show_flags = any(p["special"] for p in rows)
        # Intel fields are scouted data (see planet_intel), same visibility rule
        # as the alliance-page roster -- logged-out visitors don't see them, and
        # an empty column is noise, so each only shows up if some row actually
        # has something in it.
        show_alliance = user is not None and any(p["alliance"] for p in rows)
        show_nick = user is not None and any(p["nick"] for p in rows)
        show_amps = user is not None and any(p["amps"] is not None for p in rows)
        show_dists = user is not None and any(p["dists"] is not None for p in rows)
    uniform_comment, show_comment_column = (
        _comment_display(rows) if user is not None else (None, False)
    )
    context = {
        "round": round_row,
        "planets": rows,
        "q": q,
        "q_param": quote(q),
        "page": page,
        "total": total,
        "page_size": LIST_PAGE_SIZE,
        "paginated": paginated,
        "user": user,
        "show_flags": show_flags,
        "show_alliance": show_alliance,
        "show_nick": show_nick,
        "show_amps": show_amps,
        "show_dists": show_dists,
        "show_comment_column": show_comment_column,
        "uniform_comment": uniform_comment,
        "sort": sort,
        "dir": dir,
        "sort_url": _sort_url_factory(sort, dir, q),
    }
    return templates.TemplateResponse(request, "planets_list.html", context)


@router.get("/web/needs-intel")
async def needs_intel(
    request: Request,
    page: int = 1,
    conn: Connection = Depends(db_conn),
    user: Record | None = Depends(current_user),
):
    """A ready-made scouting worklist: every planet with nothing at all tagged in
    planet_intel, biggest (most worth knowing about) first. Same visibility rule as
    every other intel surface -- logged-out visitors get sent to log in rather than
    an empty/gated page, since the entire point of this page is intel."""
    if user is None:
        return RedirectResponse(f"/login?next={request.url.path}", status_code=303)
    round_row = await _current_round(conn)
    rows = []
    total = 0
    page = max(page, 1)
    tick_row = await _latest_tick(conn, round_row["id"]) if round_row else None
    if tick_row:
        # Rank is computed against the FULL tick (RANKED_PLANET_STAT_CTE), same as
        # every other rank on the site -- filtering to the no-intel subset happens
        # after, so a planet's rank here means the same thing it does everywhere
        # else, not just "rank among the unscouted." A blank-but-present
        # planet_intel row (e.g. the web form saved with every field empty) counts
        # as "no intel" just as much as no row at all -- same completeness check
        # planet_detail.html uses to decide whether to show its own intel box.
        no_intel_clause = """
            nick IS NULL AND alliance IS NULL AND comment IS NULL
            AND amps IS NULL AND dists IS NULL AND defwhore IS NULL
        """
        total = await conn.fetchval(
            RANKED_PLANET_STAT_CTE + f"SELECT count(*) FROM ranked WHERE {no_intel_clause}",
            tick_row["id"],
        )
        rows = await conn.fetch(
            RANKED_PLANET_STAT_CTE
            + f"""
            SELECT * FROM ranked
            WHERE {no_intel_clause}
            ORDER BY score DESC
            LIMIT $2 OFFSET $3
            """,
            tick_row["id"],
            LIST_PAGE_SIZE,
            (page - 1) * LIST_PAGE_SIZE,
        )
    return templates.TemplateResponse(
        request,
        "needs_intel.html",
        {
            "round": round_row,
            "planets": rows,
            "page": page,
            "total": total,
            "page_size": LIST_PAGE_SIZE,
            "user": user,
        },
    )


@router.get("/web/planets/{planet_id}")
async def planet_detail(
    request: Request,
    planet_id: int,
    page: int = 1,
    conn: Connection = Depends(db_conn),
    user: Record | None = Depends(current_user),
):
    external_id = await conn.fetchval("SELECT external_id FROM planet WHERE id = $1", planet_id)
    if external_id is None:
        raise HTTPException(status_code=404, detail="No such planet")
    page = max(page, 1)
    log_total = await conn.fetchval(
        "SELECT count(*) FROM planet_stat WHERE planet_id = $1", planet_id
    )
    log_fields = ["rank", "size", "score", "value", "xp"]
    # planet_stat has no stored rank column -- computed here per tick via a window
    # function scoped to this planet's round, so both the current rank and
    # with_deltas' rank-over-time come from one query. Fetches one extra row past
    # the page so the oldest visible row's delta is computed against the tick
    # just before the page (see with_deltas()).
    page_rows = await conn.fetch(
        """
        WITH ranked AS (
            SELECT ps.planet_id, ps.tick_id,
                   RANK() OVER (PARTITION BY ps.tick_id ORDER BY ps.score DESC) AS rank
            FROM planet_stat ps
            JOIN tick t ON t.id = ps.tick_id
            WHERE t.round_id = (SELECT round_id FROM planet WHERE id = $1)
        )
        SELECT t.number AS tick, s.x, s.y, s.z, s.planet_name, s.ruler_name,
               s.race, s.size, s.score, s.value, s.xp, s.special, r.rank
        FROM planet_stat s
        JOIN tick t ON t.id = s.tick_id
        JOIN ranked r ON r.planet_id = s.planet_id AND r.tick_id = s.tick_id
        WHERE s.planet_id = $1
        ORDER BY t.number DESC
        LIMIT $2 OFFSET $3
        """,
        planet_id,
        TICK_LOG_PAGE_SIZE + 1,
        (page - 1) * TICK_LOG_PAGE_SIZE,
    )
    history = with_deltas(page_rows, log_fields)[:TICK_LOG_PAGE_SIZE]
    # Vitals/sparklines always reflect the true latest tick, independent of
    # which log page is being browsed -- see the alliance_detail comment above.
    if page == 1:
        trend = history
    else:
        trend_rows = await conn.fetch(
            """
            WITH ranked AS (
                SELECT ps.planet_id, ps.tick_id,
                       RANK() OVER (PARTITION BY ps.tick_id ORDER BY ps.score DESC) AS rank
                FROM planet_stat ps
                JOIN tick t ON t.id = ps.tick_id
                WHERE t.round_id = (SELECT round_id FROM planet WHERE id = $1)
            )
            SELECT t.number AS tick, s.x, s.y, s.z, s.planet_name, s.ruler_name,
                   s.race, s.size, s.score, s.value, s.xp, s.special, r.rank
            FROM planet_stat s
            JOIN tick t ON t.id = s.tick_id
            JOIN ranked r ON r.planet_id = s.planet_id AND r.tick_id = s.tick_id
            WHERE s.planet_id = $1
            ORDER BY t.number DESC
            LIMIT $2
            """,
            planet_id,
            TICK_LOG_PAGE_SIZE,
        )
        trend = with_deltas(trend_rows, log_fields)
    avg_score_delta = _avg_delta(trend, "score")
    chronological = list(reversed(trend))
    size_trend = sparkline([h["size"] for h in chronological])
    score_trend = sparkline([h["score"] for h in chronological])
    value_trend = sparkline([h["value"] for h in chronological])
    xp_trend = sparkline([h["xp"] for h in chronological])
    rank = trend[0]["rank"] if trend else None
    # Scouting notes are player-submitted intel on other alliances, not
    # public data -- only shown to logged-in (Discord-verified) members.
    intel = None
    watching = False
    if user is not None:
        intel = await conn.fetchrow(
            "SELECT nick, alliance, comment, amps, dists, defwhore FROM planet_intel WHERE planet_id = $1",
            planet_id,
        )
        watching = await conn.fetchval(
            "SELECT 1 FROM watchlist WHERE discord_user_id = $1 AND target_type = 'planet' AND target_id = $2",
            user["discord_user_id"],
            planet_id,
        )
    return templates.TemplateResponse(
        request,
        "planet_detail.html",
        {
            "planet_id": planet_id,
            "external_id": external_id,
            "history": history,
            "latest": trend[0] if trend else None,
            "log_page": page,
            "log_total": log_total,
            "log_page_size": TICK_LOG_PAGE_SIZE,
            "rank": rank,
            "rank_change": trend[0]["rank_delta"] if trend else None,
            "avg_score_delta": avg_score_delta,
            "size_trend": size_trend,
            "score_trend": score_trend,
            "value_trend": value_trend,
            "xp_trend": xp_trend,
            "user": user,
            "intel": intel,
            "watching": bool(watching),
        },
    )


@router.post("/web/planets/{planet_id}/intel")
async def update_intel(
    planet_id: int,
    nick: str = Form(""),
    alliance: str = Form(""),
    comment: str = Form(""),
    amps: str = Form(""),
    dists: str = Form(""),
    defwhore: str = Form(""),
    conn: Connection = Depends(db_conn),
    user: Record | None = Depends(current_user),
):
    """Web equivalent of the bot's !intel <x:y:z> ... (btk/bot/cogs/intel.py) -- same
    upsert, same planet_intel table, just reachable without leaving the browser tab
    already open on this planet. Unlike the bot's key=value partial update, this form
    always submits every field, so a blank field here clears that column."""
    if user is None:
        raise HTTPException(status_code=401, detail="Login required")
    exists = await conn.fetchval("SELECT 1 FROM planet WHERE id = $1", planet_id)
    if not exists:
        raise HTTPException(status_code=404, detail="No such planet")
    amps_value = int(amps) if amps.strip().isdigit() else None
    dists_value = int(dists) if dists.strip().isdigit() else None
    defwhore_value = {"yes": True, "no": False}.get(defwhore.strip().lower())
    await conn.execute(
        """
        INSERT INTO planet_intel (planet_id, nick, alliance, comment, amps, dists, defwhore, updated_by, updated_at)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, now())
        ON CONFLICT (planet_id) DO UPDATE SET
            nick = $2, alliance = $3, comment = $4, amps = $5, dists = $6, defwhore = $7,
            updated_by = $8, updated_at = now()
        """,
        planet_id,
        nick.strip() or None,
        alliance.strip() or None,
        comment.strip() or None,
        amps_value,
        dists_value,
        defwhore_value,
        user["discord_user_id"],
    )
    return RedirectResponse(f"/web/planets/{planet_id}", status_code=303)


@router.post("/web/watch")
async def toggle_watch(
    target_type: str = Form(...),
    target_id: int = Form(...),
    next: str = Form("/"),
    conn: Connection = Depends(db_conn),
    user: Record | None = Depends(current_user),
):
    """Star/unstar a planet or alliance for the homepage's Watching section. A logged-out
    POST here (the button is only ever rendered for logged-in users, but a raw request
    could still hit this) just bounces to login rather than erroring."""
    if user is None:
        return RedirectResponse(f"/login?next={next}", status_code=303)
    if target_type not in ("planet", "alliance"):
        raise HTTPException(status_code=400, detail="Invalid target_type")
    existing = await conn.fetchval(
        "SELECT id FROM watchlist WHERE discord_user_id = $1 AND target_type = $2 AND target_id = $3",
        user["discord_user_id"],
        target_type,
        target_id,
    )
    if existing:
        await conn.execute("DELETE FROM watchlist WHERE id = $1", existing)
    else:
        await conn.execute(
            "INSERT INTO watchlist (discord_user_id, target_type, target_id) VALUES ($1, $2, $3)",
            user["discord_user_id"],
            target_type,
            target_id,
        )
    return RedirectResponse(next or "/", status_code=303)


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
