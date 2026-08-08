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
               ps.size, ps.score, ps.value, ps.xp, ps.special,
               RANK() OVER (ORDER BY ps.score DESC) AS rank
        FROM planet_stat ps
        JOIN planet p ON p.id = ps.planet_id
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


@router.get("/")
async def index(
    request: Request,
    conn: Connection = Depends(db_conn),
    user: Record | None = Depends(current_user),
):
    round_row = await _current_round(conn)
    tick_row = await _latest_tick(conn, round_row["id"]) if round_row else None
    counts = {"alliances": 0, "galaxies": 0, "planets": 0}
    if tick_row:
        counts["alliances"] = await conn.fetchval(
            "SELECT count(*) FROM alliance_stat WHERE tick_id = $1", tick_row["id"]
        )
        counts["galaxies"] = await conn.fetchval(
            "SELECT count(*) FROM galaxy_stat WHERE tick_id = $1", tick_row["id"]
        )
        counts["planets"] = await conn.fetchval(
            "SELECT count(*) FROM planet_stat WHERE tick_id = $1", tick_row["id"]
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
    # alliance_listing.txt has no per-planet membership (see db/schema.sql's
    # note on planet_intel), so the only "roster" for an alliance is
    # whatever's been manually tagged via !intel alliance=<name> -- shown
    # here for logged-in members only, same as the intel box on a planet.
    intel_planets = []
    if user is not None:
        tick_row = await _latest_tick(conn, alliance_row["round_id"])
        if tick_row:
            intel_planets = await conn.fetch(
                """
                SELECT p.id, ps.x, ps.y, ps.z, ps.planet_name, ps.ruler_name, ps.score,
                       pi.nick, pi.comment, pi.amps, pi.dists, pi.defwhore
                FROM planet_intel pi
                JOIN planet p ON p.id = pi.planet_id
                JOIN planet_stat ps ON ps.planet_id = p.id AND ps.tick_id = $1
                WHERE pi.alliance ILIKE $2
                ORDER BY ps.score DESC
                """,
                tick_row["id"],
                name,
            )
    return templates.TemplateResponse(
        request,
        "alliance_detail.html",
        {"name": name, "history": history, "user": user, "intel_planets": intel_planets},
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
    planets = []
    if history:
        latest_tick_id = await conn.fetchval(
            "SELECT id FROM tick WHERE round_id = (SELECT round_id FROM galaxy WHERE id = $1) ORDER BY number DESC LIMIT 1",
            galaxy_id,
        )
        planets = await conn.fetch(
            """
            SELECT p.id, ps.x, ps.y, ps.z, ps.planet_name, ps.ruler_name, ps.race,
                   ps.size, ps.score, ps.value, ps.xp, ps.special
            FROM planet_stat ps
            JOIN planet p ON p.id = ps.planet_id
            WHERE ps.tick_id = $1 AND ps.galaxy_id = $2
            ORDER BY ps.z
            """,
            latest_tick_id,
            galaxy_id,
        )
    return templates.TemplateResponse(
        request,
        "galaxy_detail.html",
        {"galaxy": galaxy, "history": history, "planets": planets, "user": user},
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
            total = await conn.fetchval(
                "SELECT count(*) FROM planet_stat WHERE tick_id = $1", tick_row["id"]
            )
            rows = await conn.fetch(
                """
                SELECT p.id, ps.x, ps.y, ps.z, ps.planet_name, ps.ruler_name, ps.race,
                       ps.size, ps.score, ps.value, ps.xp, ps.special,
                       RANK() OVER (ORDER BY ps.score DESC) AS rank
                FROM planet_stat ps
                JOIN planet p ON p.id = ps.planet_id
                WHERE ps.tick_id = $1
                ORDER BY ps.score DESC
                LIMIT $2 OFFSET $3
                """,
                tick_row["id"],
                PLANET_LIST_PAGE_SIZE,
                (page - 1) * PLANET_LIST_PAGE_SIZE,
            )
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
        {"external_id": external_id, "history": history, "user": user, "intel": intel},
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
