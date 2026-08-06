"""Number formatting helpers, ported from Merlin's Core/loadable.py."""

# Minimum multiple of 1,000/1,000,000 a number must reach before num2short
# abbreviates it to "k"/"m" -- e.g. with the default of 10, 9,999 stays
# "9999" but 10,000 becomes "10k".
_THRESHOLD = 10


def num2short(num: float) -> str:
    prefix = "-" if num < 0 else ""
    num = abs(num)
    if num / (1_000_000 * _THRESHOLD) >= 1:
        value = round(num / 1_000_000, 1)
        return f"{prefix}{int(value) if value.is_integer() else value}m"
    if num / (1_000 * _THRESHOLD) >= 1:
        value = round(num / 1_000, 1)
        return f"{prefix}{int(value) if value.is_integer() else value}k"
    return f"{prefix}{round(num)}"


def short2num(short: str) -> int:
    short = short.replace(",", "")
    try:
        if short[-1].lower() == "m":
            return int(float(short[:-1]) * 1_000_000)
        if short[-1].lower() == "k":
            return int(float(short[:-1]) * 1_000)
        return int(float(short))
    except (ValueError, IndexError) as exc:
        raise ValueError(f"Could not parse number: {short!r}") from exc
