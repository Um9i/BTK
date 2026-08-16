"""Guess round-118 planet nicks from cross-round ruler-name reuse.

Usage:
    uv run btk-guess-nicks <round118_planets.csv>

Input is a CSV with header external_id,ruler,planet,race,size,score,x,y,alliance
(alliance may be blank; x,y are optional -- without them, mechanism 4 below
is skipped) -- e.g. round 118's live planet_stat joined against whatever
planet_intel already has confirmed, one row per planet.

THE APPROACH. `ruler` is repicked fresh every round (see nick_history.py's
docstring) so it is not itself a nick -- but if a round-118 ruler name
EXACTLY matches a ruler name some single historical nick used in
docs/data/nick_history.sqlite, and no other nick ever used that exact ruler
name, that's real evidence: a distinctive ruler name is not something two
different people coincidentally pick. `ruler-collisions` already tells us
which ruler names are too generic for this (reused by many different nicks,
e.g. "King" by 144 of them).

ALLIANCE CORROBORATION, three different mechanisms:

1. RECENCY. If this planet's round-118 alliance equals the matched nick's
   MOST RECENT historical alliance, that's a live, continuing pattern --
   the same core group persisted from (usually) round 117 into round 118.
   This is the strong, common case.

2. CLUSTERING. Sometimes an alliance goes dormant for many rounds and later
   reforms with much of its old membership -- a real pattern, not
   hypothetical (round-118 KittenZ and Tal Shiar both show several members
   whose nicks were last in an alliance of that exact name 30-45 rounds
   ago, not in round 117). A single such match, on its own, is weak --
   alliance *names* get reused by unrelated groups too. But if MULTIPLE
   different round-118 members of the same alliance all independently
   match nicks that were in an alliance of that name, that clustering is
   not something coincidental name reuse would produce -- unrelated
   coincidences don't land several different people in the same historical
   3-round window. CLUSTER_MIN is the number of independent old-match
   members required before they're promoted to "high" together; below
   that, an old match stays "medium" (isolated, could be coincidence).

3. DISAMBIGUATION. A ruler name can collide (multiple historical nicks used
   it) and still be resolvable: if the round-118 alliance is already known
   (confirmed independently, e.g. by btk-solve-roster) and EXACTLY ONE of
   the colliding candidates was ever a member of that alliance, that one
   candidate is the answer -- not a guess among equals. This is what
   catches PATSA's "Fiery"/"mPulse"/"Pit": all three ruler names collide
   with unrelated nicks from other rounds (e.g. "Fiery" was also used by
   five other, unconnected players), but only the actual PATSA member's
   nick has ever been in PATSA, so the collision resolves cleanly. Without
   this, a naive ruler-uniqueness rule silently drops every one of these,
   which is wrong: the alliance is already known and independently proven,
   so it isn't circular to use it as the tie-breaker here.

4. GALAXY BUDDIES. Players who know each other tend to colonize the same
   galaxy together round after round -- a real, observed social pattern in
   this game, not hypothetical. So once a planet's galaxy-mates include an
   already-HIGH-confidence nick, that nick's own historical galaxy-mates
   become a lookup: if this planet's remaining ruler candidate(s) include a
   nick who shared a galaxy with that anchor nick in GALAXY_BUDDY_MIN or
   more past rounds, that's corroboration, and if exactly one candidate
   clears the bar it resolves the planet. This mechanism runs as a second
   pass, after 1-3 above have produced a set of trusted anchors -- it can
   only use nicks that are already confident, not other guesses. It then
   runs to a FIXED POINT rather than just once: a planet resolved by
   galaxy-buddy becomes a new anchor itself, which can unlock a *different*
   neighbor on the next hop -- a buddy pack's third and fourth members,
   found via the second rather than directly via the first. It's also
   what catches contradictions mechanisms 1-3 miss: when a nick gets
   proposed for two different round-118 planets, only one of them (if
   either) will actually have real galaxy-buddy history backing it.

CONFIDENCE TIERS, in order:
  high    -- resolved via recency, a corroborating cluster, a clean
             alliance-disambiguated collision, or galaxy-buddy corroboration.
  medium  -- unique ruler match, but no corroboration from any mechanism
             (round-118 alliance unknown, an isolated old-alliance match
             below CLUSTER_MIN, and no galaxy-buddy support).
  none    -- ruler name never seen before, seen from more than one nick
             with no way to disambiguate.

This never writes to planet_intel. It only prints a report -- these are
statistical guesses from name reuse, not scouted intel, and the existing
`!intel` convention (see docs/alliance-membership-inference.md) is explicit
that a guess should never be presented as confirmed.
"""

