# BTK

A modern, simplified successor to [Merlin](../merlin): tracks Planetarion-style
tick-based browser-game data (galaxies, alliances, planets, per-tick stats,
ship stats) in Postgres, exposed via a FastAPI HTTP API and a Discord bot,
instead of Merlin's SQLAlchemy + IRC stack.

- **DB access**: `asyncpg` (raw SQL, no ORM) + `pydantic` models
- **API**: FastAPI
- **Bot**: `discord.py`
- **Containers**: Podman — `compose.yml` for local dev, `podman/*.pod`
  `.container` `.volume` `.timer` (quadlets) for production, same pattern as
  `merlin/podman/`
- **Dependencies**: [`uv`](https://docs.astral.sh/uv/)
- **Tick data**: fetched live from `https://game.planetarion.com/botfiles/`
  (PA ticks 1 minute past every hour, exactly as
  `merlin/podman/merlin-excalibur.timer` assumes) — but only after checking
  [the game status page](https://www.planetarion.com/games/status/game/)
  confirms the round is actually ticking right now, rather than blindly
  hitting the botfile URLs on a timer regardless. The round number itself
  also comes from that status check, not from config.

## Layout

```
btk/
  config.py        pydantic-settings (env vars, prefix BTK_)
  db.py             asyncpg pool management
  models/           pydantic schemas shared by the API and dump parser
  api/               FastAPI app, routers, dependencies
  bot/               discord.py bot + cogs
  dumps/
    downloader.py    fetches the live dump files + live ship stats
    parser.py         parses the "botfile" dump format (verified against a real sample)
    status.py          checks the live game status page (is it actually ticking? what round?)
    ingest.py          loads parsed rows into Postgres
    cli.py              `btk-ingest` entry point tying the above together
db/
  schema.sql         Postgres schema
scripts/
  init_db.py         applies db/schema.sql
podman/               quadlets for production (see "Production deployment" below)
tests/
```

## Getting started

```bash
cp .env.example .env        # fill in BTK_DISCORD_TOKEN, adjust DB creds
uv sync                     # installs deps + creates .venv

# Local Postgres via Podman
podman compose up -d postgres
uv run btk-initdb           # applies db/schema.sql

# Run the API (http://localhost:8000, docs at /docs)
uv run btk-api

# Run the Discord bot
uv run btk-bot

# Tests
uv run pytest
```

Or run everything in containers for local dev:

```bash
podman compose up --build
```

## Production deployment (podman quadlets)

For an always-on host, `podman/` has systemd quadlet unit files — the same
pattern as `merlin/podman/`: a shared pod, a `Restart=always` db/api/bot
container each, and a `Type=oneshot` ingest container fired by a
`.timer` unit instead of running as a long-lived process.

`podman/btk-ingest.timer` runs `btk-ingest live` at `*:01:00` — one minute
past every hour, matching PA's actual tick schedule (see
`merlin/podman/merlin-excalibur.timer`, which assumes the same). Unlike
`excalibur.py`, `btk-ingest live` checks
https://www.planetarion.com/games/status/game/ first (`btk/dumps/status.py`)
and exits cleanly if the round isn't actually ticking (between rounds,
signups-only, paused) instead of erroring on the botfile URLs' 404 — so
this timer is safe to install and leave running continuously, including
before/between rounds. A second, much-lower-frequency timer
(`btk-shipstats.timer`, daily) keeps `ship` stats current without hitting
the stats API every tick, since they're effectively static for a round
(and available even before it starts ticking).

```bash
# Build the image quadlets reference (Exec= per-container overrides its CMD)
podman build -t localhost/btk:latest .

# Install quadlets (rootless: ~/.config/containers/systemd/;
# system-wide: /etc/containers/systemd/)
mkdir -p ~/.config/containers/systemd/
cp podman/* ~/.config/containers/systemd/

# Fill in real config -- referenced via EnvironmentFile= in the .container files
mkdir -p /etc/btk && cp podman/btk.env.example /etc/btk/btk.env
# edit /etc/btk/btk.env: BTK_DISCORD_TOKEN, BTK_DB_PASSWORD (must match
# POSTGRES_PASSWORD in btk-db.container)

systemctl --user daemon-reload
systemctl --user enable --now btk-db.service btk-api.service btk-bot.service
systemctl --user enable --now btk-ingest.timer btk-shipstats.timer

uv run btk-initdb   # once, against the now-running btk-db container
```

### Reverse proxy / HTTPS (Caddy)

