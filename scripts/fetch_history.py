"""Fetch round ground-truth data from history.planetarion.com into docs/data/.

Usage:
    uv run python scripts/fetch_history.py 114 115 116 117
    uv run python scripts/fetch_history.py --from 14 --to 117 --keep-going
    uv run python scripts/fetch_history.py --all --keep-going   # every round the site lists

Pulls two pages per round from history.planetarion.com/history.php (id=1 is
the per-planet ranking, id=3 is the per-alliance ranking) and writes them as
the same pipe-delimited CSVs this doc's fixtures were built by hand from
earlier in the session -- docs/data/round<N>-alliance-truth.csv (planets,
with the alliance/nick columns that make it useful as roster ground truth,
and stable across rounds for reuse/nickname analysis) and
docs/data/round<N>-alliance-rankings.csv (alliance-level totals).

The site's own table already has the exact rows and column order used
throughout docs/alliance-membership-inference.md; this only reformats
comma-thousands numbers to plain ints and drops the trailing blank row the
page always ends with.

THE SITE DOES NOT 404 ON AN INVALID ROUND -- it silently serves the earliest
round's data instead (round 14; there's nothing before it). A naive fetch of,
say, round 1 would write round 14's data under the name "round1-...", wrong
and undetectable from the row count alone. Every fetch here checks the page's
own "<h2 class="header"> Round N ... </h2>" heading against the round it
asked for and refuses to write on a mismatch -- this is what actually catches
an out-of-range round, not an HTTP status.
"""

import argparse
import csv
import re
import sys
import time
from html.parser import HTMLParser
from pathlib import Path

import httpx

HISTORY_URL = "https://history.planetarion.com/history.php"
DATA_DIR = Path(__file__).resolve().parent.parent / "docs" / "data"

PLANET_HEADER = ["rank", "ruler", "planet", "nick", "alliance", "score", "value", "xp", "size", "race", "coords"]
ALLIANCE_HEADER = ["rank", "name", "score", "actual_score", "size", "members"]

ROUND_LINK_RE = re.compile(r"[?&]round=(\d+)")
HEADER_ROUND_RE = re.compile(r"Round\s+(\d+)")


