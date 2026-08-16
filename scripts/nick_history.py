"""Build and query a cross-round join of docs/data/round<N>-alliance-truth.csv.

Usage:
    uv run btk-nick-history build                    # (re)build the database
    uv run btk-nick-history lookup elviz              # every appearance of a nick
    uv run btk-nick-history lookup Cashy               # matches ruler too, not just nick
    uv run btk-nick-history top-rounds                 # nicks seen in the most rounds
    uv run btk-nick-history top-alliances               # nicks with the most distinct alliances
    uv run btk-nick-history ruler-collisions            # one ruler name, multiple nicks

WHY THIS EXISTS. `ruler` is the display name a player picks fresh each round
(often themed to match that round's `planet` name) and is worthless as a
cross-round identity signal on its own. `nick` is the stable one -- e.g. round
117's rank-1 planet is ruler "Cashy", planet "bot number 69", nick "elviz",
alliance Imperium; round 116's nick "elviz" is ruler "John Rambo", planet
"First Blood", alliance Newdawn ft HR. Same player, completely different
ruler/planet/alliance. `nick` is the join key that survives that; `ruler` is
kept alongside because a genuine ruler-name reuse (same person didn't bother
changing it) is still a corroborating signal, just a much weaker one on its
own -- see ruler-collisions, which flags rulers used by more than one nick,
i.e. cases where ruler-name matching *would* have been actively wrong.

Nothing here tells you a round-118 planet's nick -- round 118 isn't finished,
so it isn't on history.planetarion.com yet (only 14-117 are). This is the
reference data for that inference, not the inference itself: once a round-118
ruler/planet/coords/alliance is known, cross-checking it against this
database's alliance-loyalty and ruler-naming patterns is the next step, not
done here.
"""

import argparse
import csv
import sqlite3
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "docs" / "data"
DB_PATH = DATA_DIR / "nick_history.sqlite"


def _maybe_int(cell: str) -> int | None:
    """A handful of old rows have a genuinely blank numeric cell on the source
    site (see fetch_history.py); store those as NULL rather than crashing or
    coercing to a fabricated 0."""
    return int(cell) if cell else None


