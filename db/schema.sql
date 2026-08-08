-- BTK schema, modeling the real Planetarion-style dump format served live at
-- https://game.planetarion.com/botfiles/ (planet_listing.txt,
-- galaxy_listing.txt, alliance_listing.txt, user_feed.txt) plus the
-- round-level ship stats at https://game.planetarion.com/api.pl?stats.
--
-- Simplified relative to Merlin's Core/maps/game.py: no Cluster table (a
-- cluster is just "galaxies sharing an x", computable with a GROUP BY, not
-- stored) and no pre-computed rank-history bloat (highest/lowest rank ever,
-- rank_change, growth_pc, ...) -- rank and growth are derived at query time
-- from tick-over-tick stat rows instead of maintained as extra columns.
-- Re-introduce those only if they turn out to be needed.

CREATE TABLE IF NOT EXISTS round (
    id          SERIAL PRIMARY KEY,
    number      INTEGER NOT NULL UNIQUE,   -- e.g. 118, from the game status page
    name        TEXT,                      -- the round's flavor name, e.g. 'Lost SoulS'
    started_at  TIMESTAMPTZ,
    ended_at    TIMESTAMPTZ
);

-- One row per successfully-ingested tick (mirrors Merlin's `updates` table).
CREATE TABLE IF NOT EXISTS tick (
    id            SERIAL PRIMARY KEY,
    round_id      INTEGER NOT NULL REFERENCES round(id) ON DELETE CASCADE,
    number        INTEGER NOT NULL,        -- the dump's own "Tick:" header value
    etag          TEXT,
    last_modified TEXT,
    inserted_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (round_id, number)
);

-- Alliances: the dump has no numeric alliance id, only a name -- identity
-- is the name, scoped to a round (a name can be reused round to round).
CREATE TABLE IF NOT EXISTS alliance (
    id          SERIAL PRIMARY KEY,
    round_id    INTEGER NOT NULL REFERENCES round(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    UNIQUE (round_id, name)
);

-- alliance_listing.txt: rank  "name"  size  members  counted_score  points  total_score  total_value
CREATE TABLE IF NOT EXISTS alliance_stat (
    id          SERIAL PRIMARY KEY,
    tick_id     INTEGER NOT NULL REFERENCES tick(id) ON DELETE CASCADE,
    alliance_id INTEGER NOT NULL REFERENCES alliance(id) ON DELETE CASCADE,
    rank        INTEGER NOT NULL,
    size        INTEGER NOT NULL DEFAULT 0,
    members     INTEGER NOT NULL DEFAULT 0,
    score       BIGINT NOT NULL DEFAULT 0,   -- "counted_score" in the dump
    points      INTEGER NOT NULL DEFAULT 0,
    total_score BIGINT NOT NULL DEFAULT 0,
    total_value BIGINT NOT NULL DEFAULT 0,
    UNIQUE (tick_id, alliance_id)
);

-- Galaxies: identity is (x, y) within a round; galaxy names can change.
CREATE TABLE IF NOT EXISTS galaxy (
    id          SERIAL PRIMARY KEY,
    round_id    INTEGER NOT NULL REFERENCES round(id) ON DELETE CASCADE,
    x           INTEGER NOT NULL,
    y           INTEGER NOT NULL,
    UNIQUE (round_id, x, y)
);

-- galaxy_listing.txt: x  y  "name"  size  score  value  xp
CREATE TABLE IF NOT EXISTS galaxy_stat (
    id          SERIAL PRIMARY KEY,
    tick_id     INTEGER NOT NULL REFERENCES tick(id) ON DELETE CASCADE,
    galaxy_id   INTEGER NOT NULL REFERENCES galaxy(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    size        INTEGER NOT NULL DEFAULT 0,
    score       BIGINT NOT NULL DEFAULT 0,
    value       BIGINT NOT NULL DEFAULT 0,
    xp          INTEGER NOT NULL DEFAULT 0,
    UNIQUE (tick_id, galaxy_id)
);

-- Planets: the dump's own planet id (e.g. "1ay6e0gb") is an opaque token
-- that stays stable across ticks even when the planet moves/is exiled to
-- different coordinates -- unlike Merlin's Planet.id, which is a literal
-- "x:y:z" string and so has to mint a new row on every move.
CREATE TABLE IF NOT EXISTS planet (
    id          SERIAL PRIMARY KEY,
    round_id    INTEGER NOT NULL REFERENCES round(id) ON DELETE CASCADE,
    external_id TEXT NOT NULL,               -- the dump's own planet id token
    UNIQUE (round_id, external_id)
);

-- planet_listing.txt: id x y z "planet name" "ruler name" race size score value xp "special"
CREATE TABLE IF NOT EXISTS planet_stat (
    id          SERIAL PRIMARY KEY,
    tick_id     INTEGER NOT NULL REFERENCES tick(id) ON DELETE CASCADE,
    planet_id   INTEGER NOT NULL REFERENCES planet(id) ON DELETE CASCADE,
    galaxy_id   INTEGER REFERENCES galaxy(id) ON DELETE SET NULL,
    x           INTEGER NOT NULL,
    y           INTEGER NOT NULL,
    z           INTEGER NOT NULL,
    planet_name TEXT NOT NULL,
    ruler_name  TEXT NOT NULL,
    race        TEXT NOT NULL,
    size        INTEGER NOT NULL DEFAULT 0,
    score       INTEGER NOT NULL DEFAULT 0,
    value       INTEGER NOT NULL DEFAULT 0,
    xp          INTEGER NOT NULL DEFAULT 0,
    special     TEXT NOT NULL DEFAULT '',
    UNIQUE (tick_id, planet_id)
);

-- user_feed.txt: tick "item type" "item text" -- kept as free text; unlike
-- Merlin's excalibur.py, BTK does not regex-parse content to backfill
-- alliance/planet/galaxy foreign keys (see btk/dumps/parser.py) -- that's
-- a follow-up, not part of this scaffold.
--
-- The game's own feed is a "recent activity" ticker: the same event line
-- (keyed by its own tick_number, not the fetching tick) keeps reappearing
-- in user_feed.txt for as long as it stays in that window, so ingesting
-- every tick's feed verbatim reinserts the same event dozens to thousands
-- of times over a round. The unique constraint + ingest_feed's ON CONFLICT
-- DO NOTHING (btk/dumps/ingest.py) collapse those back down to one row per
-- distinct event.
CREATE TABLE IF NOT EXISTS feed (
    id          SERIAL PRIMARY KEY,
    round_id    INTEGER NOT NULL REFERENCES round(id) ON DELETE CASCADE,
    tick_number INTEGER NOT NULL,             -- the feed row's own tick column (not always == tick.number of the fetching tick)
    category    TEXT NOT NULL,
    text        TEXT NOT NULL,
    UNIQUE (round_id, tick_number, category, text)
);

-- api.pl?stats: round-scoped ship stats, ingested once per round (not per tick).
CREATE TABLE IF NOT EXISTS ship (
    id          SERIAL PRIMARY KEY,
    round_id    INTEGER NOT NULL REFERENCES round(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    class       TEXT NOT NULL,
    race        TEXT NOT NULL,
    type        TEXT NOT NULL,
    metal       INTEGER NOT NULL DEFAULT 0,
    crystal     INTEGER NOT NULL DEFAULT 0,
    eonium      INTEGER NOT NULL DEFAULT 0,
    total_cost  INTEGER NOT NULL DEFAULT 0,
    damage      INTEGER NOT NULL DEFAULT 0,
    armor       INTEGER NOT NULL DEFAULT 0,
    guns        INTEGER NOT NULL DEFAULT 0,
    initiative  INTEGER NOT NULL DEFAULT 0,
    empres      INTEGER NOT NULL DEFAULT 0,
    baseeta     INTEGER NOT NULL DEFAULT 0,
    target1     TEXT,
    target2     TEXT,
    target3     TEXT,
    UNIQUE (round_id, name)
);

-- Discord <-> game account linking, for the bot.
CREATE TABLE IF NOT EXISTS discord_link (
    id                  SERIAL PRIMARY KEY,
    discord_user_id     BIGINT NOT NULL UNIQUE,
    planet_id           INTEGER REFERENCES planet(id) ON DELETE SET NULL,
    linked_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Discord users allowed to use non-admin-only bot commands (see
-- btk/bot/access.py). Admins are config-only (BTK_DISCORD_ADMIN_IDS,
-- comma-separated Discord user IDs, not stored here) -- no runtime
-- promotion, so the bot can never be locked out of its own admin commands.
-- Anyone not an admin and not in this table is ignored entirely.
CREATE TABLE IF NOT EXISTS bot_member (
    discord_user_id BIGINT PRIMARY KEY,
    added_by        BIGINT NOT NULL,
    added_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Player-requested planet scans, posted as links to a shared Discord
-- "scans" channel (BTK_DISCORD_SCANS_CHANNEL_ID) so someone in-game can
-- scan and report back manually -- unlike Merlin, BTK doesn't ingest scan
-- results itself, so there's no automatic freshness/quota checking here,
-- just link generation and a request queue for !reqscan list/cancel.
CREATE TABLE IF NOT EXISTS scan_request (
    id              SERIAL PRIMARY KEY,
    round_id        INTEGER NOT NULL REFERENCES round(id) ON DELETE CASCADE,
    requested_by    BIGINT NOT NULL,       -- discord_user_id
    x               INTEGER NOT NULL,
    y               INTEGER NOT NULL,
    z               INTEGER NOT NULL,
    scan_type       TEXT NOT NULL,         -- P/D/U/N/J/A, see btk/bot/paconf.py SCAN_TYPES
    tick_number     INTEGER NOT NULL DEFAULT 0,
    active          BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_scan_request_active ON scan_request(round_id, active);
CREATE INDEX IF NOT EXISTS idx_planet_stat_tick ON planet_stat(tick_id);
CREATE INDEX IF NOT EXISTS idx_galaxy_stat_tick ON galaxy_stat(tick_id);
CREATE INDEX IF NOT EXISTS idx_alliance_stat_tick ON alliance_stat(tick_id);
CREATE INDEX IF NOT EXISTS idx_feed_round_tick ON feed(round_id, tick_number);
