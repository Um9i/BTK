"""Tiny server-rendered SVG trend helpers for the detail pages.

No chart library and no client JS -- these build plain <svg> markup from
already-fetched tick-history rows, in keeping with btk/api/routers/web.py's
plain-server-rendered-HTML approach. Line color is deliberately left as
`currentColor` so it inherits whatever text color the surrounding CSS sets,
rather than the module needing to know the site's light/dark tokens.
"""

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
