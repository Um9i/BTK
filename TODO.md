# Frontend TODO

Ideas for making the web UI sharper, gathered while looking at the live
site. Grounded in what BTK already is -- a plain-text console for a small
group of alliance members who want dense, fast, accurate data, not a
consumer dashboard -- so "slick" here means *precise and fast*, not
flashy. Nothing here should introduce cards, border-radius, or color
beyond the existing good/bad semantic pair.

Three parts: the **open visual pass** immediately below, then the
**completed 2026-08-17 audit**, then the **completed log** of everything
shipped before that.

---

# Open — visual pass (screenshot review, 2026-08-17)

Reviewed full-page screenshots of all eight pages (map, alliance detail,
galaxy detail, planet detail, needs-intel, alliances, galaxies, planets) at
round 118 / tick 236, after the structural audit below had shipped. The
structure is right now; what's left is that a few elements *read* as
unfinished — a chart that looks like a rendering artifact, a column of
duplicated text, and a page using a third of its width. Same constraints as
always: no cards, no radius, no third colour.

Ordered by how much each one hurts on first look.

## P0 — the three that read as broken

- [x] **Fix the vitals sparklines — they read as smudges, not data.** Three
      fixes, matching the three problems: (1) dropped the filled polygon
      entirely (`charts.py`'s `sparkline()`) rather than just lowering its
      opacity — at this size a translucent area under the line was reading as
      a grey smear, not signal; (2) `preserveAspectRatio="xMinYMid meet"` on
      the `<svg>` instead of the default `xMidYMid`, which was centering the
      drawn line and padding it away from the label above whenever the
      rendered box's aspect ratio didn't match the viewBox's; (3) wrapped
      every trend in a `<div class="trend-slot">` (`planet_detail.html`,
      `galaxy_detail.html`, `alliance_detail.html`) with a fixed
      `height: 28px` in CSS, plus a new `.vital { min-height: 3.6rem }` so
      *every* vital in the row — including ones with no trend at all, like
      alliance Rank/Members/Points — reserves identical vertical space. That
      last part turned out to matter beyond the "4 of 7 have a trend" case
      the audit called out: `sparkline()` also silently returns empty markup
      on <2 ticks of history, so without a slot with a floor height, a
      fresh-round vital would shrink and misalign the row even where a trend
      normally renders. → `btk/api/charts.py`, `style.css` (`.vital`,
      `.vital-trend .trend-slot`, `.vital-trend .sparkline`),
      `planet_detail.html`, `galaxy_detail.html`, `alliance_detail.html`
- [x] **Collapse the near-duplicate Comment column on the alliance roster.**
      Went with the tooltip option: generalized `macros.alliance_intel()` into
      `macros.intel_tooltip(anchor, p, show_nick, show_comment_column)` (an
      explicit anchor arg instead of always `p.alliance`, since this roster has
      no Alliance column to hang it off), wired it onto the Ruler cell, and
      dropped the dedicated Nick and Comment `<th>`/`<td>` pairs outright —
      matching the pattern the planets list already used for the same data.
      `_comment_display()`'s uniform-comment subtitle collapse (`web.py:152`)
      is untouched and still fires when every row shares the exact same note;
      the tooltip only carries the differing case now. → `_macros.html`
      (`intel_tooltip`, `alliance_intel`), `alliance_detail.html`, `web.py`
      (`trailing_cols` no longer counts Nick/Comment as columns), also
      removed the now-dead `.truncate-wide` CSS rule.
- [x] **Lay the cluster map out in columns.** Wrapped the `{% for cluster %}`
      loop in a `.map-clusters` container (`map.html`), each cluster now its
      own `.map-cluster` block with the `<h2>` inside it, laid out with
      `display: grid; grid-template-columns: repeat(auto-fill, minmax(22rem,
      1fr))` — auto-fill/minmax rather than a fixed 2–3 count so it also
      collapses cleanly to one column on mobile. → `map.html`, `style.css`
      (`.map-clusters`, `.map-cluster`)

  All three verified in a real browser (Playwright/Chromium, screenshots at
  desktop/mobile/dark-mode): the map now renders 3 columns at 1400px and 1 at
  420px; planet/galaxy vitals sparklines are thin flush-left strokes with no
  fill, uniform baseline across the row, confirmed on both light and dark;
  the alliance roster's Ruler column shows a dotted-underline tooltip
  (hover-tested) with the full comment when comments differ across rows, and
  still collapses to the single uniform-comment subtitle when they're
  byte-identical — tested both cases against temporary DB rows (removed after
  screenshotting; this local DB's round 116 dataset had no existing
  `planet_intel` rows to test against). `ruff check` and the full test suite
  (50 passed) both clean.

## P1 — density and hierarchy, once P0 lands

- [x] **Give the vitals row a primary metric.** New `.vital-primary` modifier
      (`style.css`) bumps just the Score value to 1.65rem/weight 600 while the
      shared `.vital .value` baseline dropped from 1.2rem to 1.05rem, so every
      other vital — Rank, Members, Size, Points, Total score, Total value —
      reads as a visible step down. Applied to the Score `<div class="vital
      vital-trend">` on all three detail pages by adding `vital-primary` to its
      class list — no markup restructuring needed. →
      `style.css` (`.vital .value`, `.vital-primary`), `planet_detail.html`,
      `galaxy_detail.html`, `alliance_detail.html`
- [x] **Strip columns that never vary from the log tables.** Added a
      full-history distinct-value check at the route level (`SELECT DISTINCT
      <col> ... LIMIT 2`, not scoped to the current page — same principle as
      `_planet_flags`' full-set check) for galaxy Name and alliance
      Rank/Members. When a column comes back constant across the entire round
      (not just what's visible on this log page), the column is dropped and
      the value stated once in a subtitle above the table instead; when it
      isn't — confirmed against a galaxy that was actually renamed
      mid-round — the column stays, so this never hides a real change that
      happens to fall outside the current page. → `web.py` (`galaxy_detail`,
      `alliance_detail`), `galaxy_detail.html`, `alliance_detail.html`
- [x] **Separate Rank and Coords on `/web/needs-intel`.** New `.rank-col`
      (wider `padding-right: 2.5rem` instead of the table's usual 1.25rem) and
      `.rank-dim` (`color: var(--fg-dim)`, skipped on top-3 rows which already
      read as heavier via the existing `.rank-top`) — the worklist's sort
      order now visibly recedes behind the coordinate a scout actually needs,
      instead of blurring into one digit string. → `style.css` (`.rank-dim`,
      `.rank-col`), `needs_intel.html`

  All three verified in a real browser (Playwright/Chromium) against the
  local DB: Score is now the clear visual anchor on all three detail pages;
  a galaxy that kept one name for its whole history collapsed to a "Name: ..."
  subtitle with the column gone, while a galaxy that was renamed mid-round
  (real data: "Cant Be Arsed" → "A Galaxy Far Far Away") correctly kept its
  Name column; a single-member alliance with constant Rank/Members collapsed
  both into one subtitle, while a growing 46-member alliance (varying rank,
  varying members over its full history despite both happening to be flat on
  the visible page) correctly kept both columns; `/web/needs-intel`'s Rank
  column now reads as clearly secondary to Coords. `ruff check` and the full
  test suite (50 passed) both clean.

---

# Completed — 2026-08-17 audit

Audit of `btk/api/templates/`, `btk/api/static/style.css` and
`btk/api/routers/web.py` at tick 233 / round 118. Ordered by impact, not by
effort. The design constraints in the intro above still hold: no cards, no
radius, no third colour.

The one deliberate new move: **promote the query grammar to the front door.**
BTK's real power feature is that `x:y:z`, `x:y`, `@alliance`, `#rank` and a
free-text name all go through one input — and today that grammar is hidden in a
90-character placeholder that vanishes the moment you type. Making the landing
page's search a full-width console command line, with the grammar shown
*beneath* it as real clickable examples, is the signature element. Spend the
boldness there and keep everything around it quiet.

## The finding that reframes the search-bar ask

Moving search out of the header is not just a layout preference — **the site
currently has no navigation at all.**

`style.css:108` says it outright: the header search was added *"in place of a
page-nav link list"*. So the only links to Planets / Galaxies / Alliances are the
three `.stat-card`s on the homepage (`index.html:118-131`), which sit below the
fold under the You panel and the watchlist. On `/web/galaxies/12`,
`/web/planets/99`, `/web/alliances/4`, `/login` or `/web/needs-intel` there is
**no way to reach another section** except clicking the wordmark back to home
first.

So P0 does both halves at once: search moves onto the page, and the space it
frees in the header becomes the real nav that was never there.

## P0 — structural (do these first, in this order)

- [x] **Put real navigation in the header.** Replaced the header search slot
      with `Planets · Galaxies · Alliances · Map` (+ `Needs intel` when logged
      in) as a new `.site-nav`, marking the current section via
      `request.url.path` against the existing `.nav-link.active` style.
      → `base.html`, `style.css`
- [x] **Move search onto the page as a hero command line.** Full-width command
      line under the round banner on `/` (`.search-form.hero`, 1.1rem input),
      reusing the existing `.search-form` style already on `/web/planets` rather
      than inventing a second search style. The `/` shortcut now finds whichever
      page's `#site-search` exists (null-guarded, since not every page has one).
      → `base.html`, `index.html`
- [x] **Replace the placeholder essay with real example chips.** Both the hero
      search and `/web/planets`' search now show a short placeholder plus a
      `Try:` row of real links (`1:2:3`, `5:9`, `@Imperium`, `#1`) that navigate
      directly — no JS needed. Folded the old "Shortcuts:" subtitle into it.
      → `index.html`, `planets_list.html`
- [x] **Lay the landing page out as two columns ≥1024px.** New `.dashboard-grid`
      (`2fr 1fr` at ≥1024px, single column below it): main column has You,
      Watching, Movers, Planet movers; rail has Race distribution and Recent
      activity. → `index.html`, `style.css`
- [x] **Move the counts out of the body and into the header/banner.** Replaced
      the `.stat-grid` cards with a `.counts-line` inline in the console banner
      (`403 planets · 68 galaxies · 24 alliances`, still linked). Removed the now
      -dead `.stat-grid`/`.stat-card` CSS. → `index.html`, `style.css`

  Verified: all four list/map pages return 200 with balanced markup and the new
  nav present; homepage's dashboard-grid/hero-search/counts-line all render;
  the `@name`/`#rank` search shortcuts still redirect correctly; `ruff check`
  clean. *Not yet checked in an actual browser* — no headless Chrome/chrome-
  devtools MCP was available in this environment, so the layout (grid
  breakpoint, chip wrapping, nav spacing on mobile) should get a real visual
  pass before merging.

## P1 — pagination & scrolling (the second explicit ask)

- [x] **Paginate `/web/galaxies`.** Real `COUNT(*)` + `LIMIT`/`OFFSET`, wrapped
      the rank window function in a CTE so it can still be sorted/paged (had to
      re-point `GALAXY_SORT_COLUMNS` at the CTE's unqualified column names).
      → `web.py`, `galaxies_list.html`
- [x] **Paginate `/web/alliances`.** Same pattern. → `web.py`, `alliances_list.html`
- [x] **Paginate planet *search* results.** Free-text search now runs a real
      `COUNT(*)` and `LIMIT`/`OFFSET` instead of capping at 100 and reporting
      that cap as the total. Also fixed a bug this surfaced: the search branch's
      `ORDER BY score DESC` was hardcoded, so the now-visible sort headers would
      have looked clickable but done nothing — sort/dir are respected there too
      now (via the already-whitelisted `sort` key, since it maps directly to the
      CTE's own unqualified column names). → `web.py`
- [x] **Carry `q` through pagination *and* sort links.** Pagination links now
      append `&q=...`; `_sort_url_factory()` gained an optional `q` param so
      re-sorting search results doesn't drop the query either (this wasn't
      needed before since sort headers never appeared on search results).
      → `web.py`, `_planets_results.html`
- [x] **Sticky table headers.** `thead th { position: sticky; top: 0;
      background: var(--bg); z-index: 1; }` — site-wide, every table.
      → `style.css`
- [x] **Page the detail-page history logs.** All three detail pages
      (alliance/galaxy/planet) now take a `?page=` param on their Log table.
      Vitals/sparklines stay pinned to the true latest tick regardless of which
      log page is open (a separate small query, only fetched on pages 2+ — page
      1 reuses the log fetch). Each page fetches one extra row past the page
      boundary so the last visible row's delta is computed correctly instead of
      coming back blank. → `web.py`, `alliance_detail.html`, `galaxy_detail.html`,
      `planet_detail.html`
- [x] **Add a mid-range breakpoint.** New `.col-tertiary` class (Value/XP
      columns — least essential once Score is visible) hidden between 641px and
      1100px, on top of the existing ≤640px `.col-secondary` tier. Applied to
      `/web/planets`, `/web/galaxies`, and a galaxy's planet roster — the three
      widest tables (up to 12 columns). → `style.css`, `_planets_results.html`,
      `galaxies_list.html`, `galaxy_detail.html`

  Verified against the local DB (round with 66 galaxies, 19 alliances, a
  372-planet free-text match, and one alliance with 482 logged ticks): every
  paginated page returns 200 with correct `page X / Y — N` counts; sort+q
  survive both search pagination and re-sorting; the log page-boundary delta
  fix confirmed directly (last row of page 2 shows a real `+142,601`-style
  delta, not blank); vitals/rank verified byte-identical between log page 1 and
  page 2 on the same alliance. `ruff check` and the full test suite (50 passed)
  both clean.

  **Then actually checked in a real browser (Playwright/Chromium, installed
  this session) — and the naive sticky-header implementation didn't work.**
  `position: sticky; top: 0` on `thead th` measured as never sticking at all
  (`getBoundingClientRect().y` went fully negative on scroll, same as static
  positioning). Root cause: `.table-scroll`'s `overflow-x: auto` makes the UA
  promote `overflow-y` to a scroll-container value too (CSS Overflow spec's
  "the other axis becomes auto" rule — confirmed even `overflow-y: clip`
  still measured as capturing it in this Chromium build), which makes
  `.table-scroll` — not the viewport — the sticky positioning context. Since
  it was unbounded height, its own edge just scrolled away with the page, so
  nothing ever visibly stuck. **Fixed properly, not worked around**: gave
  `.table-scroll` `max-height: 75vh; overflow-y: auto`, turning it into a
  real bounded, independently-scrolling pane (a standard, reliable pattern
  for sticky-header + horizontally-scrollable tables) — verified with the
  same bounding-box check that it now sticks correctly. This is a bigger win
  than plain page-level sticky would have been: the whole page height for
  `/web/planets` dropped from ~2760px to ~900px (fits one viewport), with
  pagination immediately visible below the table instead of requiring a full
  page scroll first. Screenshotted across desktop/mobile/dark-mode/tablet
  widths to confirm — nav wraps correctly on mobile, dark mode is clean, the
  641–1100px `.col-tertiary` breakpoint correctly drops Value/XP with no
  horizontal scroll.

  **One more real bug this caught**: the homepage's `.dashboard-grid`
  (`align-items: start`, independent column heights) left a large blank gap
  under the shorter main column when the rail's 15-row Recent Activity feed
  ran much longer — not something the earlier structural-only checks (div
  balance, 200s) could have caught. Fixed by bounding
  `.dashboard-rail .feed-list` to `max-height: 26rem` with its own scroll,
  same pattern as the table fix.

## P2 — correctness & real bugs found during the audit

- [x] **Fix the stale-response race in live search.** `load()` now aborts any
      in-flight fetch before starting the next one (`AbortController`, swallows
      only `AbortError`) — verified by typing three keystrokes in quick
      succession and confirming the page settles on the *last* query, not an
      earlier one. → `planets_list.html`
- [x] **Stop columns appearing and disappearing between pages.** New
      `_planet_flags()` runs one `bool_or(...)` aggregate query over the *full*
      matching set (not the page) for the two paginated branches; the
      coord/galaxy branches keep the old `any()`-over-`rows` check since their
      `rows` already *is* the full set. Also simplified `PLANET_SORT_COLUMNS`
      from a (partly dead) `{key: "ps.col"}` dict to a plain whitelist set, and
      rewrote the default-browse query to go through `RANKED_PLANET_STAT_CTE`
      like the other branches instead of duplicating the join by hand — that's
      what made sharing `_planet_flags()` straightforward. → `web.py`
- [x] **Pause the tick poll on hidden tabs.** `poll()` returns early on
      `document.hidden`; a `visibilitychange` listener polls immediately on
      return instead of waiting out the rest of the 20s interval. → `index.html`
- [x] **Make the recent-searches dropdown keyboard-operable.** Rebuilt as a
      combobox/listbox pair — `role="combobox"` + `aria-expanded` +
      `aria-activedescendant` on the input, `role="listbox"`/`role="option"` on
      the list, Up/Down to move, Enter to select, Escape to close. Focus stays
      on the input throughout (activedescendant tracking, not real DOM focus
      moving into the list), so typing still works while navigating suggestions
      — the standard accessible-autocomplete shape. Verified end-to-end in a
      real browser: focus opens it (`aria-expanded` → `true`), ArrowDown sets
      `aria-activedescendant` and the matching `.active` highlight, Enter
      navigates to the selected query. → `base.html`, `style.css`
- [x] **Add `aria-sort` to sortable headers**, plus `aria-pressed` on the theme
      toggle (reflecting dark/light state, updated alongside the existing
      `aria-label` swap). → `_macros.html`, `base.html`
- [x] **Replace `title=` tooltips for intel.** Rebuilt `alliance_intel()` as a
      CSS-only tooltip: a `tabindex="0"` trigger + `aria-describedby` pointing
      at a `role="tooltip"` bubble, shown via `:hover` *and* `:focus-within`
      (`title=` only ever covered the first). Verified in a real browser that
      `Tab`-focusing the alliance name alone makes the tooltip visible — the
      exact gap this was meant to close. → `_macros.html`, `style.css`

  All six verified in a real browser (Playwright/Chromium), not just read —
  zero console errors across every page, `ruff check` and the full test suite
  (50 passed) both clean.

## P3 — polish, performance, distribution

- [x] **Self-host the two fonts.** Both Inter and JetBrains Mono turned out to be
      variable fonts (`fvar` axis `wght` 100–900, confirmed with `fonttools`) — one
      ~30-50KB `latin`-subset woff2 file per family covers every weight the site
      uses (400-700), so this is 2 files (~80KB total) at `/static/fonts/`, not 7.
      `@font-face` uses a weight *range* (`font-weight: 100 900`) so the browser
      still picks the right instance. Removed both Google Fonts `<link>`s and the
      two preconnects. Verified in a real browser: zero requests to
      fonts.googleapis.com/gstatic.com, computed `font-family` on real elements
      resolves to the self-hosted names. → `style.css`, `base.html`,
      `static/fonts/`
- [x] **Extract the inline JS to `/static/app.js`.** All body-level scripts from
      `base.html`, `index.html`, and `planets_list.html` moved into one file,
      loaded once with `<script defer>` from `base.html` and reused by every page
      — each piece guards on the DOM elements it needs (most pages have most of
      them absent) so one file safely covers the whole site. The one exception,
      left inline on purpose: the pre-paint theme-init snippet in `<head>`, which
      has to block before first paint to avoid a flash of the wrong theme — an
      external deferred script can't do that. `index.html`'s two Jinja-templated
      values (`round.number`/`tick.number`) now ride in `data-round`/`data-tick`
      attributes on `#status-pill` instead, since app.js has no template access.
      Verified end-to-end in a browser: countdown ticks, `/` focuses search, live
      planet search still works, zero console errors on any page. → `static/app.js`
      (new), `base.html`, `index.html`, `planets_list.html`
- [x] **Add a favicon.** Small theme-aware SVG (`>` prompt glyph, matches the
      search box's own prompt character, switches fill via
      `prefers-color-scheme` inside the SVG itself) at `/static/favicon.svg`,
      linked from `<head>`. Also added a `/favicon.ico` → `/static/favicon.svg`
      redirect route for the browsers/crawlers that still probe the conventional
      path regardless of the `<link>` tag. → `static/favicon.svg` (new),
      `base.html`, `app.py`
- [x] **Add `<meta name="description">` and Open Graph tags.** `base.html` gained
      a `{% block description %}` (generic default) plus `og:site_name`,
      `og:type`, `og:title` (`self.title()`), and `og:description`
      (`self.description()`) — every extending template can override just the
      description block. Overridden with real per-page content on the homepage
      (round/tick/counts), `/web/planets`, `/web/galaxies`, `/web/alliances`
      (round context), and all three detail pages (planet rank/score, alliance
      rank/members/score, galaxy rank/score) — verified live against the running
      DB that these render with real data, not placeholders. → `base.html`,
      `index.html`, `planets_list.html`, `galaxies_list.html`,
      `alliances_list.html`, `planet_detail.html`, `alliance_detail.html`,
      `galaxy_detail.html`
- [x] **Write real empty states.** The no-match search state now names the actual
      query grammar (coords, galaxy, ruler/planet/nick, `@name`, `#123`) instead
      of a dead end. The no-round-data state now links to `/status/game` so a
      visitor can check whether the round is actually ticking. → `_planets_results.html`,
      `index.html`
- [x] **Reclaim the table-scroll hint's reserved space.** Collapsed to `height: 0`
      by default (was unconditionally reserved on every table on every page);
      expands only via `.visible`, with a transition since — after the P1 sticky-
      header fix gave `.table-scroll` a real bounded height — a table gaining this
      line now only shifts its own few rows, not the whole page, so the "reserve
      forever to prevent a page jump" tradeoff that motivated the original design
      no longer applies at the same severity. → `style.css`
- [x] **Vary `--content-max` by content type — turned out already done.** Checked
      `.feed-list` (`max-width: 44rem`) and `.race-dist` (`max-width: 30rem`):
      both already have their own intentional caps independent of the global
      92rem token, and P0's `.dashboard-grid` already narrows the rail to a
      fraction of that anyway (verified: 480px/704px actual widths at a 900px
      viewport, not stretched). The global token still needs to stay wide for the
      12-column roster tables it was sized for — varying it per-page would have
      been the wrong lever; the per-component caps that already existed were the
      right one.
- [x] **Give `/login` the console treatment.** Wrapped the form in a new
      `.auth-console` (bounded to 26rem, 2px bottom rule) with an "Authenticate"
      eyebrow above the `<h1>` — the same bordered-banner weight as the
      homepage's `.console`, instead of a bare form sitting at the top of an
      otherwise-empty page. Screenshotted in both themes. → `login.html`,
      `style.css`

  All eight verified in a real browser — zero console errors, fonts confirmed
  self-hosted with no external requests, meta/OG tags confirmed rendering real
  data, `ruff check`/`node --check`/full test suite (50 passed) all clean.

## Suggested first slice

P0 in order, as one branch: header nav → hero search → example chips →
two-column grid → counts inline. Those five are interdependent (the nav depends
on the search moving, the grid depends on the counts moving) and together they
are the whole of the user-visible complaint. P1's sticky headers is a cheap,
high-value add-on to the same branch.

---

# Done

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
- ~~**Distinguish scouted intel from guessed nicks visually.**~~ **Done.**
  Added `macros.nick_provenance()`: a quiet `guessed` tag (reusing
  `.tag.quiet`) whenever a nick's comment carries the
  `guess_nicks.py` marker every guess insert writes by convention.
  Scouted intel (the default/expected case) stays unmarked. Wired into
  the planet search table, the alliance roster table, and the planet
  intel form's Nick label. Caught and fixed a real bug along the way:
  the new intel-editing form was rendering a literal "None" into text
  inputs whenever a field was NULL (`{{ x if intel else '' }}` doesn't
  guard `intel.x` itself being None) -- fixed to `{{ x or '' if intel
  else '' }}` on all four text fields.
- ~~**"Needs intel" queue.**~~ **Done.** New `/web/needs-intel` page,
  linked from `/web/planets` for logged-in users -- every planet with
  nothing at all tagged in `planet_intel` (no row, or a row that's
  present but every field blank, same completeness check
  `planet_detail.html` already uses), biggest first, paginated like the
  main planet list. Rank is computed against the *full* tick
  (`RANKED_PLANET_STAT_CTE`) then filtered, so it means the same thing
  here as everywhere else on the site, not "rank among the unscouted."

## Feel / responsiveness

- ~~**Live tick landing, no refresh.**~~ **Done.** New public
  `GET /web/tick-status` (as open as `/health`/`/status/game` -- it's
  operational freshness, not gated game data), polled every 20s from the
  homepage. When the tick number advances past what was server-rendered,
  a "new tick — reload" link appears and the LIVE pill gets a one-off
  4-iteration pulse (not the continuous "live" one), skipped under
  `prefers-reduced-motion`. Verified with a real Playwright browser
  session: loaded the page, inserted a new tick mid-session, watched the
  hint actually appear.
- ~~**Sortable list columns.**~~ **Done.** Click-to-sort headers
  (`macros.sort_th`) on `/web/planets` (default browse only, not search
  results, which already have their own implied order), `/web/alliances`,
  `/web/galaxies` -- plain `?sort=field&dir=asc|desc` GET links against a
  whitelisted column map per page (no raw user input in the SQL), no
  client state. Caught and fixed a real double-escaping bug along the
  way: arrow HTML entities written inside a `{{ }}` expression render as
  literal `&amp;#9660;` text (Jinja auto-escapes expression output) --
  fixed by using literal Unicode arrows instead.

## Housekeeping

- ~~**Comment column placement.**~~ **Done.** Confirmed with a real
  1280px screenshot before touching anything: Comment was the 15th
  column, cut to an unreadable sliver even on a fairly wide desktop
  viewport. Moved it right after Nick (still `col-secondary`, still
  hidden on mobile) -- reads as one intel block (Alliance, Nick,
  Comment) ahead of the numeric game-stat columns.
- ~~**`show_comment_column` collapse edge case.**~~ **Done.** This was a
  real bug, not just an edge case: it collapsed to a page-level subtitle
  whenever there was only one *distinct* comment value, even if just one
  row out of fifty had it -- hiding which row the note was actually
  about. Added `_comment_display()`, shared by `/web/planets` and
  `alliance_detail.html`'s roster (same bug, same fix): only collapses
  when literally every row shares the exact same comment; otherwise
  shows the column, however few rows populate it.

---
