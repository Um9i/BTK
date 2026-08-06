"""Parse Planetarion-style dump files (the "botfile" format) served at
https://game.planetarion.com/botfiles/.

Verified directly against a real sample tick -- each listing file looks
like:

    Content: Planetarion Galaxy Listing (sorted by x, y)
    Author: Appocomaster <appocomaster@planetarion.com>
    Version: 1.03
    Tick: 2375
    Separator: '<TAB>'
    EOF: 'EndOfPlanetarionDumpFile'
    Format: 'x  y  "galaxy name"  size  score  value  xp'

    1   1   "Dammm admins"  44581   274772328   274759141   2896
    ...
    EndOfPlanetarionDumpFile

i.e. "Field: value" header lines (single-quoted values unquoted) until a
blank line, then rows separated by the "Separator" header value until a
line matching the "EOF" header value. This mirrors Merlin's `botfile` class
in excalibur.py, minus the live-socket reading (we always parse a complete
string already read from disk/network).
"""

from dataclasses import dataclass


@dataclass
class BotFile:
    header: dict[str, str]
    lines: list[str]

    @property
    def tick(self) -> int:
        return int(self.header["Tick"])


def parse_botfile(text: str) -> BotFile:
    raw_lines = text.splitlines()

    header: dict[str, str] = {}
    i = 0
    while i < len(raw_lines) and raw_lines[i].strip():
        field, _, value = raw_lines[i].partition(": ")
        value = _unquote(value.strip(), quote="'")
        header[field] = value
        i += 1
    i += 1  # skip the blank line separating header from body

    eof = header.get("EOF") or "EndOfPlanetarionDumpFile"

    lines: list[str] = []
    while i < len(raw_lines):
        line = raw_lines[i]
        if line.strip() == eof:
            break
        if line.strip():
            lines.append(line)
        i += 1

    return BotFile(header=header, lines=lines)


def _unquote(value: str, quote: str = '"') -> str:
    if len(value) >= 2 and value[0] == quote and value[-1] == quote:
        return value[1:-1]
    return value


def parse_planet_listing(text: str) -> tuple[int, list[dict]]:
    """planet_listing.txt: id x y z "name" "ruler" race size score value xp "special" """
    bf = parse_botfile(text)
    sep = bf.header.get("Separator", "\t")
    rows = []
    for line in bf.lines:
        f = line.split(sep)
        rows.append(
            {
                "external_id": _unquote(f[0]),
                "x": int(f[1]),
                "y": int(f[2]),
                "z": int(f[3]),
                "planet_name": _unquote(f[4]),
                "ruler_name": _unquote(f[5]),
                "race": f[6],
                "size": int(f[7] or 0),
                "score": int(f[8] or 0),
                "value": int(f[9] or 0),
                "xp": int(f[10] or 0),
                "special": _unquote(f[11]),
            }
        )
    return bf.tick, rows


def parse_galaxy_listing(text: str) -> tuple[int, list[dict]]:
    """galaxy_listing.txt: x y "name" size score value xp"""
    bf = parse_botfile(text)
    sep = bf.header.get("Separator", "\t")
    rows = []
    for line in bf.lines:
        f = line.split(sep)
        rows.append(
            {
                "x": int(f[0]),
                "y": int(f[1]),
                "name": _unquote(f[2]),
                "size": int(f[3] or 0),
                "score": int(f[4] or 0),
                "value": int(f[5] or 0),
                "xp": int(f[6] or 0),
            }
        )
    return bf.tick, rows


def parse_alliance_listing(text: str) -> tuple[int, list[dict]]:
    """alliance_listing.txt: rank "name" size members counted_score points total_score total_value"""
    bf = parse_botfile(text)
    sep = bf.header.get("Separator", "\t")
    rows = []
    for line in bf.lines:
        f = line.split(sep)
        rows.append(
            {
                "rank": int(f[0]),
                "name": _unquote(f[1]),
                "size": int(f[2] or 0),
                "members": int(f[3] or 1),
                "score": int(f[4] or 0),
                "points": int(f[5] or 0),
                "total_score": int(f[6] or 0),
                "total_value": int(f[7] or 0),
            }
        )
    return bf.tick, rows


def parse_user_feed(text: str) -> tuple[int, list[dict]]:
    """user_feed.txt: tick "item type" "item text" -- content may itself contain
    the separator, so only the first two fields are split off (maxsplit=2),
    matching excalibur.py's parse_userfeed."""
    bf = parse_botfile(text)
    sep = bf.header.get("Separator", "\t")
    rows = []
    for line in bf.lines:
        tick_str, category, content = line.split(sep, 2)
        rows.append(
            {
                "tick_number": int(tick_str),
                "category": _unquote(category),
                "text": _unquote(content),
            }
        )
    return bf.tick, rows


def parse_shipstats(ships: list[dict]) -> list[dict]:
    """Normalize stats.json entries for the `ship` table.

    "-" is the dump's placeholder for "not applicable" (e.g. a ship with
    fewer than 3 targets); it maps to NULL, mirroring shipstats.py's
    add_ship (which simply skips setting the attribute when the raw value
    is "-").
    """
    rows = []
    for ship in ships:
        metal = int(_ship_val(ship, "metal") or 0)
        crystal = int(_ship_val(ship, "crystal") or 0)
        eonium = int(_ship_val(ship, "eonium") or 0)
        rows.append(
            {
                "name": ship["name"],
                "class": ship["class"],
                "race": ship["race"],
                "type": ship["type"],
                "metal": metal,
                "crystal": crystal,
                "eonium": eonium,
                "total_cost": metal + crystal + eonium,
                "damage": int(_ship_val(ship, "damage") or 0),
                "armor": int(_ship_val(ship, "armor") or 0),
                "guns": int(_ship_val(ship, "guns") or 0),
                "initiative": int(_ship_val(ship, "initiative") or 0),
                "empres": int(_ship_val(ship, "empres") or 0),
                "baseeta": int(_ship_val(ship, "baseeta") or 0),
                "target1": _ship_val(ship, "target1"),
                "target2": _ship_val(ship, "target2"),
                "target3": _ship_val(ship, "target3"),
            }
        )
    return rows


def _ship_val(ship: dict, key: str) -> str | None:
    v = ship.get(key)
    return None if v in (None, "-") else v