def build(db_path: Path = DB_PATH) -> None:
    files = sorted(DATA_DIR.glob("round*-alliance-truth.csv"))
    if not files:
        raise SystemExit(f"no round*-alliance-truth.csv files found in {DATA_DIR}")

    db_path.unlink(missing_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE planet_round (
            round INTEGER, rank INTEGER, ruler TEXT, planet TEXT, nick TEXT,
            alliance TEXT, score INTEGER, value INTEGER, xp INTEGER, size INTEGER,
            race TEXT, coords TEXT
        )
        """
    )

    total = 0
    for path in files:
        round_number = int(path.name.removeprefix("round").split("-", 1)[0])
        with path.open(newline="") as fh:
            reader = csv.DictReader(fh, delimiter="|")
            rows = [
                (
                    round_number,
                    _maybe_int(r["rank"]),
                    r["ruler"],
                    r["planet"],
                    r["nick"],
                    r["alliance"],
                    _maybe_int(r["score"]),
                    _maybe_int(r["value"]),
                    _maybe_int(r["xp"]),
                    _maybe_int(r["size"]),
                    r["race"],
                    r["coords"],
                )
                for r in reader
            ]
        conn.executemany("INSERT INTO planet_round VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", rows)
        total += len(rows)

    conn.execute("CREATE INDEX idx_nick ON planet_round(nick COLLATE NOCASE)")
    conn.execute("CREATE INDEX idx_ruler ON planet_round(ruler COLLATE NOCASE)")
    conn.execute("CREATE INDEX idx_round ON planet_round(round)")
    conn.commit()
    conn.close()
    print(f"built {db_path} -- {total} rows across {len(files)} rounds")


def _connect() -> sqlite3.Connection:
    if not DB_PATH.exists():
        raise SystemExit(f"{DB_PATH} doesn't exist yet -- run 'build' first")
    return sqlite3.connect(DB_PATH)


def lookup(name: str) -> None:
    conn = _connect()
    rows = conn.execute(
        """
        SELECT round, ruler, planet, nick, alliance, race, coords, score, value, xp, size
        FROM planet_round
        WHERE nick = ?1 COLLATE NOCASE OR ruler = ?1 COLLATE NOCASE
        ORDER BY round
        """,
        (name,),
    ).fetchall()
    if not rows:
        print(f"no rows matching nick or ruler {name!r}")
        return
    nicks = {r[3] for r in rows}
    if len(nicks) > 1:
        print(f"NOTE: {name!r} matched {len(nicks)} distinct nicks: {sorted(nicks)}")
    print(
        f"{'round':>5}  {'ruler':<24} {'planet':<24} {'nick':<16} {'alliance':<22} {'coords':<10} score"
    )
    for round_, ruler, planet, nick, alliance, race, coords, score, value, xp, size in rows:
        score_str = f"{score:,}" if score is not None else "-"
        print(
            f"{round_:>5}  {ruler[:24]:<24} {planet[:24]:<24} {nick[:16]:<16} "
            f"{alliance[:22]:<22} {coords:<10} {score_str}"
        )


def top_rounds(limit: int) -> None:
    conn = _connect()
    rows = conn.execute(
        """
        SELECT nick, COUNT(DISTINCT round) AS n_rounds,
               GROUP_CONCAT(DISTINCT alliance) AS alliances
        FROM planet_round
        WHERE nick != ''
        GROUP BY nick COLLATE NOCASE
        ORDER BY n_rounds DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    print(f"{'nick':<20} {'rounds':>6}  alliances")
    for nick, n_rounds, alliances in rows:
        print(f"{nick:<20} {n_rounds:>6}  {alliances}")


def top_alliances(limit: int) -> None:
    conn = _connect()
    rows = conn.execute(
        """
        SELECT nick, COUNT(DISTINCT alliance) AS n_alliances, COUNT(DISTINCT round) AS n_rounds
        FROM planet_round
        WHERE nick != '' AND alliance != 'None'
        GROUP BY nick COLLATE NOCASE
        HAVING n_rounds > 1
        ORDER BY n_alliances DESC, n_rounds DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    print(f"{'nick':<20} {'alliances':>9} {'rounds':>7}")
    for nick, n_alliances, n_rounds in rows:
        print(f"{nick:<20} {n_alliances:>9} {n_rounds:>7}")


def ruler_collisions(limit: int) -> None:
    """Rulers used by more than one distinct nick -- proof that matching by ruler
    name alone would have conflated two different players."""
    conn = _connect()
    rows = conn.execute(
        """
        SELECT ruler, COUNT(DISTINCT nick) AS n_nicks, GROUP_CONCAT(DISTINCT nick) AS nicks
        FROM planet_round
        WHERE ruler != '' AND nick != ''
        GROUP BY ruler COLLATE NOCASE
        HAVING n_nicks > 1
        ORDER BY n_nicks DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    print(f"{'ruler':<24} {'nicks':>6}  distinct nicks")
    for ruler, n_nicks, nicks in rows:
        print(f"{ruler:<24} {n_nicks:>6}  {nicks}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("build", help="(re)build the database from docs/data/round*-alliance-truth.csv")
    p_lookup = sub.add_parser("lookup", help="show every appearance of a nick or ruler")
    p_lookup.add_argument("name")
    p_top_r = sub.add_parser("top-rounds", help="nicks seen in the most rounds")
    p_top_r.add_argument("--limit", type=int, default=20)
    p_top_a = sub.add_parser("top-alliances", help="nicks with the most distinct alliances")
    p_top_a.add_argument("--limit", type=int, default=20)
    p_collide = sub.add_parser("ruler-collisions", help="ruler names reused by >1 distinct nick")
    p_collide.add_argument("--limit", type=int, default=20)

    args = ap.parse_args()
    if args.cmd == "build":
        build()
    elif args.cmd == "lookup":
        lookup(args.name)
    elif args.cmd == "top-rounds":
        top_rounds(args.limit)
    elif args.cmd == "top-alliances":
        top_alliances(args.limit)
    elif args.cmd == "ruler-collisions":
        ruler_collisions(args.limit)


if __name__ == "__main__":
    main()
