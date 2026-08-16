# Frontend TODO

Ideas for making the web UI sharper, gathered while looking at the live
site. Grounded in what BTK already is -- a plain-text console for a small
group of alliance members who want dense, fast, accurate data, not a
consumer dashboard -- so "slick" here means *precise and fast*, not
flashy. Nothing here should introduce cards, border-radius, or color
beyond the existing good/bad semantic pair.

Roughly ordered by impact within each section, not a strict priority
order across the whole file.

## Data & visualization

- ~~**Rank-delta arrows.**~~ **Done.** Added `macros.rank_change()`
  (inverted sign convention from `delta()` -- a falling rank *number* is
  the improvement) next to Rank on planet/alliance/galaxy detail pages
  (both the vitals line and the per-tick log), the homepage's alliance/
  galaxy mover cards, and the "You" panel's own rank and alliance rank.
  Galaxy and planet rank aren't stored per tick (unlike alliance_stat,
  which has a native `rank` column) -- computed via a `RANK() OVER`
  window scoped to the round, joined into the existing history query
  rather than an extra round-trip.
- ~~**Inline sparklines on the movers table.**~~ **Done.** Added a
  `Trend` column to the homepage's planet movers table using the
  existing `sparkline()` helper -- one query fetching the last 10 ticks
  for all 5 rows' planet ids at once (`ROW_NUMBER() OVER (PARTITION BY
  planet_id ...)`), not 5 round-trips. `col-secondary` like the other
  decorative columns, so it drops on mobile rather than crowding a
  narrow table.
- ~~**Growth rate, not just delta.**~~ **Done.** Added `macros.avg_rate()`
  next to the Score vital on planet/alliance/galaxy detail pages -- mean
  of the last 10 ticks' score deltas, computed in Python from the
  history rows already fetched for the sparkline (no extra query).
  Deliberately unstyled/no color (`.rate`, `--fg-faint`) so it reads as
  context next to the raw delta, not a second result competing with it.
- ~~**Galaxy-cluster occupancy map.**~~ **Done.** New `/web/map` page
  (linked from `/web/galaxies`): one row per galaxy grouped by cluster
  (shared x), one glyph per planet slot. Deliberately weight/glyph-coded
  rather than color-coded per alliance, since a full color palette would
  break the "no color beyond good/bad" rule -- `·` empty, dim `■`
  occupied (alliance unknown or logged out), full-strength `■` alliance
  known, green `■` your own alliance (resolved via the same `discord_link`
  the You panel uses). Anonymous visitors only ever see occupied/empty,
  same visibility rule as every other alliance-tagging surface on the
  site.

## Search & navigation

- **`/` focuses search.** Classic terminal/site convention -- press `/`
  anywhere on the site and the header search box gets focus and selects
  its contents. Cheap, and it's the single highest-value keybinding for a
  site whose primary navigation *is* the search box now.
- **Live filtering on `/web/planets`.** Right now every query is a full
  page reload. A small debounced fetch-and-replace-tbody script (no
  framework needed) would make repeated searches ("who's near me,"
  "check three names in a row") feel instant. Keep the current
  server-rendered form as the no-JS fallback.
- **Command-style search shortcuts.** The header search already
  disambiguates `x:y:z` / `x:y` / free text. Extend the grammar: `@name`
  jumps straight to an alliance page, `#123` jumps to a rank position.
  Keeps the single-input "prompt" metaphor instead of adding more UI.
- **Recent-searches memory.** Store the last few header-search queries in
  `localStorage` and show them as a dropdown on focus -- no backend
  change, just a few lines of JS, and it's the kind of thing that gets
  used constantly by anyone actively scouting.

## Personalization ("You" panel and beyond)

- **Your alliance, expanded inline.** The "You" panel already resolves
  the linked planet's alliance and rank (`_you_panel` in `web.py`). A
  compact intel-coverage line right there ("PATSA: 6/9 members scouted")
  would save the click to the alliance page for the single most common
  follow-up question.
- **"Your feed."** `recent_feed` already parses the game's text feed
  round-wide. A personalized slice -- entries mentioning your own ruler
  name, planet name, or galaxy coords -- turns a firehose of 400 planets'
  worth of news into "things that happened to me," without any new
  ingest work.
- **Watchlist.** Let a logged-in member star specific planets or
  alliances (one small table, `discord_user_id, target_type, target_id`)
  and surface a "Watching" section on the homepage alongside "You." Useful
  for tracking a rival or a recruit before they're worth a full intel
  entry.

## Intel tooling

- **Web-based intel editing.** Right now `!intel` is bot-only. A small
  form on the planet detail page (visible to logged-in users, posting to
  a new `POST /web/planets/{id}/intel` route reusing the same
  `planet_intel` upsert `intel.py`'s bot cog already does) would let
  someone update a nick/comment without leaving the browser tab they're
  already reading.
- **Distinguish scouted intel from guessed nicks visually.** Right now a
  guessed nick's provenance is buried in the free-text Comment column
  ("Nick guessed 2026-08-16 via scripts/guess_nicks.py: ..."). A small
  tag next to the Nick value itself -- `scouted` vs. `guessed` -- makes
  the confidence level visible without reading a paragraph. This also
  surfaces cases like Appocomaster (see below) where nothing at all is
  guessed, silently, with no visible reason why.
- **"Needs intel" queue.** A view (or a homepage section) listing
  round-118 planets with no `alliance`/`nick` tagged at all, sorted by
  score, gives logged-in members a ready-made worklist instead of
  guessing who's worth scouting next.

## Feel / responsiveness

- **Live tick landing, no refresh.** The countdown clock is already
  client-side; when it hits zero there's no signal that new data has
  actually landed until a manual reload. A short poll of `/health` or a
  cheap "latest tick number" endpoint around T-0, with a flash/pulse on
  the LIVE pill when the number advances, would close that gap.
- **Sortable list columns.** `/web/planets`, `/web/alliances`,
  `/web/galaxies` are all fixed-order (`ORDER BY score DESC`, etc). A
  click-to-sort on column headers (query-param driven, so it stays a
  plain GET link, no client state) would help when the interesting
  question isn't "who's #1" but "who's biggest by value, not score."

## Housekeeping

- **Comment column placement.** On `/web/planets` search results, Comment
  is the last (rightmost) column, so it's the first thing pushed off
  past the fold on a wide roster -- and it's usually the column with the
  most actually-useful text (nick-guess provenance, scouting notes).
  Worth checking whether it reads better earlier, or whether the
  provenance tag above (separate from free-text comment) makes this
  moot.
- **`show_comment_column` collapse edge case.** The single-match "shown
  as a subtitle instead of a column" behavior (mirrors
  `alliance_detail.html`) is easy to miss when skimming a wide result
  set — worth a visual gut-check once a few more search patterns get
  used in practice.

---