import csv
import sqlite3
import sys
from collections import defaultdict
from itertools import pairwise
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "docs" / "data" / "nick_history.sqlite"

# How many independent round-118 same-alliance members need to share an
# old-alliance-name match before it's treated as corroboration rather than
# a possibly-coincidental single hit. See "CLUSTERING" above.
CLUSTER_MIN = 2

# How many past rounds two nicks need to have shared a galaxy in before
# that's treated as a real "buddy pack" pattern rather than one-off chance.
# See "GALAXY BUDDIES" above.
GALAXY_BUDDY_MIN = 2

# Largest gap (in rounds) allowed between two candidate nicks' non-overlapping
# usage windows of the same exact ruler string before they're still treated
# as unrelated coincidental reuse rather than one player's own mid-career
# renick. See _renick_merge().
MAX_RENICK_GAP = 3


def load_planets(path: str) -> list[dict]:
    with open(path, newline="") as fh:
        return list(csv.DictReader(fh))


def build_ruler_index(conn: sqlite3.Connection) -> dict[str, set[str]]:
    """ruler (lowercased) -> set of distinct nicks that have ever used it."""
    index: dict[str, set[str]] = {}
    for ruler, nick in conn.execute(
        "SELECT ruler, nick FROM planet_round WHERE ruler != '' AND nick != ''"
    ):
        index.setdefault(ruler.lower(), set()).add(nick)
    return index


def alliance_history(conn: sqlite3.Connection, nick: str) -> list[tuple[int, str]]:
    """Every (round, alliance) this nick has ever had, most recent first."""
    return conn.execute(
        """
        SELECT round, alliance FROM planet_round
        WHERE nick = ?1 COLLATE NOCASE AND alliance != '' AND alliance != 'None'
        ORDER BY round DESC
        """,
        (nick,),
    ).fetchall()


def _merged_alliance_history(conn: sqlite3.Connection, nicks: set[str]) -> list[tuple[int, str]]:
    """alliance_history(), unioned across every nick in a renick-merged identity."""
    rows: list[tuple[int, str]] = []
    for nick in nicks:
        rows.extend(alliance_history(conn, nick))
    rows.sort(key=lambda r: r[0], reverse=True)
    return rows


def _renick_merge(
    conn: sqlite3.Connection, candidates: set[str], ruler_key: str
) -> tuple[str, set[str]] | None:
    """A ruler name can collide across nicks for two very different reasons: unrelated
    players coincidentally picking the same string, or one real player changing their own
    nick label mid-career while reusing the same ruler name. The two are distinguishable:
    if every candidate's use of this exact ruler string forms one continuous, NON-
    OVERLAPPING timeline (no round used by two of them at once, and no gap between
    consecutive nicks' windows wider than MAX_RENICK_GAP rounds), that's not coincidence --
    two unrelated players picking an identical ruler name and then taking turns using it
    without ever overlapping is itself vanishingly unlikely, and the tight round-gap rules
    out two different people who happened to reuse a generic name decades apart. When it
    holds, treat every candidate as one identity, canonicalized to whichever nick's window
    is most recent (e.g. round 118's "Appocomaster" ruler resolves to nick "Appocomaster"
    even though the same player briefly went by "Appoco" in rounds 27-29). Returns
    (canonical_nick, all_nicks), or None if candidates can't be safely merged this way."""
    if len(candidates) < 2:
        return None
    placeholders = ",".join("?" * len(candidates))
    rows = conn.execute(
        f"SELECT round, nick FROM planet_round WHERE ruler = ?1 COLLATE NOCASE "
        f"AND nick IN ({placeholders}) COLLATE NOCASE",
        (ruler_key, *candidates),
    ).fetchall()
    by_round: dict[int, set[str]] = defaultdict(set)
    for rnd, nick in rows:
        by_round[rnd].add(nick)
    if any(len(nicks) > 1 for nicks in by_round.values()):
        return None  # co-occurred in the same round -- genuinely different people

    windows: dict[str, tuple[int, int]] = {}
    for nick in candidates:
        rounds = [r for r, nicks in by_round.items() if nick in nicks]
        if not rounds:
            return None
        windows[nick] = (min(rounds), max(rounds))

    ordered = sorted(windows.items(), key=lambda kv: kv[1][0])
    for (_, (_, end)), (_, (start, _)) in pairwise(ordered):
        if start - end > MAX_RENICK_GAP:
            return None

    canonical = max(windows, key=lambda n: windows[n][1])
    return canonical, set(windows)


