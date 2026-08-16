# Inferring alliance membership from aggregate stats

## The gap this fills

As documented in `db/schema.sql` and `CLAUDE.md`, the game's dumps never
give a per-planet alliance membership list — `alliance_listing.txt` only
reports each alliance's *aggregate* totals per tick (`size`, `members`,
`counted_score`, `points`, `total_score`, `total_value`). The only place
BTK records "this planet is in that alliance" is `planet_intel.alliance`,
populated exclusively by manual player scouting (`!intel` in Discord, or
the web UI). For most of a round, most alliances have no scouted roster
at all.

This doc describes a technique developed across three investigative
sessions (2026-08-09, continued the same day, and 2026-08-16) for
inferring exact rosters from the aggregate numbers alone, without any
manual scouting — and honestly documents where it works, where it
doesn't, and why. Later sections correct earlier ones; where they
conflict, the later pass wins. Read "Third pass" before acting on
anything here.

## Why this is possible at all

> ## ⚠ CORRECTION (2026-08-16) — READ BEFORE ANYTHING ELSE
>
> **The `total_score` / `total_value` equations below are WRONG.** Both
> alliance totals include the **alliance fund** (`resources / 150`), which
> belongs to no planet, so neither is a plain sum over members:
>
> ```
> alliance.size[T]        = sum(member.size[T])                    <- EXACT
> sum(member.xp[T])       = (total_score[T] - total_value[T]) / 60 <- EXACT
> alliance.total_value[T] = sum(member.value[T]) + fund[T]/150     <- fund term
> alliance.total_score[T] = sum(member.score[T]) + fund[T]/150     <- fund term
> ```
>
> `fund[T]` is unknown, varies tick to tick, and is capped at 75,000,000
> resources (500,000 value). Verified against round 117 ground truth: all
> 22 alliances reconcile exactly on member count, size and xp, with a
> value-only non-negative residual peaking at 69,417,300 resources — just
> under the documented cap. Every zero-fund alliance is a 1-member one or
> PATSA.
>
> **Consequence: essentially every `INFEASIBLE` verdict recorded in this
> doc is untrustworthy.** Any solve that constrained `total_score` or
> `total_value` as an exact sum was solving a false model, and would
> correctly report INFEASIBLE for any alliance with a non-empty fund.
> That is why small/inactive alliances solved cleanly while every large
> active one failed; why the "scaling wall" appeared to track alliance
> size (larger alliances use the fund); why TiT round 118 "broke at tick
> 66" (it started using the fund); and why the delta method failed too
> (the fund changes over time). The "hidden member" was the fund.
>
> **The corrected model — size and xp exact, value bounded — solved TiT
> round 118 immediately**, `OPTIMAL` in 2.9s across all 75 ticks of its
> 10-member regime, confirming all five gap-derived joiners plus
> `sp3qt2r2` as the tenth member.
>
> Re-read every INFEASIBLE result below in this light before relying on
> it. Results that used **only** `size`, or that involved zero-fund
> alliances (1-2 member ones, PATSA), are unaffected.

`alliance_stat.size` is a literal sum over that alliance's member planets'
own `planet_stat.size`, and member `xp` is recoverable exactly from
`total_score - total_value`. So for an alliance with N members at tick T
there are **two** exact equations per tick, plus one inequality:

```
alliance.size[T]  = sum(member.size[T] for member in members)
(total_score[T] - total_value[T]) / 60 = sum(member.xp[T] for member in members)
sum(member.value[T]) <= alliance.total_value[T]     (shortfall = the fund)
```

These hold simultaneously across *every* tick the roster is unchanged.
With N unknowns (which N of ~300-400 planets are members) and 2 exact
equations per tick across many ticks, the system is still wildly
overdetermined — in practice a unique subset satisfies it, provided the
roster didn't change during the window.

## The core technique: exact subset-sum via constraint solver

1. Pick a tick window `[t0, t1]` where the alliance's `members` count is
   constant (no visible joins/leaves).
2. Build a candidate pool: every planet present at every tick in the
   window, excluding planets already confirmed as belonging to a
   *different* alliance, and excluding any value whose own `value[t0]`
   already exceeds the target (a cheap necessary-condition prune).
3. Formulate as an exact-cover feasibility problem: choose exactly N
   planets from the pool such that, at every tick in the window, the
   sum of their `size`/`score`/`value` equals the alliance's reported
   totals exactly.
4. Solve with a real combinatorial solver — not a MILP with a weak
   objective. **CP-SAT (Google OR-Tools)** consistently outperformed
   **CBC (via `pulp`)** for this problem shape by a wide margin; CBC
   would grind for 20+ minutes on cases CP-SAT resolved in seconds.
   Install via `uv pip install ortools` (see the `ortools.sat.python.cp_model`
   API — `NewBoolVar` per candidate, `Add(sum(x) == N)`, one equality
   constraint per tick per field).

When it solves, the match is exact — not a fit, not a probability. This
is fundamentally different from (and much stronger than) the
`suggested_planets` single-tick nearest-neighbor heuristic already in
`btk/api/routers/web.py` (see below).

### The residual trick for finding *new* joiners

Once N-1 members of an N-member alliance are confirmed, the Nth (a new
joiner) can be found by subtracting the N-1 known members' own
continuing stats from the alliance's tick-by-tick totals and solving the
*residual* as a 1-unknown or few-unknown exact-match problem — far
cheaper than resolving the whole roster from scratch. This is how TiT's
tick-29 and tick-34 joiners (Holoman, SteveNT) and QQ's tick-22 joiner
(Retribution) were pinned down exactly. Same idea generalizes to k
unknowns if k members are new/unconfirmed.

### Finding the founding roster (no small-membership anchor)

If a round's tracked history starts with an alliance already at full
size (no tick where it had 1-2 members to anchor from), bisect the
tick range: try solving `[t0, t0+1]`, then `[t0, t0+2]`, etc., until the
solver reports `INFEASIBLE` — this proves a roster change happened
somewhere in that window (see "swap detection" below). The largest
*provably feasible* prefix window is solvable outright. This is how
TiT's 5 founding members and CaRnies' merge history were isolated.

## Hard-won lessons and failure modes

### UNKNOWN is not INFEASIBLE — don't conflate them

Both CBC and CP-SAT can time out without proving anything either way.
**Infeasibility proofs are cheap** (the solver's presolve/cut-generation
often disproves a window in under a second when it's genuinely
impossible) — **feasibility proofs are expensive** (finding one needle
in an astronomically large haystack). A budget-limited "UNKNOWN" result
tells you nothing about whether the true answer is yes or no; only trust
`INFEASIBLE` and `OPTIMAL`/`FEASIBLE` as real signal.

### Roster churn is common — even without a visible member-count change

Several alliances (TiT, PussycatZ, BBQQ, KittenZ, Newdawn ft HR, Pink
Fluffy Unicorns, VGN, and the post-merge Tal Shiar) show a member
*left and was replaced* at some tick without the `members` count ever
changing — invisible to anyone just watching the headcount. This was
caught by extending the equality window and getting a fast
`INFEASIBLE` on a range that "should" have been stable. **Always
re-verify a solved roster against the full available tick range** — a
window that solved cleanly on ticks 14-18 can still be wrong for tick 25
if membership changed in between.

### The generic "idle planet" ambiguity — verify uniqueness before trusting a match

Many freshly-started or totally inactive planets share **bit-for-bit
identical** trajectories (a fixed game formula for zero-activity
planets: `size=0`, `score`/`value` growing by the same small constant
every tick). When a solved roster includes a slot with these
characteristics, there can be a dozen or more indistinguishable
candidates — the solver picks one arbitrarily (usually by variable
order), and that pick is **not evidence of identity**. This produced one
real mistake in this session: `rf6kw8zr` was inserted as a confirmed
Tal Shiar member, but turned out to be one of 19 identical `(size=0,
score=380, value=380)` planets at tick 14 — later deleted from
`planet_intel` once caught.

**Before inserting any match with `size=0` or otherwise suspiciously
round/generic starting stats**, check how many other planets share the
exact same `(size, score, value)` at that tick:

```sql
select external_id, ruler_name, planet_name
from planet_stat ps join planet p on p.id = ps.planet_id
join tick t on t.id = ps.tick_id and t.number = <tick>
where ps.size = <size> and ps.score = <score> and ps.value = <value>;
```

More than one row back means the identity is genuinely ambiguous — leave
that slot unassigned rather than guessing. Note the math is still valid
even when the identity isn't (any of the tied candidates gives an
identical numeric contribution, so residual-subtraction calculations
built on top of an ambiguous slot remain correct — only the specific
planet name is uncertain).

### The scaling wall: roughly 19+ unknowns with churn resists exact solving

Both solvers, even CP-SAT with 12 parallel workers and 10-20 minute
budgets, failed to *exactly* resolve any alliance with **both** (a) 19+
members **and** (b) any internal roster churn during the tracked window.
Two alliances with zero churn the whole round (Chocolate Starfish, 20
members; Imperium, 40 members) solved in under a second on the full
range — the size of N alone isn't the blocker, unbroken exact-equality
windows are what matters. (**Both of those "zero churn" claims have
since been disproven** — Chocolate Starfish in the Second pass, Imperium
in the Third. The point about N-vs-churn still stands; the two examples
do not.) A later pass in the same session (see
"Breaking the scaling wall" below) got high-confidence *best-guess*
rosters for every alliance this section originally left unresolved, by
combining GPU search with narrowed CP-SAT — but none of them converted
to a fully proven exact match, so the wall for *exact* solving still
stands; what changed is having a usable answer on the other side of it.

Approaches tried and their outcomes, for future reference:

- **Bisection to find a stable sub-window**: works when it works (TiT,
  Tal Shiar pre-merge), but most windows return UNKNOWN rather than a
  clean answer, so it doesn't reliably localize the churn point.
- **Trajectory-correlation candidate pruning** (rank candidates by
  correlation between their own tick-over-tick deltas and the target's
  aggregate delta pattern, keep only the top few×N): **empirically
  unreliable** — validated against Chocolate Starfish's known-true
  20-member roster, and true members ranked anywhere from 4th to 264th
  out of ~300 candidates. A single member's trajectory is too
  independent of a 20-40-person aggregate to correlate meaningfully.
  **Do not trust this as a pre-filter without validating against a
  known-solved case first.**
- **Relaxed knapsack optimization** (maximize value picked subject to
  inequality ≤ target at every tick, instead of exact equality): solves
  near-instantly but the optimal packing left large gaps versus the true
  target at every earlier tick — greedy value-maximization doesn't
  approximate true membership well, so it's not a useful proxy either.
- **Two-segment swap model** (jointly solve for roster-before and
  roster-after around an unknown breakpoint tick, linked by a small
  symmetric-difference constraint): theoretically the right model for a
  "roster A, one swap, roster B" structure, and does find real swaps for
  small N (this is effectively how the TiT/Henrik-T swap and CaRnies
  merge were understood), but for N≈23+ it inherits the same scaling
  wall — twice the variables of an already-hard single-roster problem.
- **Single-snapshot nearest-neighbor fit** (see `suggested_planets` in
  `web.py`): only discriminating for *small* alliances where the
  average-missing-member gap is distinctive. For large alliances (20+
  members) every reasonably big planet ends up looking like the
  "closest fit" to whichever unresolved alliance happens to have the
  highest average member value — not real signal, just an artifact of
  alliance size. Confirmed by testing against the round's top-50
  planets by score: results clustered around the same 2-3 alliances
  regardless of the actual planet, with no meaningful separation.

### Breaking the scaling wall: GPU-accelerated search + narrowed CP-SAT

The exact-solver wall above held for the rest of a single investigative
session, until a second pass revisited it with two additions: a GPU
search to find *approximate* structure, and a way to convert that
structure into something CP-SAT could actually finish.

**GPU evolutionary local search.** Represent a candidate roster as a
binary vector over the full candidate pool (one bit per planet); stack
thousands of these into one big matrix; score *all* of them against the
tick/field constraint matrix in a single batched matrix multiply per
generation (`X @ A` where `A` is `[n_candidates, n_ticks*3]`). This is
exactly the kind of massively-parallel linear algebra a GPU is built
for — even a low-end card (a GTX 980 in this case) evaluates a
population of thousands in milliseconds. Install a CUDA-enabled PyTorch
build matching the card (`uv venv --python 3.12` in a separate venv, since
current PyTorch wheels lag the latest CPython; `uv pip install torch
--index-url https://download.pytorch.org/whl/cu121` for an older Maxwell-era
GPU). Local search loop: keep the best few hundred individuals each
generation ("elites"), generate children by mutating a copy of a random
elite (flip a handful of 1-bits to 0 and an equal number of 0-bits to 1,
keeping the fixed member-count invariant), inject a batch of fresh
random individuals every generation to fight premature convergence, and
track the best (lowest sum-of-squared-error-vs-target) individual seen.

Two counterintuitive findings from tuning this:
- **True two-parent crossover (recombining two elites' gene sets) performed
  *worse* than mutation-only local search**, and much slower per
  generation. Recombining two decent partial solutions doesn't reliably
  preserve the good sub-structure of either — for this problem shape,
  simple mutation-based hill-climbing beat a "smarter"-looking genetic
  operator.
- **Convergence plateaus hard and fast** — violation drops >90% in the
  first ~200 generations, then crawls for the next 10,000+. Running
  longer past the plateau is close to wasted compute; better to accept
  the plateau and move to the next step below.

**Per-candidate selection frequency, not the single best individual.**
The single best individual the GPU finds is still just a heuristic
guess and usually includes 1-2 wrong picks. Far more useful: after the
population converges, compute what fraction of the top ~1500
individuals (by violation) contain each candidate. For every alliance
tried this way, the frequency distribution showed a **sharp cliff** —
N-ish candidates sitting at 0.93-1.00 (present in nearly every good
solution the search found), then either a hard drop straight to ~0.01-0.05
(noise), or a secondary cluster in the 0.15-0.55 range that turned out to
be the same generic-idle-planet tie described above, splitting the
GPU's "vote" across several indistinguishable stub candidates for one
ambiguous slot.

**Feed that ranking into CP-SAT as forced variables.** Force the
high-frequency candidates to `x=1`, leave the member-count's worth of
remaining slots free across a generous pool (the full remaining
candidate set costs almost nothing once most variables are fixed), and
let CP-SAT solve the now-tiny residual problem exactly. This is the
same shape as the residual trick above, just with the "known members"
supplied by search instead of prior confirmation.

**This combination still didn't crack the largest/churniest alliances.**
Every alliance in the 19+ member, has-churn category — VGN, PussycatZ,
BBQQ, KittenZ, Newdawn ft HR, Pink Fluffy Unicorns — stayed
`INFEASIBLE` even after narrowing, at every relaxation level tried (down
to forcing only a handful of the most-confident candidates and freeing
5-7 slots from the *entire* remaining pool). The likely explanation,
confirmed by a diagnostic below: churn is pervasive enough in these
alliances that no fixed-membership window — not the historical
founding window, not even a bare 2-tick window — has a true exact
solution to find. The GPU's near-zero-but-nonzero violation is the
closest *possible* fit to an target that quietly has no exact answer
under the single-fixed-roster model, not evidence the search almost
succeeded.

**Diagnostic: check the confident subset's own sum before assuming
churn is to blame.** Before concluding a residual is unsolvable, sum
just the high-frequency candidates (dropping the ambiguous tail
entirely) against the raw alliance totals at each tick. If **size**
matches exactly (or within a couple of units) while score/value are off
by a small fraction of a percent, that's strong evidence the confident
set is correct and the shortfall is just the missing/wrong tail
members — not proof of pervasive churn. This showed up repeatedly:
several alliances' top-N-minus-a-few candidates summed to the *exact*
`size` target across 3 ticks with only a few-hundred-out-of-millions
score/value gap.

**Anchor on the most recent ticks, not the historical founding window.**
A late but important pivot: instead of anchoring the search on the
alliance's early-round history (where the original scaling-wall section
above spent most of its effort), anchor on the **last 2-3 ticks** of
available data instead. This consistently produced much lower GPU
violations and much cleaner frequency cliffs than any historical window
tried for the same alliances — recent history has had less *cumulative*
time to churn than a window stretching back to tick 14, and it's also
the actionable, currently-relevant roster. If you only have budget for
one window per alliance, make it the most recent one.

### The "best-guess" confidence tier

Given the above, this session settled on a second, explicitly weaker
tier of confidence for `planet_intel` rather than only ever inserting
provably-exact matches:

- **Confirmed exact match**: the CP-SAT/subset-sum equality held across
  the full tested tick range with no `INFEASIBLE` result anywhere in the
  range. Comment says so plainly and doesn't hedge.
- **Best-guess match**: the GPU search's high-frequency candidate set
  for the most recent ticks, where forcing those candidates into CP-SAT
  and freeing the rest of the pool for the remainder still came back
  `INFEASIBLE`, but the confident set's own `size` sum matched the
  target exactly or near-exactly at each tick checked. Inserted with a
  comment that says explicitly it is **not** a confirmed exact match and
  should be verified before being treated as certain — e.g. `"KittenZ
  best-guess member -- GPU-search+CP-SAT fit at ticks 38-40, NOT a
  confirmed exact multi-field match (...). Verify before treating as
  certain."`

This mirrors the existing `suggested_planets` heuristic's own honesty
about being "a fit, not a detection" — but is materially stronger
evidence than that heuristic, since it's anchored in genuine
optimization pressure across thousands of candidate solutions rather
than a single-tick nearest-neighbor guess (which was separately proven
unreliable for large alliances — see above).

**Before storing best-guess planet IDs, verify they belong to the
current round.** `planet.external_id` is only unique *within* a round
(`planet_round_id_external_id_key UNIQUE (round_id, external_id)`), so a
raw `SELECT id FROM planet WHERE external_id IN (...)` with no
`round_id` filter can silently pull in a same-named planet from a
different archived round if one happens to collide. Always join through
`round` (or filter `round_id = <current>`) when resolving external IDs
to internal IDs for a bulk insert.

### A "confident" frequency cliff is not proof of identity — always exclude known-elsewhere planets

A late sanity check on this session revealed something important about
the GPU frequency-ranking signal itself. Re-running KittenZ's search
with **zero exclusions** (every planet in the round eligible, including
ones already confirmed to *other* alliances) produced an even *lower*
violation than the properly-excluded run — but its high-frequency
cluster included `rsjq5m2j`, `7mkbkcds`, and `t6tdr4ib` (all three
provably-exact **Imperium** members), plus `a1xifw7y` (confirmed sole
member of **temp**) and `vx4je29i` (confirmed sole member of **Three
Little Pigs**), each scoring 0.96-0.99 — indistinguishable from the
genuine candidates in the same list.

