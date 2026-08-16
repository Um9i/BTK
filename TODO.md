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

- **Rank-delta arrows.** Score deltas already render everywhere
  (`macros.delta`); rank itself never shows movement. Add a small ▲/▼ +N
  next to `Rank #371` on planet/alliance/galaxy detail pages and in the
  homepage movers table -- the rank history is already there (previous
  tick's `RANK() OVER` is one query away), this is a display gap, not a
  data gap.
- **Inline sparklines on the movers table.** `btk/api/charts.py`'s
  `sparkline()` already renders a mini trend for detail pages; the
  homepage's "Planet movers this tick" table only shows a single-tick
  delta. A tiny inline sparkline per row (last ~10 ticks) turns "gained
  9,591 this tick" into "gained 9,591 this tick, and has been climbing
  steadily for a week" at a glance.
- **Growth rate, not just delta.** A raw `+57,821` score delta reads
  differently on a planet that's been flat for days vs. one that's been
  climbing every tick. A small "avg/tick over last N ticks" figure next
  to the raw delta would separate one-off events (combat, a single big
  build) from sustained momentum.
- **Galaxy-cluster occupancy map.** A compact monospace grid -- one row
  per galaxy, one glyph per planet slot, colored/weighted by whether it's
  occupied and which alliance holds it -- would make "where is everyone"
  scannable in a way a sorted table can't. Fits the terminal aesthetic
  directly (think `htop`'s CPU grid, not a starmap).

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

## Why didn't Appocomaster get a nick guess?

Checked directly (`btk-nick-history lookup Appocomaster` + the live DB):
Appocomaster has an extremely long, consistent history -- present in
nearly every round since round 14, almost always in PATSA, almost always
in galaxy `1:1`. Round 118's planet at `1:1:6` is real, but:

1. It has **no confirmed round-118 alliance** in `planet_intel` at all --
   `alliance` is blank. Mechanisms 1 and 3 (recency match, ruler-collision
   disambiguation) both need a known round-118 alliance to compare
   against; with nothing to compare, neither can fire.
2. The ruler string `"Appocomaster"` is **ambiguous on its own** --
   the same real player renicked to `"Appoco"` for rounds 27-29 while
   still playing under the ruler name `"Appocomaster"`, so the ruler→nick
   index has two candidates (`Appocomaster`, `Appoco`) for this one ruler
   string, and there's no independent alliance signal available to break
   the tie.
3. It's an essentially untouched planet this round (score 4,440, size 0
   -- starting stats), so there's no galaxy-buddy corroboration path
   either: mechanism 4 only promotes nicks that were already a clean
   single-candidate match, and this one never got that far.

So it's a real, specific gap: a same-player rename collision plus zero
scouted intel on that particular planet leaves it silently in the "none"
bucket (260 of 414 planets), with no line printed in the report at all.

**Fixed** in `scripts/guess_nicks.py` (`_renick_merge`): when a ruler
name collides across nicks, check whether every candidate's usage window
is non-overlapping and close together (no two candidates ever used it in
the same round, and no gap wider than `MAX_RENICK_GAP` rounds between
consecutive windows) -- that pattern is what one real player's own
mid-career renick looks like, not two unrelated players coincidentally
picking an identical ruler string. When it holds, the candidates
collapse into one identity (canonicalized to the most recent nick) with
a combined alliance/galaxy history instead of an unresolvable tie.
Re-running against round 118: Appocomaster now resolves to **high**
confidence via galaxy-buddy corroboration (96 shared historical rounds
in galaxy 1:1 with an already-confirmed PATSA anchor) -- exactly the
signal a human would trust instantly. Verified purely additive against
the previous report: 82→84 high confidence, 72→73 medium, 260→257 none,
with every previously-resolved nick unchanged (only their corroboration
notes grew, since previously-invisible planets now count toward
clustering).