def _resolve(
    candidates: set[str], alliance: str, conn: sqlite3.Connection
) -> tuple[str | None, bool]:
    """Pick a single nick out of `candidates`, if possible. Returns (nick, disambiguated).
    disambiguated=True means it was picked by elimination via alliance history (mechanism 3
    in the module docstring), not by ruler-uniqueness alone."""
    if len(candidates) == 1:
        (nick,) = candidates
        return nick, False
    if len(candidates) > 1 and alliance:
        in_alliance = [
            c
            for c in candidates
            if any(a.strip().lower() == alliance.lower() for _, a in alliance_history(conn, c))
        ]
        if len(in_alliance) == 1:
            return in_alliance[0], True
    return None, False


def guess(planets: list[dict], conn: sqlite3.Connection) -> list[dict]:
    ruler_index = build_ruler_index(conn)

    # First pass: resolve each planet to a nick (or not) and classify its
    # alliance match, without deciding tiers yet -- clustering needs to see
    # every old match for an alliance before any single one can be judged.
    prelim = []
    old_match_nicks: dict[str, set[str]] = defaultdict(set)  # alliance -> nicks with an old match
    renick_cache: dict[str, tuple[str, set[str]] | None] = {}
    for p in planets:
        ruler_key = p["ruler"].strip().lower()
        candidates = ruler_index.get(ruler_key, set())
        alliance = (p.get("alliance") or "").strip()

        renick_prefix = ""
        history_nicks = candidates
        if len(candidates) > 1:
            if ruler_key not in renick_cache:
                renick_cache[ruler_key] = _renick_merge(conn, candidates, ruler_key)
            merged = renick_cache[ruler_key]
            if merged is not None:
                canonical, all_nicks = merged
                candidates = {canonical}
                history_nicks = all_nicks
                others = sorted(all_nicks - {canonical})
                others_str = ", ".join(repr(o) for o in others)
                renick_prefix = (
                    f"same player's own renick ({canonical!r} was also {others_str} "
                    f"in other rounds under this exact ruler name) -- "
                )

        nick, disambiguated = _resolve(candidates, alliance, conn)
        if nick is None:
            detail = (
                f"{len(candidates)} distinct historical nicks used this ruler name, "
                "none uniquely resolvable by alliance"
                if candidates
                else "ruler name never seen before"
            )
            prelim.append(
                {**p, "nick": "", "match_kind": "none", "detail": detail, "candidates": candidates}
            )
            continue

        if disambiguated:
            prelim.append(
                {
                    **p,
                    "nick": nick,
                    "candidates": candidates,
                    "match_kind": "disambiguated",
                    "detail": (
                        f"ruler name also used by {len(candidates) - 1} unrelated "
                        f"nick(s), but only {nick!r} was ever in {alliance!r}"
                    ),
                }
            )
            continue

        history = _merged_alliance_history(conn, history_nicks)
        match = (
            next((h for h in history if h[1].strip().lower() == alliance.lower()), None)
            if alliance
            else None
        )
        if match and match == history[0]:
            prelim.append(
                {
                    **p,
                    "nick": nick,
                    "candidates": candidates,
                    "match_kind": "recent",
                    "detail": f"{renick_prefix}round {match[0]}",
                }
            )
        elif match:
            old_match_nicks[alliance].add(nick)
            prelim.append(
                {
                    **p,
                    "nick": nick,
                    "candidates": candidates,
                    "match_kind": "old",
                    "detail": (
                        f"{renick_prefix}round {match[0]} "
                        f"(most recent is {history[0][1]!r}, round {history[0][0]})"
                    ),
                }
            )
        elif history:
            prelim.append(
                {
                    **p,
                    "nick": nick,
                    "candidates": candidates,
                    "match_kind": "unrelated_history",
                    "detail": (
                        f"{renick_prefix}last seen in {history[0][1]!r}, round {history[0][0]}, "
                        f"{len(history)} round{'s' if len(history) != 1 else ''} total"
                    ),
                }
            )
        else:
            prelim.append(
                {
                    **p,
                    "nick": nick,
                    "candidates": candidates,
                    "match_kind": "no_history",
                    "detail": "",
                }
            )

    # Second pass: turn match_kind + cluster size into a final tier.
    results = []
    for p in prelim:
        alliance = (p.get("alliance") or "").strip()
        kind = p["match_kind"]
        if kind == "disambiguated":
            tier, note = "high", f"alliance-disambiguated ruler collision -- {p['detail']}"
        elif kind == "recent":
            tier, note = "high", f"alliance matches nick's last known alliance ({p['detail']})"
        elif kind == "old":
            cluster = old_match_nicks[alliance]
            if len(cluster) >= CLUSTER_MIN:
                tier = "high"
                note = (
                    f"alliance matches nick's {p['detail']} -- corroborated by "
                    f"{len(cluster)} independent round-118 {alliance!r} members whose "
                    "ruler-matched nicks all have this same old alliance, consistent with "
                    "the alliance going dormant and reforming rather than coincidence"
                )
            else:
                tier = "medium"
                note = (
                    f"alliance matches nick's {p['detail']}, but this is the only "
                    f"round-118 {alliance!r} member with that old match -- could be "
                    "coincidental alliance-name reuse, not corroborated by a cluster"
                )
        elif kind == "unrelated_history":
            tier, note = "medium", f"alliance never appears in nick's history ({p['detail']})"
        elif kind == "no_history":
            tier, note = "medium", "no alliance corroboration (nick has no alliance history)"
        else:
            tier, note = "none", p["detail"]

        results.append({**p, "guessed_nick": p["nick"], "tier": tier, "note": note})
    return results


