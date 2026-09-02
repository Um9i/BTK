"""Tiny server-rendered SVG trend helpers for the detail pages, plus the JSON
feed for the bigger Chart.js panel in the new Trends tab (see app.js).

The inline sparkline stays a plain <svg> -- it's a glance-level accent inside
a vitals tile, not something worth a JS library for. The Trends tab is the
opposite job (read exact tick-by-tick values), which a hand-rolled SVG can't
give you (no hover, fixed resolution) -- that one goes to the client as raw
{tick, value} pairs and Chart.js renders it. Line color for the sparkline is
deliberately left as `currentColor` so it inherits whatever text color the
surrounding CSS sets, rather than the module needing to know the site's
light/dark tokens; the JSON feed carries no color at all, since app.js reads
the same CSS tokens itself at draw time.
"""

import json
from collections.abc import Sequence

from markupsafe import Markup


def sparkline(
    values: Sequence[float | int | None], width: int = 128, height: int = 32, pad: float = 3
) -> Markup:
    """A minimal line+area trend across `values`, given oldest to newest."""
    clean = [v for v in values if v is not None]
    if len(clean) < 2:
        return Markup("")
    lo, hi = min(clean), max(clean)
    span = (hi - lo) or 1
    n = len(clean)

    points = []
    for i, v in enumerate(clean):
        x = pad + (i / (n - 1)) * (width - 2 * pad)
        y = height - pad - ((v - lo) / span) * (height - 2 * pad)
        points.append((x, y))

    line = " ".join(f"{x:.1f},{y:.1f}" for x, y in points)
    end_x, end_y = points[-1]

    # No fill/area -- at this size a translucent polygon under the line reads as a grey
    # smear rather than a chart, so the stroke alone is the signal. preserveAspectRatio
    # is pinned to the left edge (not the "meet" default's centering) so the drawn line
    # starts flush under its vital's label instead of drifting right when the rendered
    # box's aspect ratio doesn't match the viewBox's.
    return Markup(
        f'<svg class="sparkline" viewBox="0 0 {width} {height}" preserveAspectRatio="xMinYMid meet" aria-hidden="true">'
        f'<polyline points="{line}" fill="none" stroke="currentColor" stroke-width="2" '
        f'stroke-linejoin="round" stroke-linecap="round"></polyline>'
        f'<circle cx="{end_x:.1f}" cy="{end_y:.1f}" r="2.5" fill="currentColor"></circle>'
        f"</svg>"
    )


def trend_series_json(rows: Sequence, fields: Sequence[str]) -> str:
    """{field: [{"t": tick, "v": value}, ...]} for every field, oldest tick first,
    serialized once server-side so the template just drops it into a <script
    type="application/json"> tag. Numeric only (tick numbers/stat values) --
    never carries a free-text field, so no HTML-escaping concerns embedding it
    directly in the page."""
    series: dict[str, list[dict[str, float | int]]] = {f: [] for f in fields}
    for row in rows:
        for f in fields:
            v = row[f]
            if v is not None:
                series[f].append({"t": row["tick"], "v": v})
    return json.dumps(series)


def with_deltas(rows: Sequence, fields: Sequence[str]) -> list[dict]:
    """Tag each row (newest tick first) with `<field>_delta` = this tick's value minus the
    next (chronologically previous) row's -- so the oldest row in the window has no delta."""
    rows = list(rows)
    tagged = []
    for i, row in enumerate(rows):
        item = dict(row)
        prev = rows[i + 1] if i + 1 < len(rows) else None
        for field in fields:
            item[f"{field}_delta"] = (item[field] - prev[field]) if prev is not None else None
        tagged.append(item)
    return tagged