class HistoryPageParser(HTMLParser):
    """Pulls every <tr><td>...</td></tr> row out of #history_table (skipping the
    header row and the single blank row the page always ends the table with),
    the round number in the "<h2 class=header>" banner, and every "round=N" link
    on the page (used by discover_rounds() to enumerate what the site has)."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.in_table = False
        self.in_cell = False
        self.in_header = False
        self.rows: list[list[str]] = []
        self.round_links: set[int] = set()
        self.header_text = ""
        self._row: list[str] | None = None
        self._cell = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_d = dict(attrs)
        if tag == "table" and attrs_d.get("id") == "history_table":
            self.in_table = True
        elif self.in_table and tag == "tr":
            self._row = []
        elif self.in_table and tag == "td":
            self.in_cell = True
            self._cell = ""
        elif tag == "h2" and attrs_d.get("class") == "header":
            self.in_header = True
        elif tag == "a" and attrs_d.get("href"):
            m = ROUND_LINK_RE.search(attrs_d["href"])
            if m:
                self.round_links.add(int(m.group(1)))

    def handle_endtag(self, tag: str) -> None:
        if tag == "table":
            self.in_table = False
        elif tag == "h2":
            self.in_header = False
        elif self.in_table and tag == "td":
            self.in_cell = False
            assert self._row is not None
            self._row.append(self._cell.strip())
        elif self.in_table and tag == "tr" and self._row is not None:
            if any(self._row):
                self.rows.append(self._row)
            self._row = None

    def handle_data(self, data: str) -> None:
        if self.in_cell:
            self._cell += data
        elif self.in_header:
            self.header_text += data


def _int(cell: str) -> str:
    """Numeric columns are usually plain comma-thousands numbers, but the site
    genuinely leaves a cell blank on some old rows (e.g. round 17's alliance
    [BIG] has no "actual score" at all) -- pass those through empty rather
    than fabricating a 0, which would misrepresent missing data as a real
    value."""
    cell = cell.strip()
    return str(int(cell.replace(",", ""))) if cell else ""


def fetch_page(client: httpx.Client, page_id: int, round_number: int) -> HistoryPageParser:
    resp = client.get(HISTORY_URL, params={"id": page_id, "round": round_number})
    resp.raise_for_status()
    parser = HistoryPageParser()
    parser.feed(resp.text)
    served = HEADER_ROUND_RE.search(parser.header_text)
    if not served or int(served.group(1)) != round_number:
        got = served.group(1) if served else "unknown"
        raise SystemExit(
            f"round {round_number} id={page_id}: page header says round {got}, not "
            f"{round_number} -- the site serves round 14's data for anything out of "
            "range instead of a 404, so this round almost certainly doesn't exist"
        )
    if not parser.rows:
        raise SystemExit(f"round {round_number} id={page_id}: header matched but the table was empty")
    return parser


def discover_rounds(client: httpx.Client) -> list[int]:
    """Every round number the site's own nav links to (from the id=3 page for round 14,
    which is guaranteed to exist and carries the full round list in its nav)."""
    parser = fetch_page(client, 3, 14)
    return sorted(parser.round_links)


def write_csv(path: Path, header: list[str], rows: list[list[str]], int_cols: set[int]) -> None:
    with path.open("w", newline="") as fh:
        w = csv.writer(fh, delimiter="|", lineterminator="\n")
        w.writerow(header)
        for row in rows:
            w.writerow([_int(c) if i in int_cols else c for i, c in enumerate(row)])
    print(f"wrote {path} ({len(rows)} rows)")


def fetch_round(client: httpx.Client, round_number: int) -> None:
    planet_page = fetch_page(client, 1, round_number)
    write_csv(
        DATA_DIR / f"round{round_number}-alliance-truth.csv",
        PLANET_HEADER,
        planet_page.rows,
        int_cols={5, 6, 7, 8},
    )
    alliance_page = fetch_page(client, 3, round_number)
    write_csv(
        DATA_DIR / f"round{round_number}-alliance-rankings.csv",
        ALLIANCE_HEADER,
        alliance_page.rows,
        int_cols={2, 3, 4, 5},
    )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("rounds", type=int, nargs="*", help="explicit round numbers")
    ap.add_argument("--from", dest="lo", type=int, default=None, help="start of an inclusive range")
    ap.add_argument("--to", dest="hi", type=int, default=None, help="end of an inclusive range")
    ap.add_argument(
        "--all", action="store_true", help="fetch every round the site's own nav links to"
    )
    ap.add_argument(
        "--sleep", type=float, default=0.5, help="seconds to wait between requests (default 0.5)"
    )
    ap.add_argument(
        "--keep-going",
        action="store_true",
        help="on a round with no/broken data, warn and continue instead of stopping "
        "(use for a long historical range where some rounds are expected to be missing)",
    )
    args = ap.parse_args()

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with httpx.Client(timeout=30.0) as client:
        if args.all:
            rounds = discover_rounds(client)
            print(f"discovered {len(rounds)} rounds: {rounds[0]}-{rounds[-1]}")
        else:
            rounds = list(args.rounds)
            if args.lo is not None or args.hi is not None:
                if args.lo is None or args.hi is None:
                    raise SystemExit("--from and --to must be given together")
                rounds.extend(range(args.lo, args.hi + 1))
            if not rounds:
                raise SystemExit("give at least one round number, --from/--to a range, or --all")
            rounds = sorted(set(rounds))

        skipped = []
        for i, r in enumerate(rounds):
            if i > 0:
                time.sleep(args.sleep)
            try:
                fetch_round(client, r)
            except (SystemExit, httpx.HTTPStatusError) as e:
                if not args.keep_going:
                    raise
                print(f"round {r}: SKIPPED -- {e}")
                skipped.append(r)
    if skipped:
        print(f"\n{len(skipped)} round(s) skipped: {skipped}")


if __name__ == "__main__":
    try:
        main()
    except httpx.HTTPStatusError as e:
        sys.exit(f"HTTP error: {e}")