def galaxy_history(conn: sqlite3.Connection, nick: str) -> dict[int, tuple[int, int]]:
    """round -> (galaxy_x, galaxy_y) for every round this nick has ever had coords in."""
    out = {}
    for rnd, coords in conn.execute(
        "SELECT round, coords FROM planet_round WHERE nick = ?1 COLLATE NOCASE", (nick,)
    ):
        parts = coords.split(":")
        if len(parts) == 3:
            out[rnd] = (int(parts[0]), int(parts[1]))
    return out


def _has_coords(results: list[dict]) -> bool:
    return bool(results) and bool(results[0].get("x")) and bool(results[0].get("y"))


def _galaxy_support(
    nick: str,
    self_ext: str,
    gx: int,
    gy: int,
    galaxy_members: dict[tuple[int, int], list[str]],
    anchor_by_ext: dict[str, str],
    conn: sqlite3.Connection,
    cache: dict[str, dict[int, tuple[int, int]]],
) -> set[int]:
    """Every historical round `nick` shared a galaxy with one of its round-118
    galaxy-mates (excluding itself and any galaxy-mate resolved to the same nick)."""
    if nick not in cache:
        cache[nick] = galaxy_history(conn, nick)
    hist = cache[nick]
    shared: set[int] = set()
    for ext in galaxy_members.get((gx, gy), []):
        if ext == self_ext:
            continue
        other = anchor_by_ext.get(ext)
        if not other or other.lower() == nick.lower():
            continue
        if other not in cache:
            cache[other] = galaxy_history(conn, other)
        other_hist = cache[other]
        shared |= {r for r in hist if r in other_hist and hist[r] == other_hist[r]}
    return shared


def apply_galaxy_buddy(results: list[dict], conn: sqlite3.Connection) -> list[dict]:
    """Mechanism 4, run to a fixed point. Each hop can only use nicks that are already
    "high" as anchors -- but a planet resolved BY galaxy-buddy becomes a new anchor itself,
    which can unlock a neighbor of *that* planet on the next hop (a buddy pack's third and
    fourth members, found via the second, not directly via the first). Iterating stops
    finding anything new within 2-3 hops in practice; the loop just runs until a hop adds
    zero new "high" results rather than assuming a fixed hop count."""
    prev_high = -1
    while True:
        results = _galaxy_buddy_hop(results, conn)
        high = sum(1 for r in results if r["tier"] == "high")
        if high == prev_high:
            return results
        prev_high = high


def _galaxy_buddy_hop(results: list[dict], conn: sqlite3.Connection) -> list[dict]:
    """One hop of mechanism 4: try to resolve everything still below "high" using
    galaxy-mate history against the nicks already resolved with confidence so far."""
    if not _has_coords(results):
        return results

    galaxy_members: dict[tuple[int, int], list[str]] = defaultdict(list)
    anchor_by_ext: dict[str, str] = {}
    for r in results:
        galaxy_members[(int(r["x"]), int(r["y"]))].append(r["external_id"])
        if r["tier"] == "high" and r["guessed_nick"]:
            anchor_by_ext[r["external_id"]] = r["guessed_nick"]

    cache: dict[str, dict[int, tuple[int, int]]] = {}
    updated = []
    for r in results:
        if r["tier"] == "high":
            updated.append(r)
            continue
        gx, gy = int(r["x"]), int(r["y"])
        candidates = r.get("candidates") or ({r["nick"]} if r["nick"] else set())
        support = {}
        for c in candidates:
            shared = _galaxy_support(
                c, r["external_id"], gx, gy, galaxy_members, anchor_by_ext, conn, cache
            )
            if len(shared) >= GALAXY_BUDDY_MIN:
                support[c] = shared
        if len(support) == 1:
            ((nick, shared),) = support.items()
            r = {
                **r,
                "guessed_nick": nick,
                "tier": "high",
                "note": (
                    f"galaxy-buddy corroboration -- shared a galaxy with an already-"
                    f"confirmed round-118 member in {len(shared)} past round(s) "
                    f"({sorted(shared)})"
                ),
            }
        updated.append(r)
    return updated


