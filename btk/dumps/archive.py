"""Fetch historical Planetarion dumps from the dumps.dfwtk.com archive, for
backfilling past rounds -- unlike btk/dumps/downloader.py's live fetch,
which always means "whatever tick it is right now" at a fixed URL, the
archive is laid out per round and tick:

    {BTK_ARCHIVE_BASE_URL}/r<round>/          index of available tick dirs
    {BTK_ARCHIVE_BASE_URL}/r<round>/<tick>/{planet,galaxy,alliance}_listing.txt, user_feed.txt
    {BTK_ARCHIVE_BASE_URL}/r<round>/stats.json   round-scoped ship stats

The four listing files are byte-for-byte the same botfile format as the
live game serves (verified against a real archived tick), so they parse
with the exact same btk.dumps.parser functions used for live ingest.
stats.json is likewise the same shape fetch_live_shipstats() returns (a
few extra derived fields like "armorcost" that parse_shipstats() already
ignores), so it reuses parse_shipstats() unchanged too.

Tick directories aren't contiguous (some ticks are missing from the
archive), so available ticks are discovered from the round's own index
page rather than assumed to run 1..N.
"""

import asyncio
import re

import httpx

from btk.config import get_settings
from btk.dumps.downloader import LISTING_FILES

_TICK_DIR_RE = re.compile(r'href="(\d+)/"')

# A ~1500-tick backfill makes that many sequential round trips to a
# third-party server; transient read timeouts/connection resets are
# expected, not exceptional, so retry a few times before giving up rather
# than letting one flaky request kill an otherwise-successful run.
_RETRY_ATTEMPTS = 4
_RETRY_BACKOFF_SECONDS = 2.0


async def _get_with_retry(client: httpx.AsyncClient, url: str) -> httpx.Response:
    for attempt in range(1, _RETRY_ATTEMPTS + 1):
        try:
            resp = await client.get(url)
            resp.raise_for_status()
            return resp
        except (httpx.TransportError, httpx.HTTPStatusError) as exc:
            if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code < 500:
                raise  # 404 etc. -- retrying won't help
            if attempt == _RETRY_ATTEMPTS:
                raise
            await asyncio.sleep(_RETRY_BACKOFF_SECONDS * attempt)
    raise AssertionError("unreachable")


async def list_archive_ticks(round_number: int) -> list[int]:
    """Return the sorted tick numbers available for a round, per its index page."""
    settings = get_settings()
    base = settings.archive_base_url.rstrip("/")
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await _get_with_retry(client, f"{base}/r{round_number}/")
    return sorted(int(m) for m in _TICK_DIR_RE.findall(resp.text))


async def fetch_archive_tick(
    client: httpx.AsyncClient, round_number: int, tick_number: int
) -> dict[str, str]:
    """Fetch the four dump files for one archived round/tick."""
    settings = get_settings()
    base = settings.archive_base_url.rstrip("/")
    texts = {}
    for filename in LISTING_FILES:
        resp = await _get_with_retry(client, f"{base}/r{round_number}/{tick_number}/{filename}")
        texts[filename] = resp.text
    return texts


async def fetch_archive_shipstats(round_number: int) -> list[dict]:
    """Fetch a round's archived ship stats (JSON), same shape as fetch_live_shipstats()."""
    settings = get_settings()
    base = settings.archive_base_url.rstrip("/")
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(f"{base}/r{round_number}/stats.json")
        resp.raise_for_status()
        return resp.json()