`btk-api` also serves a server-rendered HTML frontend (Jinja2 templates,
`btk/api/routers/web.py`) alongside the JSON API on the same port 8000 --
`/` for the dashboard, `/web/alliances`, `/web/galaxies`, `/web/planets`
(and their `/{id}` detail pages), plus the existing JSON routes and `/docs`.
For a real domain, put [Caddy](https://caddyserver.com/) in front for
automatic HTTPS rather than exposing 8000 directly.

Caddy is a plain Fedora package (`dnf install caddy`), **not** one of the
`podman/` quadlets -- its systemd unit already runs with
`CAP_NET_BIND_SERVICE`, so it can bind ports 80/443 without any of the
rootless-podman low-port workarounds (lowering
`net.ipv4.ip_unprivileged_port_start`, etc.) that publishing 80/443 from the
pod itself would need.

```bash
sudo dnf install -y caddy

# /etc/caddy/Caddyfile
cat <<'EOF' | sudo tee /etc/caddy/Caddyfile
your.domain.here {
    reverse_proxy 127.0.0.1:8000
}
EOF

sudo systemctl enable --now caddy
```

Caddy fetches and auto-renews the Let's Encrypt cert on first start (the
domain's DNS A/AAAA record must already point at the host) and redirects
HTTP to HTTPS by default. Once it's confirmed working, lock `btk.pod` down
so port 8000 is only reachable via Caddy, not directly from the internet --
`podman/btk.pod`'s `PublishPort` is already set to `127.0.0.1:8000:8000`
for this reason. If you change it, re-apply and cycle the whole pod (a
`PublishPort` change needs the pod itself recreated, not just the
containers):

```bash
systemctl --user daemon-reload
systemctl --user stop btk-api.service btk-bot.service btk-db.service btk-pod.service
systemctl --user start btk-db.service btk-api.service btk-bot.service
```

## Dump data

The current round's dumps live at fixed URLs under
`https://game.planetarion.com/botfiles/` — `planet_listing.txt`,
`galaxy_listing.txt`, `alliance_listing.txt`, `user_feed.txt` (matches
`merlin.cfg`'s `[URL] dumps`/`planets`/`galaxies`/`alliances`/`userfeed`).
There's no tick number in the URL — it's always "whatever tick it is right
now"; the actual tick number comes from each file's own `Tick:` header
after parsing (`btk/dumps/parser.py`). Ship stats come from
`https://game.planetarion.com/api.pl?stats` (matches `merlin.cfg`'s
`[URL] ships`) and are available even before the round starts ticking.

`btk-ingest live` doesn't fetch blindly: it first checks
https://www.planetarion.com/games/status/game/ (`btk/dumps/status.py`,
also exposed as `GET /status/game`) for the round number, current tick,
and whether the round is actually ticking right now — the `round.number`
(int, e.g. `118`) and `round.name` (its flavor name, e.g. `"Lost SoulS"`)
DB columns both come directly from that response rather than a configured
value — and skips the fetch entirely if the round isn't ticking:

```bash
uv run btk-ingest live             # fetch + ingest the current live tick
uv run btk-ingest live-shipstats   # fetch + ingest the live round's ship stats
```

```
$ uv run btk-ingest live
live: round 118 (Lost SoulS) is not ticking (current tick 0, last tick at 15:00 GMT on Thursday 6th August 2026) -- skipping
```

`btk/dumps/parser.py` does **not** regex-parse `user_feed.txt` content to
backfill alliance/planet/galaxy foreign keys onto `feed` rows the way
Merlin's `excalibur.py`'s `parse_userfeed` does — `feed` rows are stored as
plain (round, tick_number, category, text). That's a reasonable follow-up,
not part of this scaffold.

## Schema notes

Simplified relative to Merlin's `Core/maps/game.py`:

- No `Cluster` table — a cluster is just "galaxies sharing an x", computable
  with `GROUP BY` rather than stored and separately maintained.
- No precomputed rank-history columns (`*_highest_rank`, `*_rank_change`,
  `*_growth_pc`, ...) — rank and growth are derived at query time from
  tick-over-tick stat rows instead of being maintained as ~45 extra columns
  per entity. Re-introduce specific ones if a real query needs them.
- Planet identity is the dump's own opaque planet-id token (e.g.
  `"1ay6e0gb"`), which stays stable across ticks even when a planet
  moves/is exiled to new coordinates — unlike Merlin's `Planet.id`, which is
  a literal `"x:y:z"` string and so has to mint a new row on every move.

## Status

Schema, config, DB pool, the live dump downloader/parser/ingest pipeline,
the game-status check, and a handful of read endpoints (`/status/game`,
`/rounds`, `/alliances`, `/planets`) all work end to end:

- The full pipeline (parser + ingest + API) was verified against a real
  archived sample tick during development (r117 tick 2375: 383 planets, 61
  galaxies, 11 alliances, 876 feed items, plus 76 ship stats) before the
  archive-based backfill path was removed in favor of live-only fetching
  — the dump *format* those runs confirmed is unchanged.
- `btk-ingest live` correctly hits the real live botfile URLs, and (via
  `btk/dumps/status.py`) checks https://www.planetarion.com/games/status/game/
  first and skips cleanly when the round isn't ticking, rather than
  erroring on the resulting 404. Verified against the real status page
  (round 118 "Lost SoulS", `Ticking: No`); not yet verified against an
  actual live/ticking round, since none is running right now.
- `uv run btk-ingest live-shipstats` was verified against the real
  `api.pl?stats` endpoint for round 118 (72 ships), which is live even
  pre-round.

The Discord bot has a `!ping`/`!dbcheck` scaffold cog but no game-specific
commands yet. `user_feed.txt` cross-referencing (see above) is the main
piece of Merlin's pipeline not yet ported. The podman quadlets (`podman/`)
were validated with `podman-system-generator` (translate cleanly, correct
`Exec=` overrides) but not yet installed/run against a real systemd
instance.