def resolve_conflicts(results: list[dict], conn: sqlite3.Connection) -> list[dict]:
    """A nick can end up "high" confidence for more than one round-118 planet -- a real
    player can only hold one, so at least one such guess is wrong. Break the tie with
    galaxy-buddy support if exactly one side has it; otherwise demote all of them to
    "medium" rather than presenting either as confirmed. Mutates and returns `results`."""
    by_nick: dict[str, list[int]] = defaultdict(list)
    for i, r in enumerate(results):
        if r["tier"] == "high" and r["guessed_nick"]:
            by_nick[r["guessed_nick"].lower()].append(i)

    conflicts = {nick: idxs for nick, idxs in by_nick.items() if len(idxs) > 1}
    if not conflicts:
        return results

    has_coords = _has_coords(results)
    galaxy_members: dict[tuple[int, int], list[str]] = defaultdict(list)
    anchor_by_ext: dict[str, str] = {}
    if has_coords:
        for r in results:
            galaxy_members[(int(r["x"]), int(r["y"]))].append(r["external_id"])
            if r["tier"] == "high" and r["guessed_nick"]:
                anchor_by_ext[r["external_id"]] = r["guessed_nick"]
    cache: dict[str, dict[int, tuple[int, int]]] = {}

    for idxs in conflicts.values():
        scores = {}
        for i in idxs:
            r = results[i]
            if has_coords:
                gx, gy = int(r["x"]), int(r["y"])
                shared = _galaxy_support(
                    r["guessed_nick"],
                    r["external_id"],
                    gx,
                    gy,
                    galaxy_members,
                    anchor_by_ext,
                    conn,
                    cache,
                )
            else:
                shared = set()
            scores[i] = len(shared)
        best = max(scores.values())
        winners = [i for i in idxs if best > 0 and scores[i] == best]

        if len(winners) == 1:
            keep = winners[0]
            r = results[keep]
            results[keep] = {
                **r,
                "note": r["note"]
                + f" (kept over {len(idxs) - 1} conflicting claim(s) on this nick, resolved by galaxy-buddy support)",
            }
            for i in idxs:
                if i == keep:
                    continue
                r = results[i]
                results[i] = {
                    **r,
                    "tier": "medium",
                    "note": r["note"]
                    + " -- CONFLICT: this nick was also matched to another round-118 planet with stronger galaxy-buddy support; demoted",
                }
        else:
            for i in idxs:
                r = results[i]
                results[i] = {
                    **r,
                    "tier": "medium",
                    "note": r["note"]
                    + " -- CONFLICT: this nick was independently matched to multiple round-118 planets with no way to disambiguate; demoted to unresolved",
                }
    return results


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit(f"usage: {sys.argv[0]} <round118_planets.csv>")
    if not DB_PATH.exists():
        raise SystemExit(f"{DB_PATH} doesn't exist -- run 'uv run btk-nick-history build' first")

    planets = load_planets(sys.argv[1])
    conn = sqlite3.connect(DB_PATH)
    results = guess(planets, conn)
    results = apply_galaxy_buddy(results, conn)
    results = resolve_conflicts(results, conn)

    by_tier: dict[str, int] = {}
    for r in results:
        by_tier[r["tier"]] = by_tier.get(r["tier"], 0) + 1
    print(f"{len(results)} planets checked: {by_tier}")
    print()

    for tier in ("high", "medium"):
        rows = [r for r in results if r["tier"] == tier]
        if not rows:
            continue
        print(f"=== {tier} confidence ({len(rows)}) ===")
        print(f"{'external_id':<10} {'ruler':<22} {'alliance':<22} {'guessed_nick':<18} note")
        for r in rows:
            print(
                f"{r['external_id']:<10} {r['ruler'][:22]:<22} {(r.get('alliance') or ''):<22} "
                f"{r['guessed_nick']:<18} {r['note']}"
            )
        print()


if __name__ == "__main__":
    main()
