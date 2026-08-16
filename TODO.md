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

- ~~**`/` focuses search.**~~ **Done.** Press `/` anywhere (not already
  typing in a field, no modifier held) to focus and select the header
  search box. A faint `/` hint (reusing `.tag`) sits in the box until
  focused, then hides; hidden outright on mobile where it's meaningless.
- ~~**Live filtering on `/web/planets`.**~~ **Done.** Split the results
  table/pagination out into `_planets_results.html`; the route serves
  that fragment alone when the request carries an `X-BTK-Partial: 1`
  header (only the page's own fetch call sends it), full page otherwise.
  Debounced (300ms) input listener re-fetches and swaps `#planets-results`
  via `history.replaceState` instead of a real navigation. The plain
  `<form>` still works untouched with JS off -- same GET, same URL.
- ~~**Command-style search shortcuts.**~~ **Done.** `@name` redirects to
  the matching alliance (exact/prefix/substring tiers, same as the bot's
  `!lookup`); `#123` redirects to whoever currently holds that score
  rank. Both work from the header search (plain navigation) and the
  on-page `/web/planets` search -- the latter's live-filter JS treats
  `@`/`#` as an exemption, letting the form submit for real instead of
  a per-keystroke fetch, since a redirect mid-typing or fetch()
  silently following one would both be wrong.
- ~~**Recent-searches memory.**~~ **Done.** Last 6 header-search queries
  (deduped, most recent first) saved to `localStorage` on submit, shown
  as a dropdown on focus -- pure client-side, no backend change. Wrapped
  the search form in `.header-search-wrap` (`position: relative`) so the
  dropdown anchors under the box instead of the whole header row.

## Personalization ("You" panel and beyond)

- ~~**Your alliance, expanded inline.**~~ **Done.** Added an "Intel"
  line to the You panel -- `<scouted>/<members> scouted`, one extra
  `count(*)` on `_you_panel`'s existing alliance lookup, linking straight
  to the alliance page for the full breakdown.
- ~~**"Your feed."**~~ **Done.** Added a text-matched slice (ruler name
  or planet name) inside the You panel itself -- the feed table has no
  structured planet reference (known gap, see db/schema.sql), so an
  `ILIKE` match on the viewer's own names is the only way to slice it.
  Only renders when there's at least one match, right under the You
  panel's stats.
- ~~**Watchlist.**~~ **Done.** New `watchlist` table (polymorphic
  `target_type`/`target_id`, no FK -- same tradeoff schema.sql already
  makes elsewhere), a single `POST /web/watch` toggle route, a ★/☆
  button on planet/alliance detail pages, and a homepage "Watching"
  table (rank/score/change, ranked against the *full* tick like every
  other rank on the site, plus an inline unwatch) for logged-in members.

## Intel tooling

- ~~**Web-based intel editing.**~~ **Done.** `POST /web/planets/{id}/intel`
  reuses the exact same `planet_intel` upsert the bot's `!intel` cog
  runs. Replaced the old read-only `.intel-box` with a form that doubles
  as the view (current values pre-filled) -- unlike the bot's key=value
  partial update, the web form always submits every field, so a blank
  field here clears that column rather than leaving it untouched.
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