The lesson: a planet's stats can fit a *different* alliance's aggregate
numbers just as well as its real one, purely by coincidence of size/
growth trajectory. **The frequency cliff measures "fits the target
numbers well," not "is this alliance's member"** — those only coincide
once every other explanation (i.e. every planet's real, already-known
assignment) has been excluded from the pool. This is why the exclusion
set (`ALREADY_ASSIGNED`, refreshed from the live `planet_intel` table
immediately before building each alliance's candidate pool) is a
**correctness requirement, not just a speed optimization** — running
this method with an open pool will silently produce confident-looking
answers that steal other alliances' real members.

**How this was verified as a non-issue in practice, not just asserted:**
after the fact, cross-check that every alliance's row count in
`planet_intel` still matches exactly what was intended to be inserted
for it (`SELECT alliance, count(*) FROM planet_intel GROUP BY
alliance`) — if any run had accidentally claimed a planet already
belonging to an earlier-solved alliance, that earlier alliance's count
would come up short (its row silently overwritten by the later insert,
since `planet_id` is the primary key and `ON CONFLICT ... DO UPDATE`
replaces in place with no history). In this session every count matched
exactly, because the sequential insert order (each alliance's run
refreshing the exclusion set from the current DB state right before
selecting candidates) made a same-planet collision structurally
impossible for the runs that actually wrote to the table — only the
deliberately-unexcluded diagnostic run (never itself inserted) exposed
the risk.

### Judging which of two competing fits is more trustworthy

Re-running the same alliance with a different tick window won't always
agree with a prior run — this happened for both Tal Shiar and KittenZ
when re-anchored on a wider or more recent span. Two lessons from
comparing them head-to-head:

- **A fixed-membership model applied across a window that actually spans
  a real join/leave produces a measurably worse fit** — not just a
  slightly worse one. Re-running Tal Shiar (which gained 2 members at
  tick 41) across ticks 37-41 with `N_MEMBERS=39` uniformly applied to
  all 5 ticks — even though only tick 41 actually had 39 members — gave
  a violation an order of magnitude higher (0.0046) than clean
  single-regime runs (~0.000001-0.00001), and the frequency tail was
  visibly messier (a gradual slope instead of a sharp cliff). Don't
  average member-count regimes into one model; if the count changed
  mid-window, that's a hard signal to split the window at that tick, not
  average through it.
- **When two independent runs for the same (stable-membership) alliance
  disagree, check per-tick `size` residuals against ground truth for
  each candidate set, don't just compare which one converged to a lower
  internal violation score.** For KittenZ, a 3-tick run and an
  independent 5-tick run overlapped on only 26 of ~40 picks. Summing
  each set's actual `planet_stat` values against the alliance's real
  totals at every tick showed the 3-tick set was tighter on the ticks it
  was built from but didn't cover the newest tick at all, while the
  5-tick set covered all 5 ticks but fit distinctly worse at the single
  most recent one — itself a signal that the roster may have shifted yet
  again right at the boundary. Neither run should be blindly trusted
  over the other without this direct comparison.

### Alliance renames and merges

Alliance identity in `alliance_listing.txt` is name-keyed, and names
change. This round, `CaRnies` (23 members) absorbed the original
`Tal Shiar` (12 members) at tick 23 (confirmed via the `feed` table's
`Alliance Merging` category), and the merged 35-member entity was
tracked under three different names in sequence: `CaRnies` (tick 24) →
`Car Shiar` (ticks 25-28) → `Tal Shiar` (tick 29 onward) — the final
name it kept. **A gap in an alliance's `alliance_stat` tick coverage, or
a sudden jump in `members`, is worth cross-checking against `feed`
(`category = 'Alliance Merging'`) and against other alliance names in
the same round before assuming it's a simple join/leave.**

## A better approach for churn: sequential residual tracking instead of batch windows

**Status: partially falsified against real data — see "Second pass"
below.** This section was written as a proposal before being tested. The
reframe itself (track changes, not snapshots) still looks directionally
right, but the specific claim that a pairwise 2-tick sweep is *cheap*
turned out to be wrong — read the correction in the "Second pass"
section before applying anything here. Left in place rather than
deleted so the reasoning trail (what was assumed, what broke, why) stays
intact.

Everything above solves for a roster over a fixed multi-tick *window* —
and that's exactly why churn is so damaging: a single join/leave anywhere
in the window makes the whole window's equality constraints
unsatisfiable, so the solver has to fall back to guessing (GPU
frequency + narrowed CP-SAT) over the *entire* alliance rather than just
the part that actually changed. This section proposes a different
framing that was not implemented in the 2026-08-09 session but should
remove the scaling wall rather than work around it. Treat it as a
recipe to try next, not a validated result — it hasn't been run against
a real churny alliance yet.

### The reframe: track roster changes, not roster snapshots

The batch approach asks "what N-subset satisfies these constraints over
this whole window?" — one big combinatorial problem whose size scales
with alliance size, and which is *all-or-nothing*: any churn anywhere
invalidates the entire window.

The sequential approach asks a much smaller question repeatedly: "given
the roster at tick T, what changed by tick T+1?" This is the doc's own
"residual trick" (see above), promoted from a patch applied late in the
process to the primary method. Once tick T's roster is confirmed,
subtract each continuing member's own `size`/`score`/`value` at T+1 from
the alliance's reported totals at T+1. The residual is what the
joins/leaves that tick have to account for — almost always a 0-3-unknown
subset-sum problem, solvable by CP-SAT in milliseconds *regardless of
how large N is*, because problem size now tracks the size of that
tick's *change*, not the size of the alliance.

Chained across the whole tracked history, this turns one intractable
40-member/many-churn-events batch problem into dozens of trivial 1-3
member problems — and produces a full join/leave timeline as a natural
byproduct, instead of a single point-in-time snapshot.

### Detecting churn as a cheap pairwise sweep, not a bisection — DISPROVEN, see below

**This subsection's central claim did not survive contact with real
data.** Original proposal, left verbatim for the record:

> Bisecting a window to find where it breaks (as used for TiT and Tal
> Shiar pre-merge) is coarse: each probe is itself a multi-tick solve,
> and a probe that comes back `UNKNOWN` rather than `INFEASIBLE` tells
> you nothing (see "UNKNOWN is not INFEASIBLE" above). Instead, sweep
> every adjacent tick pair `(T, T+1)` across the whole tracked range as
> an independent 2-tick equality solve. Two-tick windows are cheap, and
> infeasibility proofs are fast... This sweep is worth running
> unconditionally, even for alliances assumed stable — it's the direct,
> cheap way to confirm "zero churn the whole round" (as true for
> Chocolate Starfish and Imperium) rather than inferring it from
> `members` never changing.

What actually happened when this was run against real round-118
production data (see "Second pass" below for the full account): **2-tick
windows were *not* cheap.** Sweeping KittenZ across all 30 adjacent tick
pairs with the full ~130-planet candidate pool gave `INFEASIBLE` on 12
pairs and `UNKNOWN` (15s budget) on the other 18. Worse, the 12
"cheap" `INFEASIBLE` results turned out to depend entirely on a
candidate-pool exclusion set built from the *prior session's own
unverified best-guess assignments* — re-running the identical pairs with
an unrestricted pool (every planet in the round eligible) turned every
one of them into `UNKNOWN` too. And the "confirm Chocolate Starfish is
zero-churn" example cited above was flatly wrong: CS's own correctly-
membership-windowed pairwise sweep also came back all-`UNKNOWN`, and
independently, CS turned out to *not* be zero-churn at all (see below).

The likely reason 2-tick windows are hard rather than easy: **fewer
ticks means fewer equality constraints, which means a *larger* feasible
region to search or refute** — the opposite of "cheap." A tightly-
constrained many-tick window prunes the search space far more
aggressively than a loosely-constrained 2-tick one, which is why the
doc's original full-window batch solves (seconds) were consistently
faster than small windows meant to be a "cheap" probe (which frequently
timed out at 15-90s without resolving either way). Don't reuse this
2-tick-sweep idea without budgeting for it being *expensive*, and don't
trust an `INFEASIBLE` result from a narrowed pool without independently
checking it survives an unrestricted pool.

### Seeding the chain

Sequential tracking still needs one confirmed starting roster to anchor
on. Two ways to get one, both already used elsewhere in this doc:

- **Bisect/sweep back to a small founding roster**, if the alliance's
  tracked history starts small enough to solve outright (as for TiT).
- **For alliances with no small anchor anywhere in history** (started
  already at 19+ members): repurpose the GPU frequency-ranking pass
  (see "Breaking the scaling wall" above), but apply it *per residual
  step* rather than to the whole roster at once. Searching for "who are
  the 1-3 planets that joined/left at this specific tick" is a far
  smaller search space than "who are all 40 members," so the
  frequency-cliff signal (which the doc found gets muddier at large N)
  should sharpen rather than degrade. This effectively makes the GPU
  pass a candidate-generator for the residual solve instead of a
  whole-roster guesser.

### Expected failure mode: simultaneous multi-member moves

The one case this doesn't trivially solve is several members
joining/leaving in the *same* tick — the residual becomes a k-unknown
subset-sum instead of 1-unknown, and k could occasionally be large
enough (e.g. a merge, as with CaRnies → Tal Shiar) to need the
already-documented two-segment swap model or a full alliance-merge
detection via the `feed` table instead of blind residual-solving. Detect
this case by residual size (a large, non-trivial leftover after
subtracting known continuing members) rather than assuming every tick
transition is a simple 0-3-member delta.

### Why this should still respect the doc's existing lessons

Nothing here bypasses the failure modes documented above — it only
shrinks the problem size at each step:

- Still verify each step's residual match for the size=0/generic-stub
  ambiguity before accepting it as identified.
- Still exclude planets already confirmed to other alliances before
  solving a residual — the "confident frequency cliff is not proof of
  identity" lesson applies just as much to a 1-3-unknown residual as to
  a 40-unknown roster.
- A wrong or ambiguous pick early in the chain propagates forward
  silently (later residuals will "explain" the wrong baseline) until
  re-verified against ground truth, so the doc's "always re-check
  outside the solved window" habit matters *more* here, not less — check
  it after every step, not just at the end of a whole run.

## Second pass (2026-08-09, continued): validating against real production data, and a full reset

The addendum above was written speculatively, then tested the same day
against real round-118 data pulled live from the production DB
(`um9i.dev`, via `podman exec systemd-btk-db psql`). This section
records what that testing actually found — both the corrections to the
addendum (above) and a set of genuinely new, previously-unknown issues
with the *existing* `planet_intel` data from the original 2026-08-09
session.

### `planet_intel` had drifted since the original session, for reasons that couldn't be fully explained

Before touching the churn methodology, a simple cross-check was run:
sum `alliance_stat.members` per alliance at the round's latest tick and
diff it against `count(*)` grouped by `alliance` in `planet_intel`. This
surfaced two things:

- **A harmless casing bug**: `PATSA` (4 members per `alliance_stat`, 0
  tagged) vs. `Patsa` (0 members, 4 tagged) — same alliance, inconsistent
  string casing between the two tables. Same pattern for `QQ`/`qq`. Nets
  to zero real data loss but is a real bug: `planet_intel.alliance` is
  free text with no FK to `alliance.id`, so nothing enforces the two
  tables agree on spelling.
- **Real, substantial shrinkage in three alliances** versus the counts
  the original doc reported: BBQQ 24→8 rows, KittenZ 39→26 rows,
  PussycatZ 18→9 rows. A search for stale comments (rows tagged to a
  *different* alliance whose `comment` text still mentioned BBQQ/
  KittenZ/PussycatZ — the fingerprint the doc's own "ON CONFLICT DO
  UPDATE replaces in place with no history" warning predicts for a
  collision) found **zero** matches, which doesn't confirm a collision
  happened (an overwrite replaces the comment too, so it wouldn't leave
  this trace either way) but doesn't rule it out. No conclusive
  explanation was found — most likely someone manually pruned these
  three alliances' unverified *best-guess* rows back down at some point
  after the original session (consistent with the doc's own "verify
  before treating as certain" caveat for that tier), but this could not
  be confirmed from the data alone, since `planet_intel` keeps no
  history.

**Lesson: `planet_intel`'s current state is not self-auditing.** The
`members` vs. `count(*)` cross-check above is cheap and worth running
before trusting any existing roster data, not just when something looks
wrong. It would have caught this immediately.

### Cheap global cross-check: total planets minus total alliance members = unaffiliated count

A related, generally useful check: summing `alliance_stat.members`
across *every* alliance at a tick and subtracting from the total planet
count at that tick gives the number of planets with no alliance at all
— 388 total planets − 329 summed members = 59 unaffiliated at tick 44
for round 118. This is a legitimate extra global constraint (a hard
cardinality bound), not just a sanity check: it belongs in the "global
joint formulation across all alliances simultaneously" idea floated
earlier in this doc (one more equation to prune the search with), and
it's also a fast way to validate any full-round `planet_intel` rebuild
without needing every alliance solved.

### "Confirmed exact" is not a permanent guarantee — ties can silently diverge later

The single most important finding of this pass. Re-solving **TiT**
against real data anchored on its *most recent* stable window
(ticks 34-44) — rather than the full founding-to-current range the
original session used — resolved cleanly to **7/7 `OPTIMAL`, no
ambiguity**. The original session's doc entry for TiT explicitly notes
"one ambiguous founding stub slot" — i.e. a tie in the *founding* window
that was never resolved. Two ties that are numerically indistinguishable
at the tick they're solved can still diverge later as one twin goes
active and the other stays idle; a match that was legitimately "exact"
against the window it was tested on can become wrong against a wider
one without anything about the roster itself changing. **A `planet_intel`
row's "confirmed exact" label is only as good as the window it was
verified against — it needs periodic re-verification against newer
ticks, not a one-time stamp.** This is the real-world validation of what
the abandoned pairwise-sweep idea was trying (badly) to achieve; the
fix that actually worked was simpler: anchor on the most recent stable
window instead of the founding one, per the doc's own earlier
"Anchor on the most recent ticks" finding — this pass confirms that
finding generalizes to more than just the large/churny alliances it was
originally derived from.

### Chocolate Starfish's "zero churn" claim does not hold

The original doc lists Chocolate Starfish alongside Imperium as one of
two "zero churn the whole round" alliances, both solving in under a
second. Re-tested from scratch (no reliance on the old `planet_intel`
roster at all — pure `alliance_stat` + `planet_stat`), CS's own
documented stable window (ticks 15-44, `members` constant at 20) came
back **`INFEASIBLE`**, confirmed independent of any prior data:

- Right-anchored windows ending at tick 44 were tested at decreasing
  sizes: 15-44, 20-44, 25-44, 30-44, and 35-44 were all `INFEASIBLE` in
  1-8 seconds (fast, confident proofs — not timeouts). 38-44 (7 ticks)
  and 39-44 (6 ticks) were *also* `INFEASIBLE`, just slower (11s, 60s).
  40-44 and smaller windows only returned `UNKNOWN` even at 45-90s
  budgets — consistent with the "small windows are hard to resolve
  either way" finding above, not evidence those windows are actually
  feasible.
- This means CS's roster genuinely changed somewhere in the back half of
  the round. The largest confirmed-infeasible right-anchored suffix
  found is 39-44 (6 ticks); nothing shorter than that resolved either
  way within budget, so the true churn tick has not been localized more
  precisely than "at or before tick 39" given the compute spent so far.
- Two independent attempts to find the specific wrong/missing member (an
  exact same-size single-member swap search across all 20 previously-
  confirmed members, and a small 124-candidate/3-tick CP-SAT solve
  warm-started with `add_hint` toward the old roster, 300s/12 workers)
  both failed to find or confirm a fix.

**Take the doc's original "zero churn" results as unverified pending
re-check**, not as ground truth — Imperium was independently re-verified
and does still hold up (40/40, no drift found), but Chocolate Starfish
did not, and neither had been re-tested since the original session
before this pass.

> **Superseded by the Third pass (2026-08-16):** Imperium does *not*
> still hold up. At tick 208 its stored 40/40 roster is short by exactly
> 35,058 on both score and value (size and count still exact), and no
> single same-size swap explains the gap. Both alliances originally
> called "zero churn the whole round" have now failed re-verification.
> See "Imperium's 'confirmed exact' 40/40 no longer holds either" below.

### Decision: `planet_intel` was wiped for round 118 and is being rebuilt from a clean slate

Given the drift found above (unexplained shrinkage in 3 alliances, a
casing bug, and at least one previously-"confirmed" alliance that no
longer holds up), the working decision this session was to **delete all
`planet_intel` rows for round 118** rather than keep patching a data set
of uncertain provenance, and rebuild incrementally with a growing
exclusion set, smallest/least-ambiguous alliances first. (This was a
production DELETE; it ran only after explicit operator confirmation, and
a full CSV export of the pre-delete state was taken first as a
reversible backup.)

Rebuild results so far, solved in ascending member-count order with a
growing exclusion pool (each alliance's confirmed members removed from
the candidate pool before solving the next), **anchored on the most
recent stable window per alliance rather than the founding window**:

| Alliance | N | Window | Result |
|---|---|---|---|
| 10× single-member alliances (Alliance of One, CV, Hades, One of One, Questers, PT, Order and Occupation, Three Little Pigs, asf, qetuo) | 1 each | full available range | `OPTIMAL`, all 10 |
| qq | 2 | 22-44 | `OPTIMAL` |
| PATSA | 4 | 14-44 | `OPTIMAL` |
| TiT | 7 | 34-44 | `OPTIMAL`, no ambiguity (see above) — but see the Third-pass TiT correction below |
| Chocolate Starfish | 20 | 15-44 | `INFEASIBLE` — genuine unresolved churn, left out of this pass |

23 planets across 13 alliances confirmed and inserted into `planet_intel`
with comments noting the method, tick window, and rebuild date.

> **Superseded by the Third pass (2026-08-16).** None of this data is
> live any more: `planet_intel` was wiped a second time (all rows except
> PATSA's four) and rebuilt from only what is provable without search.
> See "Decision: wiped again, and rebuilt to a stricter '100%' standard".
PussycatZ (19), BBQQ (28), Tal Shiar (39), and the four 40-member
alliances (Imperium, KittenZ, Newdawn ft HR, Pink Fluffy Unicorns, VGN)
have not been re-touched in this pass — treat their `planet_intel`
coverage as empty/unknown again until re-solved, not as "known to be
missing what the original doc said."

### Updated practical guidance from this pass

- **Run the `members`-vs-`count(*)` cross-check (and the unaffiliated-
  count check) before trusting existing `planet_intel` data**, not just
  when a discrepancy is reported.
- **Default to anchoring every alliance's solve on its most recent
  stable window**, not the founding window, even for alliances that seem
  unambiguous — this is now confirmed (not just theorized) to resolve
  ties that a founding-window solve leaves ambiguous.
- **Don't trust a narrow/short window's `INFEASIBLE` or `FEASIBLE`
  result without also checking a wide window** — small windows are
  *harder* to resolve confidently than large ones for this problem
  shape, the opposite of the original intuition.
- **A `CpSolver` warm-start uses `model.add_hint(var, value)` per
  variable** in the installed `ortools` version (9.15), not the list-
  based `AddHint(vars, values)` signature referenced in older examples.
- **Treat every "confirmed exact" label as provisional**, and re-verify
  it whenever new ticks arrive, especially for alliances noted to have
  had any ambiguous tie at solve time.

## Third pass (2026-08-16): 6× more data, and the global-partition reframe

Round 118 was still live a week later, at **tick 208 instead of tick 44** —
195 ticks of history instead of 31, and 403 planets instead of 388. The
premise for this pass was simply "there's much more data now, is it worth
another shot?" The short answer: **more ticks did not crack the hard
alliances**, but the pass turned up a structural property of the data
(closure) that the first two passes never used, produced 13 genuinely
*proven* planets for free, and falsified one more "confirmed exact"
roster.

### More data did not help the churny alliances — VGN retested and still unsolved

VGN (40 members, constant the entire round) was retried first, on the
theory that 164 extra ticks would give a cleaner recent window. It did
not:

| Window | Pool | Result |
|---|---|---|
| 14-208 (195 ticks) | 286 (excl. known) | `INFEASIBLE` |
| 14-208 | 369 (unrestricted) | `INFEASIBLE` |
| 178-208 (31 ticks) | 399 (unrestricted) | `INFEASIBLE` |
| 190-208 (19 ticks) | 400 (unrestricted) | `INFEASIBLE` |
| 195-208 (14 ticks) | 402 (unrestricted) | `INFEASIBLE` |
| 200-208 (9 ticks) | 402 (unrestricted) | `UNKNOWN` (80s) |
| 202-208 (7 ticks) | 402 (unrestricted) | `UNKNOWN` (80s) |

Two things worth carrying forward from this table:

- **These `INFEASIBLE` results survived opening the pool.** The Second
  pass found KittenZ's `INFEASIBLE` results evaporated into `UNKNOWN`
  once the (unverified, prior-session-derived) exclusion set was removed,
  and rightly warned not to trust a narrowed-pool infeasibility proof.
  VGN's do *not* evaporate — every window down to 195-208 is infeasible
  against the **entire** round's planet set. That is a real, unconditional
  proof that VGN's roster changed within each of those spans, not an
  artifact of bad priors.
- **The small-windows-are-harder finding held again.** 195-208 (14 ticks)
  proved infeasible, while the strictly easier-looking 200-208 (9 ticks)
  and 202-208 (7 ticks) could not be resolved either way. Consistent with
  the Second pass; do not read those `UNKNOWN`s as "these windows are
  clean."

### The GPU frequency cliff did not appear for VGN — and the doc's own diagnostic caught it

Per the recipe's step 4a, the GPU search was run on VGN at 178-208 and
again at 200-208. Anchoring recent did help the *fit* (best violation
56,627 → 3,406), consistent with "anchor on the most recent ticks."
But **no sharp cliff appeared at either window** — the ranking sloped
gently (0.948, 0.887, 0.874, … 0.672, then 0.549, 0.534, …) instead of
showing a ~40-candidate plateau near 1.0 followed by a hard drop.

The doc's own "check the confident subset's own sum" diagnostic then
correctly refused the result. Summing the 12 highest-frequency
candidates against VGN's real totals:

```
tick 200: size  4687/13305  (short 8618)
tick 204: size  4702/13395  (short 8693)
tick 208: size  5451/14237  (short 8786)
```

That is ~35% of the target with a roughly constant ~8,600 shortfall —
nothing like the "size matches exactly, score/value off by a fraction of
a percent" signature that justifies a best-guess insert. **Nothing was
written to `planet_intel` for VGN.** This is the diagnostic working as
designed, and is the clearest example so far of it preventing a bad
insert.

### GPU environment has bitrotted since the first pass — what actually works now

The doc's install line (`uv pip install torch --index-url
.../whl/cu121`) is still correct but the *reason* now matters more:
current default PyTorch wheels ship kernels only for compute capability
**7.5+**, and the GTX 980 is Maxwell, **sm_52**. A default `pip install
torch` imports fine and reports `torch.cuda.is_available() == True`, then
dies on the first real op with `CUDA error: no kernel image is available
for execution on the device`. Verify with `torch.cuda.get_arch_list()` —
a working build lists `sm_50`. `torch==2.5.1+cu121` in a Python 3.12 venv
does; the system Python 3.14 + torch 2.11 build does not.

Two more traps that cost real time this pass:

- **The doc's mutation loop as written is a severe CPU bottleneck.**
  Looping per individual (`for i in range(n_children)` with a
  `torch.randperm` inside) pegs one core and starves the GPU entirely —
  3,000 generations did not finish in 4+ minutes. Vectorize the whole
  batch instead: rank uniform noise masked to the 1-bits and to the
  0-bits, take `topk(n_flips)` of each, and `scatter_` the flips. Same
  operator, ~100× faster, and the GPU actually gets used.
- **Don't call `.numpy()` on the result.** A minimal torch venv has no
  numpy, and the final frequency-ranking line will crash *after* the
  whole search has run. Use `.tolist()`.

### The structural finding: the system is closed, and nobody had used that

The Second pass noted in passing that total planets minus summed
`alliance_stat.members` gives the unaffiliated count, and called it "one
more equation." It is much stronger than that. At tick 208, **all three
fields close exactly**:

```
                 n      size          score          value
planets        403    112694      160492382      137368802
alliance sum   338    105757      153301704      130967184
unaffiliated    65      6937        7190678        6401618
```

So the "no alliance" bucket is **not a free residual** — its cardinality
and all three sums are known exactly by subtraction. Every planet in the
round is accounted for by some bin, and there are no free variables
anywhere in the system. That makes membership a **closed set-partition
problem**, not 23 independent subset-sum problems.

Why the distinction matters: solving alliances one at a time discards
mutual exclusivity, which is exactly the hole the doc patched with the
sequential `ALREADY_ASSIGNED` exclusion set. That patch is order-dependent
and only as good as the solves that preceded it (and the Second pass
found it silently propagating bad priors). In a joint formulation
exclusivity is structural — a planet carries exactly one label, so it
cannot be awarded to two alliances, with no exclusion set at all.

### Three candidate extra equations, all ruled out — don't re-investigate

Since the model is short of equations, this pass checked every remaining
unused field in the schema for a fourth constraint. All three are dead
ends, recorded here so nobody spends the time again:

- **`feed` does not contain membership events.** `CLAUDE.md` notes that
  BTK deliberately doesn't cross-reference `user_feed.txt` to backfill
  FKs, which raises the hope that join/leave announcements are sitting
  there unparsed. They are not. Round 118's entire feed is 195 rows
  across 7 categories: `Planet Ranking` (161), `Galaxy Ranking` (13),
  `Anarchy` (11), `Alliance Ranking` (4), `Combat Report` (3),
  `Relation Change` (2), `Alliance Merging` (1). The only membership-
  relevant rows are the single merge event (already used) and relation
  declarations. There is no "X joined Y" event to mine.
- **`alliance_stat.score` (counted_score) has no usable rule.** It is
  *not* "sum of the top K members" — 1-member alliances have gaps too,
  and several show a suspiciously flat gap of exactly 40,000 (Hades,
  Questers, Hardliner, Order and Occupation) while others differ
  (Doghouse 291,912; Terra 181,008; PATSA 0). Without knowing the game's
  formula this cannot be turned into a constraint.
- **`alliance_stat.points` is not the sum of member `xp`.** Tested
  against Imperium's 40 tagged planets at tick 208: summed `xp` = 72,601
  vs reported `points` = 12,520.

So `size`, `total_score` and `total_value` remain the only three exact
per-alliance equations, exactly as the original section assumed. Note
that `score` and `value` are strongly correlated (`score − value` is a
small xp-derived component), so the three fields carry rather less
independent information than their count suggests — worth remembering
when reasoning about how over-determined the system really is.

### Immediate payoff: 13 planets proven by pure lookup, no solver

A 1-member alliance's reported totals **are** its single member's planet
stats. So it resolves by direct dictionary lookup on the
`(size, score, value)` triple — no search, no tick window, no solver.
At tick 208 all 11 single-member alliances matched **exactly one** planet
each (no ties to disambiguate), and `Fairies` (2 members) resolved
uniquely by exhaustive pair search over ~400 planets in under a second:

```
Lonely Muskateer  -> 1:3:2   DArtagnan        Questers      -> 5:9:6   Rhythar
Three Little Pigs -> 3:5:2   Walter Chell     Hades         -> 3:6:6   Hell Spawn
PT                -> 4:7:6   PT               One of One    -> 4:3:6   Codfish
Alliance of One   -> 200:15:1 Darth Maelstrom Terra         -> 4:8:6   DEFENDERS_
Hardliner         -> 200:3:1 Astroturfer      Doghouse      -> 6:8:3   MR Pepper
Order and Occupation -> 200:13:2 Zephox1914
Fairies           -> 3:9:4 Retribution + 3:9:3 get
```

This is a **proof, not a fit** (the triple is unique in the round), and
it independently re-validates the summation model. It belongs at the top
of the recipe — see step 0 below. It also shrinks every other bin's pool
for free.

### Imperium's "confirmed exact" 40/40 no longer holds either

The Second pass explicitly cleared Imperium ("independently re-verified
and does still hold up (40/40, no drift found)") while condemning
Chocolate Starfish. At tick 208 Imperium's stored roster is **wrong**:

```
                 n     size          score          value
intel roster    40    16785       23347115       18991055
alliance_stat   40    16785       23382173       19026113
gap              0        0          35058          35058
```

Count and `size` are exact; score and value are each short by **exactly
35,058**. An exhaustive search for a single same-`size` swap (replace one
tagged planet with any untagged planet of identical size, +35,058 on both
score and value) found **zero** candidates, so this is at least a two-planet
change, not one mis-tagged member.

Combined with the Second pass's Chocolate Starfish result, **both**
alliances the original session called "zero churn the whole round" have
now been disproven. Treat "zero churn" as a claim that has never once
survived re-testing against newer ticks.

### The global partition solve was tried, and did not work — honestly reported

Given the closure, the obvious move is to solve all 24 bins at once at a
single tick. The appeal is that **churn cannot exist within a tick** — a
snapshot is internally consistent by definition — so the entire
fixed-window/churn failure mode that dominates this doc simply does not
apply. Both attacks were implemented and both failed:

- **CP-SAT, 403 planets × 24 bins (9,672 booleans)**, exact cardinality
  plus exact `size`/`value`/`score−value` sums per bin: **`UNKNOWN` at
  300s** with 8 workers. Re-run with `size` alone (coefficients in the
  hundreds rather than ~1e8): **still `UNKNOWN` at 120s** with 12 workers.
  So the blocker is the 24-way exact partition itself, not coefficient
  conditioning.
- **Global GPU local search**, where the unit of search is a whole-universe
  label vector and the mutation operator is a **swap** of two planets'
  labels — which preserves every bin's cardinality exactly, so every
  candidate visited is a structurally valid partition and exclusivity is
  free. Converged 1.36 → **1.6e-5 and plateaued, never reaching 0.**

Validated against PATSA, the only roster confirmed by real in-game
scouting: the search returned **1 of 4 correct**.

**Do not misread that as "many valid partitions exist."** The true
assignment is a zero-violation solution by construction (it is reality),
and the search never reached zero — so this is a local-search plateau
short of feasibility, not evidence of non-uniqueness. Uniqueness of the
single-tick partition remains **untested**, not disproven.

**Correction to the reasoning that motivated this pass.** The framing
that drove it was "churn is an artifact of our fixed-window model, so a
single tick should suffice." Half of that is right and half is wrong:
churn-immunity within a tick is real and is a genuine advantage, but a
single tick is badly **under-constrained** — 24 bins × (3 sums + 1
cardinality) ≈ 96 equations to pin 403 assignments. Time is still
needed. What changes is *what time is for*: not to impose a hard
fixed-roster equality window that one join makes `INFEASIBLE`, but to
add equations to a global model that stays feasible.

### Decision: wiped again, and rebuilt to a stricter "100%" standard

Given that Imperium (the last surviving "confirmed exact" large roster)
had drifted, the working decision was to **delete every `planet_intel`
row for round 118 except PATSA's four** — PATSA being the only roster
corroborated by real in-game scouting rather than by this method. 81 rows
were deleted; a full CSV export of all 85 pre-delete rows was taken first
as a reversible backup, per the precedent set by the Second pass's wipe.
What went: Imperium (40), Chocolate Starfish (21), TiT (8), qq (2) and
10 single-member alliances — three of which (`qetuo`, `asf`, `CV`) were
tagged to alliances that no longer appear in the current listing at all.

The table was then rebuilt with **only** what can be proven without any
search, to a deliberately stricter standard than previous passes used:

| Alliance | N | Verified across | Member(s) |
|---|---|---|---|
| Alliance of One | 1 | 196 ticks (14-209) | `swg5tvqr` |
| Hades | 1 | 196 ticks (14-209) | `ukho5enn` |
| One of One | 1 | 196 ticks (14-209) | `sd6j83sr` |
| Order and Occupation | 1 | 196 ticks (14-209) | `wj5zg4d7` |
| Questers | 1 | 196 ticks (14-209) | `awb9owcy` |
| Three Little Pigs | 1 | 196 ticks (14-209) | `vx4je29i` |
| PT | 1 | 188 ticks (22-209) | `3g2oalgr` |
| Lonely Muskateer | 1 | 136 ticks (74-209) | `40fw8mfl` |
| Terra | 1 | 86 ticks (124-209) | `whwong4p` |
| Hardliner | 1 | 68 ticks (142-209) | `icp39trw` |
| Doghouse | 1 | 29 ticks (181-209) | `erjhplvu` |
| Fairies | 2 | 126 ticks (84-209) | `6q6oe9s8` + `tilydunj` |
| PATSA | 4 | (manual scouting) | untouched |

**The standard applied.** Not "matched at the tick I looked at," which is
how the generic-idle-planet mis-taggings happened, but: intersect the
candidate set across **every** tick at which the alliance had that member
count, and accept only if exactly one candidate survives all of them. For
all 12 the per-tick candidate count was **1 at every single tick**
(`min=1, max=1`) — there was never any ambiguity to resolve, at any tick.

`planet_intel` now holds 17 rows across 13 alliances, and every one
reconciles to 100% against the live tick: tagged count equals `members`,
and size/score/value each sum exactly. Every remaining row is proven
rather than fitted — the first time that has been true this round.

Three further planets were added later in the same session from the
counted-score gap technique (see below) — Chocolate Starfish's three joiners
`g1rd963f` (tick 15), `z9jbog80` (69) and `mxw5y0nc` (198) — bringing
`planet_intel` to **20 rows across 14 alliances**. Those three are a
**distinct, weaker tier** than the twelve above: each is the unique planet in
the round carrying the exact score the alliance's gap rose by, which is strong
evidence and independently validated 3-for-3 elsewhere, but it is not the
exhaustive all-ticks uniqueness proof the singles and Fairies have. Their
`planet_intel` comments say so explicitly. Chocolate Starfish therefore shows
as **partial** (3 of 22) in the reconciliation query, not complete — the other
13 alliances still reconcile exactly on all three fields at tick 210.

**This is now automated: `uv run btk-verify-rosters`**
(`scripts/verify_rosters.py`, tests in `tests/test_verify_rosters.py`).
Read-only by default; `--insert --updated-by <id>` writes. Re-run it as
new ticks arrive — it is fast, deterministic, and needs no solver, so it
is the cheap way to honour this doc's "re-verify, don't trust the stamp"
lesson.

### Two traps found while automating the above

Both are general, and both nearly produced wrong data:

- **Snapshot skew between the two stat tables.** The first verification
  run rejected *all 11* single-member alliances as "model violation." The
  data was fine; a new tick had been ingested between pulling
  `planet_stat` and pulling `alliance_stat`, so one snapshot had tick 209
  and the other did not, and every intersection came out empty. Run the
  other way round it could equally have *hidden* a real contradiction.
  The committed script therefore reads both tables inside **one
  `REPEATABLE READ` transaction**, and additionally judges "does this
  alliance still exist" only over ticks present in both. Never assemble
  this analysis from two separately-timed queries against a live ticking
  DB.
- **Historical alliances silently overwrite current ones.** Run without a
  filter, the verifier also proves 11 *defunct* 1-member alliances plus a
  second 2-member one — and `qq` (ticks 22-83) resolves to **exactly the
  same two planets as `Fairies`** (ticks 84-209), i.e. a rename, which
  independently corroborates the doc's "alliance renames and merges"
  section and the original session's finding that Retribution joined qq
  at tick 22. Because `planet_intel` is keyed by `planet_id` with
  `ON CONFLICT DO UPDATE`, inserting the historical roster would have
  silently replaced the live Fairies rows with a dead alliance name —
  precisely the collision this doc warned about. The script now skips
  non-current alliances by default, and `insert()` hard-refuses if any
  planet is claimed by two confirmed rosters rather than letting insert
  order decide.

### The counted-score gap: the first real break on large alliances

Everything above treats `alliance_stat.score` (the dump's `counted_score`) as
useless, because it isn't a plain sum and so can't be a subset-sum constraint.
That was a mistake. The in-game manual explains it:

> Only score gained **while in** an alliance counts towards the alliance score.
> Any score earned before joining will not be counted. When a player leaves,
> the alliance will lose any score contributed by that planet.

So the difference is exactly the score the current members were carrying when
they joined:

```
gap = total_score - counted_score = sum over members of (their score when they joined)
```

Which makes the gap **piecewise constant**, moving only on a join or a leave.
Verified on TiT: across 196 ticks the gap changed at exactly five ticks —
29, 34, 47, 101, 136 — every one of them a tick where `members` also changed.
Nowhere else.

That gives two tools neither solver could provide:

**1. An exact churn detector.** A flat gap over a span proves no visible join
or leave happened in it. The member count alone cannot tell you this, since a
simultaneous leave+join leaves it unchanged — the exact case the doc spent two
passes chasing.

**2. Joiner identification by lookup, not search.** A rise of D at tick T means
somebody joined carrying exactly D, and carried-in score is the joiner's own
score at tick **T-1** (not T — verified: Retribution's score was 81,449 at tick
21 and 82,040 at tick 22, and qq's gap rose by 81,449 when they joined at 22).
So the joiner is whichever planet had score exactly D at T-1 — a dictionary
lookup over ~400 planets.

**This is validated, not assumed.** Run against TiT it returned
`mz6jsab8` (tick 29), `dn6nfrbp` (34) and `ges5dnee` (47) — the exact three
joiners an independent CP-SAT subset-sum had separately proven for the
ticks 47-65 window. Three for three, by a completely different method.

Implemented as **`uv run btk-find-joiners`** (`scripts/find_joiners.py`,
tests in `tests/test_find_joiners.py`); read-only by default,
`--insert --updated-by <id>` writes.

#### The 40,000 signature, and what a zero gap means

A planet's score is `Score = XP*60 + Value` (manual; verified exactly for all
403 planets at tick 210), and the signup grant is 2,000,000 each of
metal/crystal/eonium = 6,000,000 resources, with `Value = Resources/150`. So a
planet that founds or joins an alliance **at signup with the bonus unspent**
carries exactly **40,000**. That signature appears at the first tick of five
alliances (Hades, Hardliner, Order and Occupation, Questers, qq) — and
Hardliner's first tick is 142, which proves it tracks *signup*, not round
start.

A gap of **0** means every member joined holding literally nothing, i.e. they
formed the alliance at signup *before allocating* the bonus. That is why
PATSA's `counted_score` and `total_score` are equal all round, and it is a
property of when its members joined — **not**, as one might assume, a
consequence of its stable membership. Chocolate Starfish's 19 founders are the
same case (gap 0 at tick 14): a pre-organised bloc.

#### The blind spot, which is exactly what still blocks us

A member who joined carrying **zero** contributes nothing to the gap, so their
later departure is invisible to it — and so is a swap in which both parties
carried zero. Alliances founded at signup consist *entirely* of such members.
This is precisely why TiT's and Chocolate Starfish's founder blocs remain
unresolved while every later joiner falls out instantly. **A gap that never
moves is evidence of no visible churn, not proof of a fixed roster.**

### Solving from deltas instead of absolute totals

A second technique from the same session, worth recording with its result.
Instead of matching absolute totals, match **tick-to-tick deltas**: for every
consecutive tick pair, the members' summed deltas must equal the alliance's.

The motivation was that a member frozen in vacation/closed mode is absent from
`planet_listing` and so breaks absolute matching, but contributes exactly
**zero** to every delta — so delta matching should still work on the listed
members alone, and the leftover (alliance total minus the solved members)
would be a constant equal to the hidden member's stats.

**Validate before trusting it, at the size you care about.** It recovers
PATSA's 4 and Fairies' 2 instantly, and — the test that matters — a
**synthetic 21-member** alliance built from known planets is recovered
*exactly* in 0.0s over a 129-tick window. So `INFEASIBLE` from this model at
N≈21 is real, not a scaling artifact.

**Result: it does not rescue TiT or Chocolate Starfish.** TiT is `INFEASIBLE`
for every k from 7 to 10, including on the completely stable 206-210 window
where no planet enters or leaves the listing. CS is `INFEASIBLE` across each
of its membership regimes. Since the delta constraint is strictly *weaker*
than absolute matching, failing it is the stronger negative — and it rules out
the frozen-hidden-member explanation, because a frozen member would leave the
deltas satisfiable.

### TiT re-examined, and a correction to the Second pass

The Second pass reported TiT as **7/7 `OPTIMAL`, no ambiguity** at ticks 34-44
and treated that as settled. It is true *for that window* and false as a
statement about TiT's roster. Re-solved across the whole round:

| Window | N | Result |
|---|---|---|
| 14-28 | 5 | `OPTIMAL` — 4 determined + 1 stub tie |
| 34-46 | 7 | `OPTIMAL` — **not unique** |
| 47-65 | 8 | `OPTIMAL` — 7 determined + 1 stub tie |
| 47-66, and every window touching tick 66 or later | 8-10 | `INFEASIBLE` |

So TiT provably churns at **tick 66**, and is unrecoverable after it. The
member count does not move there, and neither does the counted-score gap —
which, per the blind spot above, means the swap was between two members who
each carried zero. That combination is invisible to *both* instruments, and is
the single thing blocking TiT.

The infeasibility is robust: it holds for N=6..11, with the exclusion set
removed entirely (full 403-planet pool), on windows with a bit-stable planet
set, under a "recruit contributes size but not score" model, and under the
delta model. Nothing explains it. The bounded hidden-member model is
*unidentifiable* — minimising and maximising the implied hidden planet's size
at tick 100 gives the full range 0 to 1115 — so it can be neither confirmed
nor refuted.

**Two methodological warnings from this, both of which cost real time:**

- **Do not carry a roster across a tick where you have already proven the
  roster changed.** Combining the ticks 47-65 members with the two later
  gap-derived joiners produced a confident-looking "9 of 10 identified" that
  was unsound on its face — it assumed across tick 66 exactly what tick 66
  disproves. The resulting residual matched no planet at any of 35 ticks.
- **An unbounded residual proves nothing.** A first pass at the hidden-member
  test left the leftover free to be `>= 0` rather than forcing it to zero in
  the control case, so a meaningless "OPTIMAL" came back that merely meant
  "sums <= target". Always run the null case (zero hidden members) and check it
  reproduces the known `INFEASIBLE` before believing the k>0 case. Equally:
  bound the leftover by the largest planet that actually exists, or it will
  cheerfully absorb 1.4M of value that no planet in the game could hold.

Note also that "no listed planet matches the residual" is **not** evidence
against a hidden member — a planet absent from the dump cannot match by
definition. The reason to set that hypothesis aside is the identifiability
test above, not the absence of a match.

### Counting the search space: why a single field proves nothing

A useful calibration. At tick 210 TiT's `size` fell by exactly 114. The number
of 10-planet combinations losing exactly 114 is **24,016,173,239,295,299**
(~2.4x10^16). Only 43 of 403 planets changed size at all; the count is
dominated entirely by padding with the 360 unchanged ones (`C(360,8)` and
friends). The genuinely constrained part is tiny — just 3 pairs and 28 triples
of *changed* planets sum to -114.

The lesson: any single field, on any single tick, is worthless for
identification, and a "match" on one is not evidence. It is the simultaneous
multi-field, multi-tick constraint that carries all the information.

### Where this points next (proposed, not validated)

A single label vector scored across **many** ticks — same 403 unknowns,
~72 more equations per tick added — with roster change as a **penalised
cost** rather than a hard constraint. That keeps the global model's
exclusivity and closure while fixing its under-determination, and churn
stops being fatal because it is priced instead of forbidden.

The practical wrinkle: **the planet set is not stable**, so this needs
presence-aware handling. Counts drift 404 → 403 → 402 → 403 across ticks
194-208, and only **ticks 206-208 share a bit-identical planet set**. A
planet must therefore contribute only at ticks where it exists, with
per-tick cardinality taken from that tick's `alliance_stat.members`.

The other thing the global framing makes possible, which no per-alliance
method can offer: **per-planet proof by contradiction.** Add the
constraint "planet X is *not* in alliance A" and re-solve; if that comes
back `INFEASIBLE`, X's membership in A is proven rather than fitted.
That would replace the best-guess tier with something real — but it
requires the global model to be tractable first, which on this evidence
it is not yet.

As with the previous sessions, the scripts for this pass lived in a
scratch directory and were not committed.

## Fourth pass (2026-08-16, continued): the fund correction lands, and three large alliances fall

Following directly from the Third pass's fund discovery (see the ⚠
correction block above `Why this is possible at all`), `verify_rosters.py`
was rewritten to the corrected model and re-run against the live round 118
database. All 12 alliances it had previously proven (11 singles + Fairies)
re-confirmed exactly, over the same planets and the same spans, extended by
the three new ticks ingested since. Checking each one's implied fund
(`total_value - sum(member.value)`) at every tick showed it was **exactly
zero throughout for all 12** — the old broken model had been accidentally
correct on every alliance it ever solved, which is precisely why nothing
caught the bug earlier. No 1- or 2-member alliance was rescued by the fix,
because none had ever been failing; the fund's real victims were always the
larger, CP-SAT-solved alliances.

### `btk-solve-roster`: the ad hoc fund solver, generalized and committed

The Third pass's `fund_solve.py` (a one-off script, alliance name and tick
range hardcoded per run) was generalized into `scripts/solve_roster.py`
(`btk-solve-roster`), which:

- builds the same three-constraint-per-tick CP-SAT model (`size` exact,
  `xp` exact, `value` bounded by `[0, 500_000]` fund slack) over any
  `(alliance, lo, hi)` window with constant `members`,
- **excludes planets already confirmed to a different alliance by
  default** (see below for why this is not optional),
- **always runs a uniqueness probe** — re-solving with the found set
  forbidden — and refuses to report a result as confirmed unless that
  probe comes back `INFEASIBLE`,
- accepts `--force-in` to pin known members (e.g. a joiner already named
  by `btk-find-joiners`) into the solution, and
- inserts into `planet_intel` behind `--insert --updated-by`, same
  convention as the other two scripts.

Three alliances were solved and inserted with it this pass:

**TiT (10 members).** The Third pass's already-proven roster (`5ht2iixt,
95hvg1i4, 99svzsph, c6l863jh, dn6nfrbp, ges5dnee, mlppd05s, mz6jsab8,
q1wtiafi, sp3qt2r2`) was re-checked against three new ticks (211-213) added
since — size, xp, and fund-bounded value all still reconcile exactly. Since
the original CP-SAT solve had already proven this was the *unique*
10-subset over 75 ticks, adding three more constraint ticks can only shrink
a solution set, never grow it, so uniqueness carried forward without
re-running the solver. Inserted unchanged.

**PussycatZ (20 members) — the "scaling wall" alliance that wasn't.** The
doc's own guidance (`### The scaling wall`, above) puts 19+ members with
churn as the point exact solving stops working. PussycatZ has 20 members,
but has had **zero visible churn since tick 95** — the counted-score gap
sat at a constant `381,199` for **119 consecutive ticks** through 213, the
longest stable window found for any alliance this round. CP-SAT solved it
to `OPTIMAL` in 5.1 seconds, and the uniqueness probe came back
`INFEASIBLE`. The lesson: the scaling wall is about *churn*, not member
count in isolation — a large, perfectly stable alliance can be easier to
solve exactly than a small, churning one, because every stable tick adds a
constraint almost for free.

### The closure-exclusion bug, caught by its own symptom

**Chocolate Starfish (22 members)** is where excluding already-claimed
planets stopped being a nice-to-have and became load-bearing. Its current
22-member window (ticks 198-213) is only 16 ticks — CP-SAT returned
`OPTIMAL`, but the uniqueness probe found a second solution, and worse: the
*first* "solution" included `iyw6qa27`, a planet **already proven to belong
to PussycatZ** minutes earlier in the same session. A plausible-looking
`OPTIMAL` status does not protect against this — the model has no notion
that a planet can only be in one alliance unless that constraint is put in
by hand.

The fix generalizes beyond this one bug: **use the alliance's own join
history to find a longer, older, fixed-membership window, not the current
one.** `btk-find-joiners` had already dated Chocolate Starfish's three join
events precisely (ticks 15, 69, 198 — three separate single-planet joins,
never a leave). That means the interval between two consecutive joins is
the longest available window at a fixed count, and — because membership
only grew — its solution is a **strict subset** of every later roster. The
interval between the tick-69 and tick-198 joins is 129 ticks at exactly 21
members, versus 16 ticks at 22. Solving the 21-member window (with
already-claimed planets excluded, and the known tick-69 joiner `z9jbog80`
forced in with `--force-in`) returned `OPTIMAL` in 4.8s and the uniqueness
probe came back `INFEASIBLE` — genuinely unique, and it correctly recovered
both already-known joiners (`g1rd963f` from tick 15, `z9jbog80` from tick
69) as members. Union that 21 with the known tick-198 joiner `mxw5y0nc` to
reach the current 22, and the three exact identities (size, xp,
fund-bounded value) reconcile at all 16 ticks of the current window — the
same closure check that caught the bug now confirms the fix.

**Practical upshot for `### The scaling wall` and step 1 of the recipe
below:** for a *growing* alliance (no leaves), don't anchor the solve on
the most recent stable window by default — check `btk-find-joiners` for the
join history first, and solve the longest inter-join interval instead. The
Second-pass guidance to anchor on the most recent ticks was itself
motivated by a case (TiT) with no known join history to exploit; where join
history *is* known, the longest interval usually beats the most recent one,
as it did here (129 ticks vs. 16).

**Running total on the live round-118 database after this pass:** 72
planets across 18 alliances — the 12 from `btk-verify-rosters` (11 singles
+ Fairies), 4 PATSA, 22 PussycatZ, 22 Chocolate Starfish, 10 TiT.

## Practical recipe

*(Updated per the "Second pass" findings: step 1 anchors on the most
recent stable window rather than the longest available one, and step 4
carries a warning about small-window tractability. Step 0 is new in the
Third pass. Step 1 is revised, and step 3 is now enforced by `btk-solve-roster`
rather than a manual set, per the Fourth pass.)*

0a. **Run `uv run btk-find-joiners` first — it is free and needs no solver.**
   The counted-score gap names every joiner who carried non-zero score, by
   lookup, regardless of how large the alliance is. It also tells you exactly
   which tick spans had no visible churn, which is the information the batch
   solver most needs and cannot derive. Remember its blind spot: members who
   joined carrying zero (i.e. any alliance founded at signup) are invisible to
   it, both joining and leaving.

0. **Then resolve everything derivable without search — run
   `uv run btk-verify-rosters`.** Every 1-member alliance is a direct
   lookup (its totals *are* its member's stats) and every 2-member
   alliance falls to an O(n)-per-tick pair scan. Accept a match only if a
   single candidate survives the intersection across *every* tick at that
   member count — not merely at the tick you happened to look at. This
   costs nothing, is immune to churn, and shrinks the candidate pool for
   every alliance solved afterwards. Also compute the closure (total
   planets − summed `members`, and the same subtraction on
   size/score/value) to get the exact unaffiliated bucket before
   starting.

1. Pull `alliance_stat` history for the target alliance. **Run
   `uv run btk-find-joiners --alliance <name>` first (step 0a already told
   you to run it for everything, but re-read its output for this specific
   alliance) and check whether it names any join events with no matching
   leave** — i.e. the alliance only ever grew. If so, per the Fourth pass's
   Chocolate Starfish result, solve the **longest interval between two
   consecutive join events**, not the most recent window — its solution is
   a strict subset of every later roster, and it is very often both longer
   and more tractable than the current window (129 ticks vs. 16 in that
   case). Force the interval's boundary joiner in with `--force-in`. Only
   fall back to anchoring on the **most recent** stable window (the
   Second-pass TiT guidance) when there's no usable join history — e.g. the
   alliance has had leaves, swaps, or founding-only zero-carry joins that
   `btk-find-joiners` can't see.
2. Pull `planet_stat` for every planet across that same tick range
   (`(external_id, ruler_name, planet_name, tick, size, score, value)`).
3. Exclude planets already confirmed to other alliances (a running
   set — grows as you solve more alliances, shrinking the candidate
   pool for every subsequent one) and any planet confirmed to have no
   alliance at all. **This is not optional** — the Fourth pass's first
   Chocolate Starfish attempt returned a plausible `OPTIMAL` roster that
   silently included a planet already proven to belong to PussycatZ, with
   nothing about the solver status hinting at the error.
   `scripts/solve_roster.py` (`btk-solve-roster`) does this exclusion
   automatically from live `planet_intel`, and only skips it if you
   explicitly pass `--no-exclude-claimed`. Before relying on this exclusion
   set (or any existing `planet_intel` data at all), run the
   `members`-vs-`count(*)` cross-check from the Second-pass section — don't
   assume prior data is trustworthy just because it exists.
4. Try the full window first with CP-SAT (`ortools.sat.python.cp_model`,
   `num_search_workers = <cpu count>`) — or just run `btk-solve-roster
   <alliance> <lo> <hi>`, which builds this model directly. **Don't
   substitute a small window for the full one to save time** — small
   windows have a larger feasible region to search/refute and were
   empirically *slower* to resolve than large ones in the Second pass, the
   opposite of the original intuition. If `INFEASIBLE`, bisect forward
   from the first tick; if `UNKNOWN` even at generous time budgets
   (10+ minutes), the roster is probably too large/churn-heavy for exact
   CP-SAT alone — move to step 4a rather than stopping. **Whatever finds a
   solution, always run the uniqueness probe next** (re-solve with the
   found set's members forbidden; treat anything but `INFEASIBLE` as
   unconfirmed) — `btk-solve-roster` runs this automatically and refuses to
   report a result as confirmed otherwise.
4a. For alliances CP-SAT can't crack directly: run the GPU search (see
   above) anchored on the **most recent** available ticks, not the
   historical founding window. Take the per-candidate frequency ranking,
   force the high-confidence cluster (the far side of the cliff) into
   CP-SAT, and free the rest of the pool for the remaining slots. If that
   still comes back `INFEASIBLE` at every relaxation level, check whether
   the confident subset's own `size` sum matches the target exactly —
   if so, store it as a **best-guess** match (see above) rather than
   discarding the work entirely.
5. On any solved roster, **re-check it against ticks outside the solved
   window** to catch invisible churn before trusting it as final.
6. Before inserting a match, check for `size=0`/generic-stub ambiguity
   (see above) and skip inserting that specific planet's name if there's
   a tie — the alliance is more accurately "N-1 confirmed, 1 unresolved
   slot" than "N confirmed" in that case.
7. Insert into `planet_intel` with a `comment` describing the method,
   tick range, and confidence tier (confirmed exact vs. best-guess), and
   leave `nick` NULL — the ruler name is not a player nickname and
   shouldn't be presented as one. Double-check `round_id` when resolving
   external IDs in bulk (see above).

## Results from the 2026-08-09 session (round 118, "Lost SoulS") — SUPERSEDED

**This section is a historical record of the first pass only. It no
longer describes the live state of `planet_intel`.** As documented in
"Second pass" above, this data was found to have drifted from these
original numbers for unclear reasons (BBQQ, KittenZ, PussycatZ
substantially undercounted vs. what's listed below), at least one of
these "confirmed exact" entries didn't survive re-verification
(Chocolate Starfish), and the whole `planet_intel` table for round 118
was subsequently wiped and is being rebuilt from scratch. See "Second
pass" for the current rebuild status (13 alliances/23 planets confirmed
as of that section) — the numbers below are what this method originally
produced, kept for reference, not what's currently stored.

**Confirmed exact matches (91 planets):** TiT (7/7, one ambiguous
founding stub slot), QQ (2/2, fully resolved), PATSA (already known from
manual scouting, cross-validated by this method), 11 single-member
alliances, Chocolate Starfish (20/20), Tal Shiar's original pre-merge
roster (11/12 confirmed — one stub tie removed), Imperium (40/40).

**Best-guess matches (227 planets, GPU-search + narrowed-CP-SAT, see
above — explicitly flagged as unconfirmed in their `planet_intel`
comments):** PussycatZ (18/19), BBQQ (24/28), KittenZ (39/40), Newdawn
ft HR (40/40), Pink Fluffy Unicorns (40/40), VGN (40/40), and the
post-merge Tal Shiar's ex-CaRnies contingent (26 planets, found via the
residual of Tal Shiar's *current* total minus the 11 confirmed
pre-merge members — this incidentally also resolved the previously
separately-tracked "CaRnies" unknown, since CaRnies became Tal Shiar
through the rename chain documented above).

**318 planets tagged in total** — every alliance in the round now has
some `planet_intel` coverage, at one of the two confidence tiers above.
Nothing in this round remains completely untouched, though the
best-guess tier should be treated as a strong lead rather than ground
truth until spot-checked against real scouting.

None of the analysis scripts from this session were committed to the
repo (they lived in a scratch directory) — this doc is written so the
technique, including the GPU pass, can be reimplemented from scratch
using the recipe above.
