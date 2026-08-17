# scripts/

One-off and semi-automated maintenance scripts. `init_db.py` is the only one
run as part of normal setup (see the repo root `CLAUDE.md`); everything else
here supports the alliance-membership-inference workflow described in full
in `docs/alliance-membership-inference.md` — read that doc for the *why* and
the failure modes. This file is just the *how to run them, in what order*.

All are installed as `uv run <name>` entry points (see `pyproject.toml`
`[project.scripts]`); run any of them with no args (or `--help`) to see its
own usage docstring, which is kept up to date in the script itself.

## Alliance roster inference pipeline

Run roughly in this order — each step shrinks the work the next one has to
do by excluding planets it already confirmed:

1. **`uv run btk-find-joiners`** (`find_joiners.py`) — free, no solver.
   Names alliance joiners from the counted-score gap (`total_score -
   counted_score`) by direct lookup. Also tells you which tick spans had no
   visible churn, which every later step benefits from knowing.
   ```
   uv run btk-find-joiners                       # latest round, read-only
   uv run btk-find-joiners --round 118 --alliance TiT
   uv run btk-find-joiners --insert --updated-by <discord_user_id>
   ```

2. **`uv run btk-verify-rosters`** (`verify_rosters.py`) — free, no solver.
   Proves every 1- and 2-member alliance's roster is a direct index lookup
   or pair scan on the alliance's own aggregate totals.
   ```
   uv run btk-verify-rosters                    # latest round, read-only
   uv run btk-verify-rosters --round 118
   uv run btk-verify-rosters --insert --updated-by <discord_user_id>
   ```

3. **`uv run btk-solve-roster <alliance> <lo> <hi>`** (`solve_roster.py`) —
   for everything bigger: exact subset-sum via CP-SAT (size and xp exact,
   value bounded by the alliance fund). Always run with `--extend` once you
   trust a result, to check it holds outside the window it was solved on.
   ```
   uv run btk-solve-roster "TiT" 136 213                      # latest round
   uv run btk-solve-roster "PussycatZ" 95 213 --round 118
   uv run btk-solve-roster "Chocolate Starfish" 69 197 \
       --force-in z9jbog80 --insert --updated-by <discord_user_id>
   uv run btk-solve-roster "Tal Shiar" 186 213 --extend       # check outside the window too
   ```
   Key flags: `--force-in EXTERNAL_ID` (pin a known member/joiner into the
   solve), `--no-exclude-claimed` (don't drop planets already confirmed to a
   different alliance — normally you want the default exclusion),
   `--no-check-idle` (skip auto-detecting fully-idle members that make one
   roster slot ambiguous), `--extend` (check the confirmed roster against
   every other tick in the round and try to name single-planet joins/leaves
   at any tick it stops reconciling).

## Nickname inference (separate, downstream pipeline)

Guesses a player's persistent `nick` (not the same as `ruler`, which is
repicked every round) for planets whose *alliance* is already confirmed by
the pipeline above, using cross-round history from
`history.planetarion.com`.

4. **`uv run btk-fetch-history [rounds...]`** (`fetch_history.py`) —
   downloads per-round ground truth into `docs/data/`.
   ```
   uv run btk-fetch-history 114 115 116 117
   uv run btk-fetch-history --from 14 --to 117 --keep-going
   uv run btk-fetch-history --all --keep-going   # every round the site lists
   ```

5. **`uv run btk-nick-history build`** (`nick_history.py`) — loads all
   fetched `docs/data/round<N>-alliance-truth.csv` files into
   `docs/data/nick_history.sqlite`, indexed by `nick` and `ruler`.
   ```
   uv run btk-nick-history build                    # (re)build the database
   uv run btk-nick-history lookup elviz              # every appearance of a nick
   uv run btk-nick-history lookup Cashy              # matches ruler too, not just nick
   uv run btk-nick-history top-rounds                # nicks seen in the most rounds
   uv run btk-nick-history top-alliances             # nicks with the most distinct alliances
   uv run btk-nick-history ruler-collisions          # one ruler name, multiple nicks
   ```

6. **`uv run btk-guess-nicks <round118_planets.csv>`** (`guess_nicks.py`) —
   given a CSV of `external_id,ruler,planet,race,size,score,x,y,alliance`
   (alliance from step 1-3's confirmed rosters where known; `x,y` optional),
   guesses nicks via recency/clustering/disambiguation/galaxy-buddy
   matching against `nick_history.sqlite`. Prints results by confidence
   tier; nothing is written to `planet_intel` automatically — review and
   insert by hand.

## Other

- **`uv run btk-initdb`** (`init_db.py`) — applies `db/schema.sql`. Part of
  normal local setup, not the inference pipeline; see the repo root
  `CLAUDE.md`.
